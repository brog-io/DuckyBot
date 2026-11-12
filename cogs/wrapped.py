"""Ducky Wrapped: a Spotify-Wrapped-style year in review for the Ente Discord.

/wrapped me      - your year as a story you tap through, ending in a shareable card
/wrapped server  - the community's year

Open to everyone from 1 December until the end of January (covering the year
that just ended). Staff (Manage Messages) can preview it any time.

Data sources: the `messages` table from MessageTracker (who posted when), the
starboard channel (who got green hearts), the solved-posts index from
ForumSimilarityBot, plus the GitHub star and Ente file counters.
"""

import asyncio
import io
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from utils.wrapped_cards import ServerWrapped, UserWrapped, render_server_slides, render_user_slides

logger = logging.getLogger(__name__)

AUTHOR_CACHE_FILE = Path(__file__).resolve().parent.parent / "wrapped_starboard_authors.json"
AVATAR_ID_RE = re.compile(r"/avatars/(\d+)/")
JUMP_RE = re.compile(r"channels/\d+/(\d+)/(\d+)")
STAR_COUNT_RE = re.compile(r"(\d+)\s*$")
SLIDE_CACHE_SECONDS = 30 * 60
STARBOARD_CACHE_SECONDS = 60 * 60
NEW_MEMBER_CACHE_SECONDS = 6 * 60 * 60


def wrapped_year(now: datetime) -> Optional[int]:
    """The year Wrapped currently covers for everyone, or None outside the window."""
    if now.month == 12:
        return now.year
    if now.month == 1:
        return now.year - 1
    return None


def to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def read_asset(asset, size: int = 256) -> Optional[bytes]:
    if asset is None:
        return None
    try:
        return await asset.replace(size=size, format="png").read()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Story view: Back / page / Next / Share
# --------------------------------------------------------------------------- #


class StoryView(discord.ui.View):
    def __init__(self, owner_id: int, slides: List[bytes], share_text: str, prefix: str):
        super().__init__(timeout=15 * 60)
        self.owner_id = owner_id
        self.slides = slides
        self.share_text = share_text
        self.prefix = prefix
        self.page = 0
        self.shared = False
        self._sync()

    def current_file(self) -> discord.File:
        return discord.File(io.BytesIO(self.slides[self.page]), filename=f"{self.prefix}-{self.page + 1}.png")

    def _sync(self):
        last = self.page == len(self.slides) - 1
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = last
        self.counter.label = f"{self.page + 1} / {len(self.slides)}"
        self.share.disabled = self.shared
        self.share.label = "Shared!" if self.shared else ("Share to channel" if last else "Share summary")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def _go(self, interaction: discord.Interaction, delta: int):
        self.page = max(0, min(len(self.slides) - 1, self.page + delta))
        self._sync()
        await interaction.response.edit_message(attachments=[self.current_file()], view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, -1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 1)

    @discord.ui.button(label="Share summary", emoji="📣", style=discord.ButtonStyle.success)
    async def share(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        summary = discord.File(io.BytesIO(self.slides[-1]), filename=f"{self.prefix}-summary.png")
        try:
            await interaction.channel.send(
                content=self.share_text, file=summary, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            await interaction.followup.send("I can't post in this channel, sorry! 🦆", ephemeral=True)
            return
        self.shared = True
        self._sync()
        await interaction.edit_original_response(view=self)


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #


class Wrapped(commands.Cog):
    wrapped = app_commands.Group(name="wrapped", description="Ducky Wrapped: the year on the Ente Discord")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._slides: Dict[tuple, Tuple[float, List[bytes]]] = {}
        self._starboard: Optional[Tuple[float, int, List[dict]]] = None
        self._star_lock = asyncio.Lock()
        self._new_members: Dict[Tuple[int, int], Tuple[float, int]] = {}
        self._authors: Dict[str, int] = self._load_authors()

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _load_authors() -> Dict[str, int]:
        try:
            return json.loads(AUTHOR_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_authors(self):
        try:
            AUTHOR_CACHE_FILE.write_text(json.dumps(self._authors), encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not save starboard author cache: {e}")

    def _cached_slides(self, key: tuple) -> Optional[List[bytes]]:
        hit = self._slides.get(key)
        if hit and time.monotonic() - hit[0] < SLIDE_CACHE_SECONDS:
            return hit[1]
        return None

    def _store_slides(self, key: tuple, slides: List[bytes]):
        now = time.monotonic()
        self._slides = {k: v for k, v in self._slides.items() if now - v[0] < SLIDE_CACHE_SECONDS}
        if len(self._slides) >= 40:  # ~2 MB each; keep memory bounded
            self._slides.pop(min(self._slides, key=lambda k: self._slides[k][0]))
        self._slides[key] = (now, slides)

    async def _rows(self, sql: str, params: tuple = ()):
        tracker = self.bot.get_cog("MessageTracker")
        if tracker is None:
            raise RuntimeError("MessageTracker cog is not loaded")
        pool = await tracker._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    @staticmethod
    def _bounds(year: int) -> Tuple[str, str]:
        return f"{year}-01-01 00:00:00", f"{year + 1}-01-01 00:00:00"

    async def _daily_and_hours(self, year: int, user_id: Optional[int] = None):
        where = "timestamp >= %s AND timestamp < %s"
        params = self._bounds(year)
        if user_id is not None:
            where += " AND user_id = %s"
            params += (user_id,)
        daily_rows = await self._rows(f"SELECT DATE(timestamp), COUNT(*) FROM messages WHERE {where} GROUP BY 1", params)
        hour_rows = await self._rows(f"SELECT HOUR(timestamp), COUNT(*) FROM messages WHERE {where} GROUP BY 1", params)
        daily = {to_date(d): int(n) for d, n in daily_rows}
        hours = [0] * 24
        for h, n in hour_rows:
            hours[int(h)] = int(n)
        return daily, hours

    async def _active_members(self, year: int) -> int:
        rows = await self._rows(
            "SELECT COUNT(DISTINCT user_id) FROM messages WHERE timestamp >= %s AND timestamp < %s",
            self._bounds(year),
        )
        return int(rows[0][0] or 0)

    def _solved_posts(self, year: int) -> List[dict]:
        cog = self.bot.get_cog("ForumSimilarityBot")
        posts = getattr(getattr(cog, "index", None), "posts", None) or {}
        out = []
        for meta in posts.values():
            try:
                if datetime.fromisoformat(meta.get("created_at", "")).year == year:
                    out.append(meta)
            except (TypeError, ValueError):
                continue
        return out

    async def _author_from_jump(self, url: str) -> Optional[int]:
        match = JUMP_RE.search(url or "")
        if not match:
            return None
        channel_id, message_id = int(match.group(1)), match.group(2)
        if message_id in self._authors:
            return self._authors[message_id] or None
        author_id = 0
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            message = await channel.fetch_message(int(message_id))
            author_id = message.author.id
        except (discord.HTTPException, AttributeError):
            pass
        self._authors[message_id] = author_id  # 0 = unknown, don't retry
        return author_id or None

    async def _starboard_entries(self, year: int) -> List[dict]:
        """Starboard posts for originals from `year`: author, hearts, date."""
        async with self._star_lock:
            if self._starboard and self._starboard[1] == year \
                    and time.monotonic() - self._starboard[0] < STARBOARD_CACHE_SECONDS:
                return self._starboard[2]
            channel_id = self.bot.config.get("starboard_channel_id")
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            entries: List[dict] = []
            if channel is not None:
                async for msg in channel.history(limit=None, after=datetime(year, 1, 1, tzinfo=timezone.utc)):
                    if msg.author.id != self.bot.user.id or not msg.embeds:
                        continue
                    embed = msg.embeds[0]
                    created = embed.timestamp or msg.created_at
                    if created.year != year:
                        continue
                    hearts, jump = 0, None
                    for row in msg.components:
                        for child in getattr(row, "children", []):
                            label, url = getattr(child, "label", None) or "", getattr(child, "url", None)
                            if url and "channels/" in url:
                                jump = url
                                found = STAR_COUNT_RE.search(label)
                                hearts = int(found.group(1)) if found else 0
                    author_id = None
                    icon = embed.author.icon_url if embed.author else None
                    found = AVATAR_ID_RE.search(icon or "")
                    if found:
                        author_id = int(found.group(1))
                    elif jump:
                        author_id = await self._author_from_jump(jump)
                    entries.append({"author_id": author_id, "hearts": hearts, "date": created.date()})
                self._save_authors()
            else:
                logger.warning("Wrapped: starboard channel not found; skipping starboard stats")
            self._starboard = (time.monotonic(), year, entries)
            return entries

    async def _count_new_members(self, guild: discord.Guild, year: int) -> int:
        key = (guild.id, year)
        hit = self._new_members.get(key)
        if hit and time.monotonic() - hit[0] < NEW_MEMBER_CACHE_SECONDS:
            return hit[1]
        count = 0
        try:  # paged REST fetch; doesn't fill the member cache (the bot runs without chunking)
            async for member in guild.fetch_members(limit=None):
                if not member.bot and member.joined_at and member.joined_at.year == year:
                    count += 1
        except discord.HTTPException as e:
            logger.warning(f"Wrapped: couldn't fetch members: {e}")
        self._new_members[key] = (time.monotonic(), count)
        return count

    async def _member_name_avatar(self, guild: Optional[discord.Guild], user_id: int) -> Tuple[str, Optional[bytes]]:
        who = guild.get_member(user_id) if guild else None
        if who is None and guild is not None:
            try:
                who = await guild.fetch_member(user_id)
            except discord.HTTPException:
                who = None
        if who is None:
            try:
                who = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return "Unknown duck", None
        return who.display_name, await read_asset(who.display_avatar, 128)

    # ---------------------------------------------------------------- build

    async def build_user(self, member: discord.abc.User, year: int) -> UserWrapped:
        daily, hours = await self._daily_and_hours(year, member.id)
        total = sum(daily.values())
        members, rank = await self._active_members(year), None
        if total:
            rows = await self._rows(
                "SELECT COUNT(*) FROM (SELECT user_id FROM messages WHERE timestamp >= %s AND timestamp < %s"
                " GROUP BY user_id HAVING COUNT(*) > %s) AS ahead",
                self._bounds(year) + (total,),
            )
            rank = int(rows[0][0]) + 1
        mine = [e for e in await self._starboard_entries(year) if e["author_id"] == member.id]
        best = max(mine, key=lambda e: e["hearts"]) if mine else None
        joined = getattr(member, "joined_at", None)
        return UserWrapped(
            year=year,
            name=member.display_name,
            avatar=await read_asset(member.display_avatar),
            daily=daily,
            hours=hours,
            rank=rank,
            members=members,
            joined=joined.date() if joined else None,
            starboard_posts=len(mine),
            hearts=sum(e["hearts"] for e in mine),
            best_post=(best["hearts"], best["date"]) if best and best["hearts"] else None,
            questions_solved=sum(1 for p in self._solved_posts(year) if p.get("author_id") == member.id),
        )

    async def build_server(self, guild: discord.Guild, year: int) -> ServerWrapped:
        daily, hours = await self._daily_and_hours(year)
        top_rows = await self._rows(
            "SELECT user_id, COUNT(*) AS c FROM messages WHERE timestamp >= %s AND timestamp < %s"
            " GROUP BY user_id ORDER BY c DESC LIMIT 5",
            self._bounds(year),
        )
        top = []
        for user_id, count in top_rows:
            name, avatar = await self._member_name_avatar(guild, int(user_id))
            top.append((name, avatar, int(count)))
        board = await self._starboard_entries(year)
        stars = getattr(self.bot.get_cog("StarCounter"), "star_count_cache", None)
        file_cog = self.bot.get_cog("FileTracker")
        files = getattr(file_cog, "data", {}).get("last_count") if file_cog else None
        return ServerWrapped(
            year=year,
            name=guild.name,
            icon=await read_asset(guild.icon),
            daily=daily,
            hours=hours,
            active_members=await self._active_members(year),
            top=top,
            new_members=await self._count_new_members(guild, year),
            solved_threads=len(self._solved_posts(year)),
            starboard_posts=len(board),
            hearts=sum(e["hearts"] for e in board),
            github_stars=stars if isinstance(stars, int) else None,
            files_protected=files if isinstance(files, int) else None,
        )

    # ------------------------------------------------------------- commands

    async def _year_or_explain(self, interaction: discord.Interaction) -> Tuple[Optional[int], bool]:
        now = datetime.now(timezone.utc)
        year = wrapped_year(now)
        if year is not None:
            return year, False
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms and perms.manage_messages:
            return now.year, True  # staff preview
        opens = int(datetime(now.year, 12, 1, tzinfo=timezone.utc).timestamp())
        await interaction.response.send_message(
            f"🦆 Ducky Wrapped isn't ready yet! It opens <t:{opens}:R>, on 1 December. Keep quacking until then.",
            ephemeral=True,
        )
        return None, False

    @wrapped.command(name="me", description="Your year on the Ente Discord, Wrapped")
    async def wrapped_me(self, interaction: discord.Interaction):
        year, preview = await self._year_or_explain(interaction)
        if year is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = ("user", interaction.user.id, year)
        slides = self._cached_slides(key)
        if slides is None:
            try:
                data = await self.build_user(interaction.user, year)
            except Exception as e:
                logger.error(f"Wrapped build failed for {interaction.user.id}: {e}", exc_info=True)
                await interaction.followup.send("Ducky tripped while wrapping your year. Try again in a bit! 🦆",
                                                ephemeral=True)
                return
            if data.total == 0:
                await interaction.followup.send(
                    f"🦆 I couldn't find any messages from you in {year}, so there's nothing to wrap yet. "
                    "Say hi in the server and you'll have a Wrapped next year!", ephemeral=True)
                return
            slides = await asyncio.to_thread(render_user_slides, data)
            self._store_slides(key, slides)
        view = StoryView(interaction.user.id, slides,
                         f"🦆 {interaction.user.mention}'s **Ducky Wrapped {year}**", f"wrapped-{year}")
        note = "👀 *Staff preview: everyone else can open Wrapped from 1 December.*\n" if preview else ""
        await interaction.followup.send(
            f"{note}🎁 **Your {year}, Wrapped.** Tap ▶️ to flip through, then share your summary if you like it.",
            file=view.current_file(), view=view, ephemeral=True,
        )

    @wrapped.command(name="server", description="The Ente community's year, Wrapped")
    async def wrapped_server(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This only works inside the server.", ephemeral=True)
            return
        year, preview = await self._year_or_explain(interaction)
        if year is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = ("server", interaction.guild.id, year)
        slides = self._cached_slides(key)
        if slides is None:
            try:
                data = await self.build_server(interaction.guild, year)
            except Exception as e:
                logger.error(f"Server Wrapped build failed: {e}", exc_info=True)
                await interaction.followup.send("Ducky tripped while wrapping the year. Try again in a bit! 🦆",
                                                ephemeral=True)
                return
            slides = await asyncio.to_thread(render_server_slides, data)
            self._store_slides(key, slides)
        view = StoryView(interaction.user.id, slides,
                         f"🦆 **The Ente community's Ducky Wrapped {year}**", f"ente-wrapped-{year}")
        note = "👀 *Staff preview: everyone else can open Wrapped from 1 December.*\n" if preview else ""
        await interaction.followup.send(
            f"{note}🎁 **The Ente community's {year}, Wrapped.** Tap ▶️ to flip through.",
            file=view.current_file(), view=view, ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Wrapped(bot))
