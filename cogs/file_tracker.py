import asyncio
import copy
import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import discord
from aiohttp import ClientTimeout
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View

from utils.cards import render_milestone_card, render_stats_card

logger = logging.getLogger(__name__)

HOUR = 3_600
DAY = 86_400
ENTE_YELLOW = 0xFFCD3F
REFRESH_COOLDOWN = 30  # seconds, per user (slash command and button share it)
COUNT_CACHE_SECONDS = 60  # reuse a fresh API result instead of hammering the API
SAMPLE_INTERVAL = HOUR  # how often a point is stored in the history
HISTORY_DAYS = 45  # enough for 30-day stats and week-over-week trends
DATA_FILENAME = "ente_counts.json"
CARD_FILENAME = "ente-files.png"
# #ente-stats, where Ducky announces milestones. Override with "milestone_channel_id" in config.json.
DEFAULT_MILESTONE_CHANNEL_ID = 1531001301557575741
ANNOUNCE_MAX_GAP = 6 * 3_600  # don't announce if the previous check is older (bot was offline)


# Milestone schedule, kept identical to the n8n "Ente File Count Tracker" workflow
# (TIERS in its Compute Growth node): (tier end, step) pairs. Change both together.
MILESTONE_TIERS = [
    (100_000_000, 10_000_000),  # every 10M up to 100M
    (1_000_000_000, 50_000_000),  # every 50M up to 1B
    (2_000_000_000, 100_000_000),  # every 100M up to 2B
    (5_000_000_000, 250_000_000),  # every 250M up to 5B
    (50_000_000_000, 500_000_000),  # every 500M after that
]


def build_milestones() -> List[int]:
    milestones, tier_start = [], 0
    for tier_end, step in MILESTONE_TIERS:
        milestones.extend(range(tier_start + step, tier_end + 1, step))
        tier_start = tier_end
    return milestones


# --------------------------------------------------------------------------- #
# Formatting / stats helpers (pure functions, no Discord state)
# --------------------------------------------------------------------------- #


def compact(n: float) -> str:
    """1_234_567 -> '1.23M'."""
    sign = "-" if n < 0 else ""
    n = abs(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if round(n / div, 2) >= 1:
            return f"{sign}{n / div:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{sign}{n:,.0f}"


def progress_bar(fraction: float, width: int = 14) -> str:
    fraction = min(max(fraction, 0.0), 1.0)
    filled = round(fraction * width)
    return "▰" * filled + "▱" * (width - filled)


def sample_at_or_before(history: List[dict], ts: float) -> Optional[dict]:
    """Latest history entry with timestamp <= ts (history is sorted)."""
    best = None
    for entry in history:
        if entry["timestamp"] <= ts:
            best = entry
        else:
            break
    return best


def window_change(
    history: List[dict], now_ts: float, current: int, seconds: int
) -> Optional[Tuple[int, float, float]]:
    """(diff, percent, actual_span_seconds) vs. the count `seconds` ago.

    Returns None when there is no history point close enough to that moment,
    so we never present a 3-week-old comparison as "last 24h".
    """
    target = now_ts - seconds
    base = sample_at_or_before(history, target)
    if base is None or target - base["timestamp"] > seconds * 0.25:
        return None
    span = now_ts - base["timestamp"]
    if span <= 0:
        return None
    diff = current - base["count"]
    pct = (diff / base["count"] * 100) if base["count"] else 0.0
    return diff, pct, span


def rate_between(
    history: List[dict], start_ts: float, end_ts: float, tolerance: float
) -> Optional[float]:
    """Files per day between two moments in the history."""
    a = sample_at_or_before(history, start_ts)
    b = sample_at_or_before(history, end_ts)
    if a is None or b is None or b["timestamp"] <= a["timestamp"]:
        return None
    if start_ts - a["timestamp"] > tolerance or end_ts - b["timestamp"] > tolerance:
        return None
    return (b["count"] - a["count"]) / (b["timestamp"] - a["timestamp"]) * DAY


def fmt_duration(seconds: float) -> str:
    days, rest = divmod(int(seconds), 86_400)
    hours = rest // 3_600
    if days >= 2:
        return f"{days} days"
    if days == 1:
        return f"1 day {hours}h"
    return f"{max(hours, 1)}h"


def fmt_change(change: Optional[Tuple[int, float, float]]) -> str:
    if change is None:
        return "*collecting data…*"
    diff, pct, _ = change
    arrow = "▲" if diff > 0 else "▼" if diff < 0 else "▬"
    return f"{arrow} **{diff:+,}**\n`{pct:+.2f}%`"


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


class PersistentView(View):
    def __init__(self):
        super().__init__(timeout=None)


class RefreshButton(Button):
    def __init__(self):
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            custom_id="refresh_count",  # unchanged so old messages keep working
            emoji=discord.PartialEmoji.from_str("<:refresh:1439928076195860613>"),
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FileTracker")
        if cog:
            await cog.handle_refresh(interaction)


def make_view() -> PersistentView:
    view = PersistentView()
    view.add_item(RefreshButton())
    return view


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #


class FileTracker(commands.Cog):
    API_URL = "https://api.ente.com/files/count"
    API_TIMEOUT = ClientTimeout(total=10)
    MAX_RETRIES = 3
    RETRY_DELAY = 1
    MILESTONES = build_milestones()
    DEFAULT_DATA = {
        "last_count": None,
        "last_update": None,
        "historical_counts": [],
        "achieved_milestones": [],
        "milestone_watch": None,  # last count the milestone checker saw
    }

    def __init__(self, bot):
        self.bot = bot
        self.last_count: Optional[int] = None  # last count shown in the channel name
        self.last_channel_edit = datetime.now(timezone.utc) - timedelta(hours=1)
        self.minimum_edit_interval = timedelta(minutes=5)
        self.cooldowns: TTLCache = TTLCache(maxsize=10_000, ttl=REFRESH_COOLDOWN)
        self._fetch_lock = asyncio.Lock()
        self._cached_count: Optional[int] = None
        self._cached_at = 0.0
        self._card_cache: Optional[Tuple[tuple, bytes]] = None
        self.milestone_channel_id = int(
            bot.config.get("milestone_channel_id", DEFAULT_MILESTONE_CHANNEL_ID)
        )
        self.data_file = self._resolve_data_path()
        self.load_data()
        self.monitor_files.start()

    # ---------------------------------------------------------------- storage

    @staticmethod
    def _resolve_data_path() -> Path:
        """Repo root, independent of the working directory.

        Falls back to the old cwd-relative file if that's where the live data is.
        """
        anchored = Path(__file__).resolve().parent.parent / DATA_FILENAME
        legacy = Path.cwd() / DATA_FILENAME
        if not anchored.exists() and legacy.exists():
            return legacy
        return anchored

    def load_data(self):
        data = {}
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                backup = self.data_file.with_suffix(".corrupt.json")
                logger.error(f"Could not read {self.data_file} ({e}); moved to {backup}")
                try:
                    os.replace(self.data_file, backup)
                except OSError:
                    pass
                data = {}
        for key, value in self.DEFAULT_DATA.items():
            data.setdefault(key, copy.deepcopy(value))
        data["historical_counts"] = sorted(
            (
                e
                for e in data["historical_counts"]
                if isinstance(e, dict)
                and isinstance(e.get("timestamp"), (int, float))
                and isinstance(e.get("count"), int)
            ),
            key=lambda e: e["timestamp"],
        )
        self.data = data

    def save_data(self):
        tmp = self.data_file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.data_file)  # atomic: never leaves a half-written file
        except OSError as e:
            logger.error(f"Failed to save {self.data_file}: {e}")

    def record_sample(self, count: int, now_ts: int):
        """Store at most one history point per SAMPLE_INTERVAL."""
        self.data["last_count"] = count
        self.data["last_update"] = now_ts
        history = self.data["historical_counts"]
        if history and now_ts - history[-1]["timestamp"] < SAMPLE_INTERVAL:
            return

        history.append({"timestamp": now_ts, "count": count})
        cutoff = now_ts - HISTORY_DAYS * DAY
        self.data["historical_counts"] = [e for e in history if e["timestamp"] > cutoff]
        self.save_data()

    # -------------------------------------------------------------------- API

    async def fetch_file_count(self) -> Optional[int]:
        for attempt in range(self.MAX_RETRIES):
            try:
                async with self.bot.http_session.get(
                    self.API_URL, timeout=self.API_TIMEOUT
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        count = data.get("count")
                        if isinstance(count, int) and count >= 0:
                            return count
                        logger.error(f"Unexpected API payload: {data!r}")
                        return None
                    if response.status == 429:
                        retry_after = min(
                            float(response.headers.get("Retry-After", self.RETRY_DELAY)),
                            30,
                        )
                        logger.warning(f"Rate limited by API. Waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    logger.error(f"API returned status code: {response.status}")
            except asyncio.TimeoutError:
                logger.warning(
                    f"API request timed out (attempt {attempt + 1}/{self.MAX_RETRIES})"
                )
            except Exception as e:
                logger.error(
                    f"Error fetching file count (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}",
                    exc_info=True,
                )
            if attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def get_count(self) -> Optional[int]:
        """Fetch the count, sharing one recent result between concurrent callers."""
        async with self._fetch_lock:
            if (
                self._cached_count is not None
                and time.monotonic() - self._cached_at < COUNT_CACHE_SECONDS
            ):
                return self._cached_count
            count = await self.fetch_file_count()
            if count is not None:
                self._cached_count = count
                self._cached_at = time.monotonic()
            return count

    # ------------------------------------------------------------ background

    def cog_unload(self):
        self.monitor_files.cancel()

    async def safe_channel_edit(self, channel, new_name):
        since_last = datetime.now(timezone.utc) - self.last_channel_edit
        if since_last < self.minimum_edit_interval:
            wait_time = (self.minimum_edit_interval - since_last).total_seconds()
            logger.info(f"Waiting {wait_time:.2f}s before next channel edit")
            await asyncio.sleep(wait_time)
        try:
            await channel.edit(name=new_name)
        except discord.HTTPException as e:
            if e.status != 429:
                raise
            retry_after = getattr(e, "retry_after", None) or 600
            logger.warning(f"Rate limited on channel edit. Retry after: {retry_after}s")
            if retry_after > self.minimum_edit_interval.total_seconds():
                self.minimum_edit_interval = timedelta(seconds=retry_after * 1.1)
            await asyncio.sleep(retry_after)
            await channel.edit(name=new_name)
        self.last_channel_edit = datetime.now(timezone.utc)

    @tasks.loop(seconds=300)
    async def monitor_files(self):
        try:
            current_count = await self.get_count()
            if current_count is None:
                return
            now = datetime.now(timezone.utc)
            await self.check_milestones(current_count, now)
            self.record_sample(current_count, int(now.timestamp()))

            if current_count == self.last_count:
                return
            channel = self.bot.get_channel(int(self.bot.config["channel_id"]))
            if channel:
                await self.safe_channel_edit(channel, f"📷 {current_count:,} Files")
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.CustomActivity(name=f"Securing {current_count:,} files"),
            )
            self.last_count = current_count
        except Exception as e:
            logger.error(f"Error in file monitoring: {e}", exc_info=True)

    @monitor_files.before_loop
    async def before_monitor_files(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------- embed

    def daily_rate(self, current: int, now_ts: int) -> Optional[float]:
        history = self.data["historical_counts"]
        for seconds in (7 * DAY, DAY, 30 * DAY):
            change = window_change(history, now_ts, current, seconds)
            if change:
                return change[0] / change[2] * DAY
        return None

    def weekly_trend(self, now_ts: int) -> Optional[float]:
        """% change of this week's growth speed vs. the week before."""
        history = self.data["historical_counts"]
        this_week = rate_between(history, now_ts - 7 * DAY, now_ts, 12 * HOUR)
        last_week = rate_between(history, now_ts - 14 * DAY, now_ts - 7 * DAY, 12 * HOUR)
        if this_week is None or not last_week or last_week <= 0:
            return None
        return (this_week - last_week) / last_week * 100

    def card_kwargs(self, current: int, now: datetime) -> dict:
        """Snapshot everything the stats card needs (taken on the event loop)."""
        history = self.data["historical_counts"]
        now_ts = int(now.timestamp())
        points = [(e["timestamp"], e["count"]) for e in history if e["timestamp"] >= now_ts - 30 * DAY]
        points.append((now_ts, current))
        week = window_change(history, now_ts, current, 7 * DAY)
        return {
            "count": current,
            "points": points,
            "updated_at": now,
            "per_day": self.daily_rate(current, now_ts),
            "week_diff": week[0] if week else None,
            "trend_pct": self.weekly_trend(now_ts),
            "next_milestone": next((m for m in self.MILESTONES if m > current), None),
            "prev_milestone": max((m for m in self.MILESTONES if m <= current), default=0),
        }

    async def get_card(self, current: int, now: datetime) -> Optional[bytes]:
        history = self.data["historical_counts"]
        key = (current, history[-1]["timestamp"] if history else None)
        if self._card_cache and self._card_cache[0] == key:
            return self._card_cache[1]
        try:
            png = await asyncio.to_thread(render_stats_card, **self.card_kwargs(current, now))
        except Exception as e:
            logger.error(f"Failed to render stats card: {e}", exc_info=True)
            return None
        self._card_cache = (key, png)
        return png

    def build_embed(self, current: int, now: datetime, has_card: bool = False) -> discord.Embed:
        history = self.data["historical_counts"]
        now_ts = int(now.timestamp())

        embed = discord.Embed(
            description=f"# 🔐 {current:,}\n-# files end-to-end encrypted with Ente",
            color=ENTE_YELLOW,
            timestamp=now,
        )
        avatar = self.bot.user.display_avatar.url if self.bot.user else None
        embed.set_author(name="Ente · Protected Files", icon_url=avatar)

        # Row 1: growth over fixed windows
        for label, seconds in (("Last 24h", DAY), ("Last 7 days", 7 * DAY),
                               ("Last 30 days", 30 * DAY)):
            embed.add_field(
                name=label,
                value=fmt_change(window_change(history, now_ts, current, seconds)),
                inline=True,
            )

        # Row 2: speed, trend, latest milestone
        rate = self.daily_rate(current, now_ts)
        if rate is not None:
            per_minute = rate / 1440
            speed = f"**{compact(rate)}**/day\n`~{per_minute:,.1f}/min`"
        else:
            speed = "*collecting data…*"
        embed.add_field(name="⚡ Speed", value=speed, inline=True)

        delta = self.weekly_trend(now_ts)
        if delta is not None:
            icon = "📈" if delta >= 0 else "📉"
            word = "faster" if delta >= 0 else "slower"
            trend = f"{icon} **{abs(delta):.1f}%** {word}\n*than the week before*"
        else:
            trend = "*collecting data…*"
        embed.add_field(name="📊 Trend", value=trend, inline=True)

        achieved = [m for m in self.data["achieved_milestones"] if isinstance(m, dict)]
        if achieved:
            latest = max(achieved, key=lambda m: m["timestamp"])
            party = "🎉 " if now_ts - latest["timestamp"] < 7 * DAY else ""
            last_ms = f"{party}**{compact(latest['milestone'])}**\n<t:{latest['timestamp']}:R>"
        else:
            passed = max((m for m in self.MILESTONES if m <= current), default=None)
            last_ms = f"**{compact(passed)}**" if passed else "*none yet*"
        embed.add_field(name="🏆 Last Milestone", value=last_ms, inline=True)

        # Row 3: next milestone with progress bar + ETA
        next_ms = next((m for m in self.MILESTONES if m > current), None)
        if next_ms is None:
            embed.add_field(name="🎯 Next Milestone", value="Every milestone reached! 🦆🎉",
                            inline=False)
        else:
            prev_ms = max((m for m in self.MILESTONES if m <= current), default=0)
            fraction = (current - prev_ms) / (next_ms - prev_ms)
            lines = [
                f"`{progress_bar(fraction)}` **{fraction * 100:.1f}%**",
                f"**{next_ms - current:,}** files to go",
            ]
            if rate and rate > 0:
                eta = now + timedelta(days=(next_ms - current) / rate)
                eta_ts = int(eta.timestamp())
                lines[-1] += f" · ETA <t:{eta_ts}:R> (<t:{eta_ts}:D>)"
            embed.add_field(name=f"🎯 Next Milestone — {next_ms:,}", value="\n".join(lines),
                            inline=False)

        embed.set_footer(text="Ducky · data from api.ente.com")
        return embed

    # -------------------------------------------------------------- commands

    @app_commands.command(
        name="files", description="Get the current number of files protected by Ente"
    )
    async def files(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    async def handle_refresh(self, interaction: discord.Interaction):
        is_button = interaction.type == discord.InteractionType.component

        last = self.cooldowns.get(interaction.user.id)
        if last is not None:
            ready_at = int(last + REFRESH_COOLDOWN)
            try:
                await interaction.response.send_message(
                    f"🦆 *Quack!* I need to catch my breath! Try again <t:{ready_at}:R>. 🕒",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
            return

        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("Interaction expired before it could be deferred")
            return
        self.cooldowns[interaction.user.id] = time.time()

        try:
            current = await self.get_count()
            if current is None:
                msg = "⚠️ Couldn't reach the Ente API right now. Please try again later."
                if is_button:
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.edit_original_response(content=msg)
                return

            now = datetime.now(timezone.utc)
            self.record_sample(current, int(now.timestamp()))
            card = await self.get_card(current, now)
            embed = self.build_embed(current, now, has_card=card is not None)
            files = []
            if card:
                files = [discord.File(io.BytesIO(card), filename=CARD_FILENAME)]
                embed.set_image(url=f"attachment://{CARD_FILENAME}")
            await interaction.edit_original_response(
                content=None, embed=embed, view=make_view(), attachments=files
            )
        except discord.NotFound:
            logger.warning("Interaction expired before response could be completed")
        except Exception as e:
            logger.error(f"Error in handle_refresh: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    "An error occurred while processing the request. Please try again later.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------ milestones

    def milestone_reached_at(self, milestone: int) -> Optional[int]:
        for entry in self.data["achieved_milestones"]:
            if isinstance(entry, dict) and entry.get("milestone") == milestone:
                return entry["timestamp"]
        history = self.data["historical_counts"]
        for before, after in zip(history, history[1:]):  # crossing inside our history
            if before["count"] < milestone <= after["count"]:
                return after["timestamp"]
        return None

    async def check_milestones(self, count: int, now: datetime):
        """Called on every background poll; announces newly crossed milestones."""
        now_ts = int(now.timestamp())
        watch = self.data.get("milestone_watch")
        self.data["milestone_watch"] = {"count": count, "timestamp": now_ts}
        if not watch:  # first run: just start watching
            self.save_data()
            return
        crossed = [m for m in self.MILESTONES if watch["count"] < m <= count]
        if not crossed:
            return
        achieved = {e.get("milestone") for e in self.data["achieved_milestones"] if isinstance(e, dict)}
        new = [m for m in crossed if m not in achieved]
        for m in new:
            self.data["achieved_milestones"].append({"milestone": m, "timestamp": now_ts})
            logger.info(f"Ente passed {m:,} files")
        self.save_data()
        if not new:
            return
        if now_ts - watch["timestamp"] > ANNOUNCE_MAX_GAP:
            logger.info(f"Not announcing {max(new):,}: last check was too long ago")
            return
        await self.announce_milestone(max(new), count, now)

    async def build_milestone_message(
        self, milestone: int, count: int, now: datetime
    ) -> Tuple[str, discord.Embed, Optional[discord.File]]:
        now_ts = int(now.timestamp())
        prev_ms = max((m for m in self.MILESTONES if m < milestone), default=0)
        prev_ts = self.milestone_reached_at(prev_ms) if prev_ms else None
        since = fmt_duration(now_ts - prev_ts) if prev_ts and prev_ts < now_ts else None
        billion = milestone % 1_000_000_000 == 0
        name = f"{milestone // 1_000_000_000} BILLION" if billion else f"{milestone:,}"

        embed = discord.Embed(
            title=f"🚀🎉 Ente just passed {name} files! 🎉🚀" if billion
            else f"🎉 Ente just passed {name} files!",
            description=f"🔐 **{count:,}** files, all end-to-end encrypted.",
            color=ENTE_YELLOW,
            timestamp=now,
        )
        if since:
            embed.add_field(name=f"⏱️ Since {compact(prev_ms)}", value=since, inline=True)
        rate = self.daily_rate(count, now_ts)
        if rate:
            embed.add_field(name="⚡ Speed", value=f"**{compact(rate)}**/day", inline=True)
        next_ms = next((m for m in self.MILESTONES if m > count), None)
        if next_ms:
            value = f"{next_ms:,}"
            if rate and rate > 0:
                eta = int(now_ts + (next_ms - count) / rate * DAY)
                value = f"<t:{eta}:R>\n<t:{eta}:D>"
            embed.add_field(name=f"🎯 Next: {compact(next_ms)}", value=value, inline=True)
        embed.set_footer(text="Ducky · data from api.ente.com")

        file = None
        try:
            card_since = f"{since} after {compact(prev_ms)}" if since else None
            png = await asyncio.to_thread(render_milestone_card, milestone, now, card_since)
            filename = f"ente-{milestone}-files.png"
            file = discord.File(io.BytesIO(png), filename=filename)
            embed.set_image(url=f"attachment://{filename}")
        except Exception as e:
            logger.error(f"Failed to render milestone card: {e}", exc_info=True)

        content = f"🚀 **Ente just passed {name} files!**" if billion \
            else f"🎉 Ente passed **{compact(milestone)}** files"
        return content, embed, file

    async def announce_milestone(self, milestone: int, count: int, now: datetime):
        channel = self.bot.get_channel(self.milestone_channel_id)
        if channel is None:
            logger.warning(f"Milestone channel {self.milestone_channel_id} not found")
            return
        content, embed, file = await self.build_milestone_message(milestone, count, now)
        try:
            if file:
                await channel.send(content=content, embed=embed, file=file)
            else:
                await channel.send(content=content, embed=embed)
        except discord.HTTPException as e:
            logger.error(f"Failed to announce milestone {milestone:,}: {e}", exc_info=True)

    @app_commands.command(
        name="milestonecard", description="Preview the announcement for an Ente milestone"
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(milestone="Milestone to preview, e.g. 1000000000 (default: the next one)")
    async def milestonecard(
        self,
        interaction: discord.Interaction,
        milestone: Optional[app_commands.Range[int, 1, 1_000_000_000_000]] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if milestone is None:
            base = self._cached_count or self.data.get("last_count") or 0
            milestone = next((m for m in self.MILESTONES if m > base), self.MILESTONES[-1])
        count = max(self._cached_count or self.data.get("last_count") or milestone, milestone)
        content, embed, file = await self.build_milestone_message(
            milestone, count, datetime.now(timezone.utc)
        )
        kwargs = {"embed": embed, "ephemeral": True}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(f"**Preview** · {content}", **kwargs)

async def setup(bot):
    await bot.add_cog(FileTracker(bot))
