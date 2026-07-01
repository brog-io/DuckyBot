from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from aiohttp import ClientTimeout
import json
import os

logger = logging.getLogger(__name__)


class PersistentView(View):
    def __init__(self):
        super().__init__(timeout=None)


class RefreshButton(Button):
    def __init__(self):
        super().__init__(
            label="Refresh Count",
            style=discord.ButtonStyle.primary,
            custom_id="refresh_paid",
            emoji=":refresh:1439928076195860613",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("PaidAccounts")
        if cog:
            await cog.handle_refresh(interaction)


class PaidAccounts(commands.Cog):
    API_URL = "https://ente.com/api/open-data"
    API_TIMEOUT = ClientTimeout(total=10)
    MAX_RETRIES = 3
    RETRY_DELAY = 1
    MILESTONES = [
        20_000,
        25_000,
        30_000,
        40_000,
        50_000,
        75_000,
        100_000,
        150_000,
        200_000,
        250_000,
        300_000,
        400_000,
        500_000,
        750_000,
        1_000_000,
    ]

    def __init__(self, bot):
        self.bot = bot
        self.last_count: Optional[int] = None
        self.last_channel_edit: datetime = datetime.now(timezone.utc)
        self.minimum_edit_interval: timedelta = timedelta(minutes=5)
        self.button_cooldowns = {}
        self.data_file = "paid_accounts.json"
        self.default_data = {
            "last_count": None,
            "last_update": None,
            "historical_counts": [],
        }
        self.load_data()
        self.monitor_paid.start()

    def load_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.data = json.load(f)
        else:
            self.data = self.default_data

    def save_data(self):
        with open(self.data_file, "w") as f:
            json.dump(self.data, f)

    def cog_unload(self):
        self.monitor_paid.cancel()

    async def safe_channel_edit(self, channel, new_name):
        now = datetime.now(timezone.utc)
        time_since_last_edit = now - self.last_channel_edit
        if time_since_last_edit < self.minimum_edit_interval:
            wait_time = (
                self.minimum_edit_interval - time_since_last_edit
            ).total_seconds()
            logger.info(f"Waiting {wait_time:.2f}s before next channel edit")
            await asyncio.sleep(wait_time)
        try:
            await channel.edit(name=new_name)
            self.last_channel_edit = datetime.now(timezone.utc)
        except discord.HTTPException as e:
            if e.code == 429:
                retry_after = e.retry_after
                logger.warning(
                    f"Rate limited on channel edit. Retry after: {retry_after}s"
                )
                if retry_after > self.minimum_edit_interval.total_seconds():
                    self.minimum_edit_interval = timedelta(seconds=retry_after * 1.1)
                await asyncio.sleep(retry_after)
                await channel.edit(name=new_name)
                self.last_channel_edit = datetime.now(timezone.utc)
            else:
                raise

    async def fetch_open_data(self) -> Optional[list]:
        """Fetch and return the full open-data history, sorted oldest -> newest."""
        for attempt in range(self.MAX_RETRIES):
            try:
                async with self.bot.http_session.get(
                    self.API_URL, timeout=self.API_TIMEOUT
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        entries = payload.get("data", [])
                        entries.sort(key=lambda e: e["metric_date"])
                        return entries
                    elif response.status == 429:
                        retry_after = float(
                            response.headers.get("Retry-After", self.RETRY_DELAY)
                        )
                        logger.warning(f"Rate limited by API. Waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                    else:
                        logger.error(f"API returned status code: {response.status}")
                        await asyncio.sleep(self.RETRY_DELAY)
            except asyncio.TimeoutError:
                logger.warning(
                    f"API request timed out (attempt {attempt + 1}/{self.MAX_RETRIES})"
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
            except Exception as e:
                logger.error(
                    f"Error fetching open data (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}",
                    exc_info=True,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
        return None

    @staticmethod
    def _timestamp(metric_date: str) -> int:
        return int(
            datetime.strptime(metric_date, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )

    def predict_milestone(self, target: int) -> Tuple[Optional[datetime], bool]:
        history = self.data["historical_counts"]
        if len(history) < 2:
            return None, False
        oldest = history[0]
        newest = history[-1]
        time_diff = newest["timestamp"] - oldest["timestamp"]
        count_diff = newest["count"] - oldest["count"]
        if time_diff <= 0:
            return None, False
        daily_rate = (count_diff / time_diff) * 86400
        remaining_count = target - newest["count"]
        if remaining_count <= 0:
            return None, True
        if daily_rate <= 0:
            return None, False
        days_until = remaining_count / daily_rate
        predicted_date = datetime.now(timezone.utc) + timedelta(days=days_until)
        return predicted_date, False

    @tasks.loop(seconds=300)
    async def monitor_paid(self):
        try:
            channel = self.bot.get_channel(int(self.bot.config["paid_channel_id"]))
            if not channel:
                return
            entries = await self.fetch_open_data()
            if not entries:
                return
            current_count = entries[-1]["paid_accounts"]
            if current_count != self.last_count:
                await self.safe_channel_edit(channel, f"💳 {current_count:,} Paid")
                self.last_count = current_count
                logger.info(
                    f"Updated channel name to {current_count:,} paid accounts. "
                    f"Minimum edit interval: {self.minimum_edit_interval.total_seconds()}s"
                )
        except Exception as e:
            logger.error(f"Error in paid accounts monitoring: {e}", exc_info=True)

    @monitor_paid.before_loop
    async def before_monitor_paid(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="paid", description="Get the current number of paid Ente subscriptions"
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def paid(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    @paid.error
    async def paid_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"🦆 *Quack!* I need to catch my breath! Try again in {error.retry_after:.1f} seconds! 🕒",
                ephemeral=True,
            )

    async def handle_refresh(self, interaction: discord.Interaction):
        try:
            user_id = interaction.user.id
            current_time = datetime.now(timezone.utc)

            if user_id in self.button_cooldowns:
                time_diff = (
                    current_time - self.button_cooldowns[user_id]
                ).total_seconds()
                if time_diff < 30:
                    try:
                        await interaction.response.send_message(
                            f"Please wait {30 - int(time_diff)} seconds before refreshing again.",
                            ephemeral=True,
                        )
                    except discord.NotFound:
                        logger.warning("Interaction expired during cooldown check")
                    return

            try:
                await interaction.response.defer()
            except discord.NotFound:
                logger.warning(
                    "Interaction token expired or invalid - button may be from old message"
                )
                return

            self.button_cooldowns[user_id] = current_time
            entries = await self.fetch_open_data()

            if entries:
                # Refresh local history cache with the latest data from the API
                self.data["historical_counts"] = [
                    {
                        "timestamp": self._timestamp(e["metric_date"]),
                        "count": e["paid_accounts"],
                    }
                    for e in entries
                ]

                current = entries[-1]
                current_count = current["paid_accounts"]
                current_mrr = current.get("mrr")

                change_text = ""
                if len(entries) >= 2:
                    previous_count = entries[-2]["paid_accounts"]
                    change = current_count - previous_count
                    percent = (
                        (change / previous_count) * 100 if previous_count else 0
                    )
                    arrow = "↑" if change >= 0 else "↓"
                    change_text = f"**{change:+,}** ({percent:+.2f}%)"
                else:
                    arrow = "↑"

                avg_monthly_text = ""
                if len(entries) >= 2:
                    oldest = entries[0]
                    days_diff = (
                        self._timestamp(current["metric_date"])
                        - self._timestamp(oldest["metric_date"])
                    ) / 86400
                    months_diff = days_diff / 30.44
                    if months_diff >= 1:
                        total_increase = current_count - oldest["paid_accounts"]
                        avg_monthly = total_increase / months_diff
                        avg_monthly_text = f"**{avg_monthly:+,.0f}** paid/month"

                paid_embed = discord.Embed(
                    title="🦆 Ente Paid Subscriptions", color=0x1DB954
                )
                paid_embed.add_field(
                    name="Paid Accounts",
                    value=f"💳 **{current_count:,}**",
                    inline=False,
                )
                if change_text:
                    paid_embed.add_field(
                        name=f"{arrow} Since Last Month", value=change_text, inline=True
                    )
                paid_embed.add_field(
                    name="📈 Avg Monthly Growth",
                    value=avg_monthly_text or "N/A",
                    inline=True,
                )
                if current_mrr is not None:
                    paid_embed.add_field(
                        name="💰 MRR",
                        value=f"**${current_mrr:,.0f}**",
                        inline=True,
                    )

                milestone_text = ""
                next_milestone = next(
                    (m for m in self.MILESTONES if m > current_count), None
                )
                if next_milestone:
                    predicted_date, already_achieved = self.predict_milestone(
                        next_milestone
                    )
                    if predicted_date and not already_achieved:
                        predicted_timestamp = int(predicted_date.timestamp())
                        days_until = (predicted_date - current_time).days
                        milestone_text = (
                            f"**{next_milestone:,}** paid accounts\n"
                            f"~{int(days_until)} days (<t:{predicted_timestamp}:D>)"
                        )
                paid_embed.add_field(
                    name="🎯 Next Milestone",
                    value=milestone_text or "N/A",
                    inline=False,
                )

                current_timestamp = int(current_time.timestamp())
                paid_embed.add_field(
                    name="Last Updated",
                    value=f"<t:{current_timestamp}:R>",
                    inline=False,
                )

                self.data["last_count"] = current_count
                self.data["last_update"] = current_timestamp
                self.save_data()

                view = PersistentView()
                view.add_item(RefreshButton())

                await interaction.edit_original_response(embed=paid_embed, view=view)
            else:
                await interaction.edit_original_response(
                    content="Failed to fetch the current paid account count. Please try again later."
                )
        except discord.NotFound:
            logger.warning("Interaction expired before response could be completed")
        except Exception as e:
            logger.error(f"Error in handle_refresh: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(
                    content="An error occurred while processing the request. Please try again later."
                )
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(PaidAccounts(bot))
