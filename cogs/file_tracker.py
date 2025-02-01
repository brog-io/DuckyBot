from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple
from aiohttp import ClientTimeout
import json
import os
import matplotlib.pyplot as plt
import io
import numpy as np

logger = logging.getLogger(__name__)


class PersistentView(View):
    def __init__(self):
        super().__init__(timeout=None)


class RefreshButton(Button):  # Move this outside FileTracker class
    def __init__(self):
        super().__init__(
            label="Refresh Count",
            style=discord.ButtonStyle.primary,
            custom_id="refresh_count",
            emoji=":refresh:1311347827795951637",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FileTracker")
        if cog:
            await cog.handle_refresh(interaction)


class FileTracker(commands.Cog):
    # Constants
    API_URL = "https://api.ente.io/files/count"
    API_TIMEOUT = ClientTimeout(total=10)  # 10 seconds timeout
    MAX_RETRIES = 3
    RETRY_DELAY = 1  # seconds
    MILESTONES = [
        10_000,
        25_000,
        50_000,
        75_000,
        100_000,
        150_000,
        200_000,
        250_000,
        300_000,
        400_000,
        500_000,
        600_000,
        700_000,
        750_000,
        800_000,
        900_000,
        1_000_000,
        1_250_000,
        1_500_000,
        1_750_000,
        2_000_000,
        2_250_000,
        2_500_000,
        2_750_000,
        3_000_000,
        3_500_000,
        4_000_000,
        4_500_000,
        5_000_000,
        6_000_000,
        7_000_000,
        7_500_000,
        8_000_000,
        9_000_000,
        10_000_000,
        12_500_000,
        15_000_000,
        17_500_000,
        20_000_000,
        22_500_000,
        25_000_000,
        27_500_000,
        30_000_000,
        35_000_000,
        40_000_000,
        45_000_000,
        50_000_000,
        60_000_000,
        70_000_000,
        75_000_000,
        80_000_000,
        90_000_000,
        100_000_000,
        150_000_000,
        200_000_000,
        250_000_000,
        300_000_000,
        400_000_000,
        500_000_000,
        600_000_000,
        700_000_000,
        750_000_000,
        800_000_000,
        900_000_000,
        1_000_000_000,  # 1 billion
        1_500_000_000,  # 1.5 billion
        2_000_000_000,  # 2 billion
        2_500_000_000,  # 2.5 billion
        3_000_000_000,  # 3 billion
        4_000_000_000,  # 4 billion
        5_000_000_000,  # 5 billion
    ]

    def __init__(self, bot):
        self.bot = bot
        self.last_count: Optional[int] = None
        self.last_channel_edit: datetime = datetime.utcnow()
        self.minimum_edit_interval: timedelta = timedelta(minutes=5)
        self.button_cooldowns = {}  # Add cooldown tracking
        self.data_file = "ente_counts.json"
        self.default_data = {
            "last_count": None,
            "last_update": None,
            "historical_counts": [],
            "achieved_milestones": [],
        }
        self.load_data()  # Load persistent data
        self.monitor_files.start()

    def load_data(self):
        """Load previous count data from file"""
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.data = json.load(f)
        else:
            self.data = self.default_data

    def save_data(self):
        """Save current count data to file"""
        with open(self.data_file, "w") as f:
            json.dump(self.data, f)

    def cog_unload(self):
        self.monitor_files.cancel()

    async def safe_channel_edit(self, channel, new_name):
        """Safely edit channel name with rate limit consideration"""
        now = datetime.utcnow()
        time_since_last_edit = now - self.last_channel_edit

        if time_since_last_edit < self.minimum_edit_interval:
            wait_time = (
                self.minimum_edit_interval - time_since_last_edit
            ).total_seconds()
            logger.info(f"Waiting {wait_time:.2f}s before next channel edit")
            await asyncio.sleep(wait_time)

        try:
            await channel.edit(name=new_name)
            self.last_channel_edit = datetime.utcnow()
        except discord.HTTPException as e:
            if e.code == 429:  # Rate limit error
                retry_after = e.retry_after
                logger.warning(
                    f"Rate limited on channel edit. Retry after: {retry_after}s"
                )
                # Update our minimum interval if the rate limit is longer
                if retry_after > self.minimum_edit_interval.total_seconds():
                    self.minimum_edit_interval = timedelta(
                        seconds=retry_after * 1.1
                    )  # Add 10% buffer
                await asyncio.sleep(retry_after)
                # Retry the edit once after waiting
                await channel.edit(name=new_name)
                self.last_channel_edit = datetime.utcnow()
            else:
                raise

    async def fetch_file_count(self) -> Optional[int]:
        """Fetch the current file count from the API with retries."""
        for attempt in range(self.MAX_RETRIES):
            try:
                async with self.bot.http_session.get(
                    self.API_URL, timeout=self.API_TIMEOUT
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("count")
                    elif response.status == 429:  # Rate limit
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
                if attempt < self.MAX_RETRIES - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(self.RETRY_DELAY)
            except Exception as e:
                logger.error(
                    f"Error fetching file count (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}",
                    exc_info=True,
                )
                if attempt < self.MAX_RETRIES - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(self.RETRY_DELAY)

        return None

    @tasks.loop(seconds=300)
    async def monitor_files(self):
        """Monitor and update the file count in channel name and bot presence."""
        try:
            channel = self.bot.get_channel(int(self.bot.config["channel_id"]))
            if not channel:
                return

            current_count = await self.fetch_file_count()
            if current_count is not None and current_count != self.last_count:
                await self.safe_channel_edit(channel, f"📷 {current_count:,} Files")

                activity = discord.Activity(
                    type=discord.ActivityType.custom,
                    name=f"Securing {current_count:,} files",
                    state=f"Securing {current_count:,} files",
                )
                await self.bot.change_presence(
                    status=discord.Status.online, activity=activity
                )
                self.last_count = current_count

                logger.info(
                    f"Current minimum edit interval: {self.minimum_edit_interval.total_seconds()}s"
                )
        except Exception as e:
            logger.error(f"Error in file monitoring: {e}", exc_info=True)

    @monitor_files.before_loop
    async def before_monitor_files(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="files", description="Get the current number of files protected by Ente"
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def files(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    @files.error
    async def files_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"🦆 *Quack!* I need to catch my breath! Try again in {error.retry_after:.1f} seconds! 🕒",
                ephemeral=True,
            )

    async def generate_growth_graph(self) -> discord.File:
        """Generate a growth graph using matplotlib"""
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(10, 6))
        # Extract data
        dates = [
            datetime.fromtimestamp(entry["timestamp"])
            for entry in self.data["historical_counts"]
        ]
        counts = [entry["count"] for entry in self.data["historical_counts"]]

        # Create the line plot
        ax.plot(dates, counts, color="#FFCD3F", linewidth=2)

        # Customize the plot
        ax.set_title("Ente.io File Count Growth", pad=20)
        ax.set_xlabel("Date")
        ax.set_ylabel("Number of Files")

        # Format y-axis with comma separator
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, p: format(int(x), ","))
        )

        # Rotate x-axis labels for better readability
        plt.xticks(rotation=45)

        # Add grid
        ax.grid(True, linestyle="--", alpha=0.7)

        # Tight layout to prevent label cutoff
        plt.tight_layout()

        # Save to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        buf.seek(0)
        plt.close()

        return discord.File(buf, "growth.png")

    def predict_milestone(self, target: int) -> Tuple[datetime, bool]:
        """Predict when the next milestone will be reached"""
        if len(self.data["historical_counts"]) < 2:
            return None, False

        oldest = min(self.data["historical_counts"], key=lambda x: x["timestamp"])
        newest = max(self.data["historical_counts"], key=lambda x: x["timestamp"])
        time_diff = newest["timestamp"] - oldest["timestamp"]
        count_diff = newest["count"] - oldest["count"]

        if time_diff <= 0:
            return None, False

        daily_rate = (count_diff / time_diff) * 86400  # Convert to daily rate
        remaining_count = target - newest["count"]

        if remaining_count <= 0:
            return None, True  # Already achieved

        if daily_rate <= 0:
            return None, False

        days_until = remaining_count / daily_rate
        predicted_date = datetime.now(timezone.utc) + timedelta(days=days_until)

        return predicted_date, False

    async def handle_refresh(self, interaction: discord.Interaction):
        try:
            # Check cooldown
            user_id = interaction.user.id
            current_time = datetime.now(timezone.utc)

            if user_id in self.button_cooldowns:
                time_diff = (
                    current_time - self.button_cooldowns[user_id]
                ).total_seconds()
                if time_diff < 30:  # 30 second cooldown
                    await interaction.response.send_message(
                        f"Please wait {30 - int(time_diff)} seconds before refreshing again.",
                        ephemeral=True,
                    )
                    return

            await interaction.response.defer()
            self.button_cooldowns[user_id] = current_time

            current_count = await self.fetch_file_count()
            if current_count is not None:
                # Initialize variables
                new_milestones = []
                increase_text = ""
                daily_growth = ""
                milestone_text = ""

                # Calculate increase/decrease since last refresh
                if self.data["last_count"] is not None:
                    increase = current_count - self.data["last_count"]
                    percent_increase = (increase / self.data["last_count"]) * 100

                    arrow = "↑" if increase >= 0 else "↓"
                    increase_text = f"**{increase:+,}** ({percent_increase:+.2f}%)"

                # Calculate weekly growth
                week_ago = (current_time - timedelta(days=7)).timestamp()
                week_data = [
                    entry
                    for entry in self.data["historical_counts"]
                    if entry["timestamp"] > week_ago
                ]

                weekly_growth = ""
                if len(week_data) > 0:
                    total_count = sum(entry["count"] for entry in week_data)
                    average_count = total_count / len(week_data)
                    week_increase = current_count - average_count
                    week_percent = (
                        (week_increase / average_count) * 100
                        if average_count > 0
                        else 0
                    )

                    # Determine the arrow and format the growth
                    week_arrow = "↑" if week_increase >= 0 else "↓"
                    weekly_growth = f"**{round(week_increase):,}** ({'+' if week_percent > 0 else ''}{round(week_percent, 2):,.2f}%)"

                # Calculate daily average using all available data
                daily_growth = ""
                if len(self.data["historical_counts"]) >= 2:
                    oldest_entry = min(
                        self.data["historical_counts"], key=lambda x: x["timestamp"]
                    )
                    days_diff = (
                        current_time.timestamp() - oldest_entry["timestamp"]
                    ) / 86400
                    if days_diff >= 1:
                        total_increase = current_count - oldest_entry["count"]
                        daily_avg = total_increase / days_diff
                        daily_growth = f"**{daily_avg:+,.0f}** files/day"

                # Create embed with all statistics
                files_embed = discord.Embed(
                    title="🦆 Ente Files Statistics", color=0xFFCD3F
                )

                files_embed.add_field(
                    name="Protected Files",
                    value=f"🔐 **{current_count:,}**",
                    inline=False,
                )

                if increase_text:
                    files_embed.add_field(
                        name=f"{arrow} Files changed", value=increase_text, inline=True
                    )

                if weekly_growth:
                    files_embed.add_field(
                        name=f"{week_arrow} Weekly Growth",
                        value=weekly_growth,
                        inline=True,
                    )

                if daily_growth:
                    files_embed.add_field(
                        name="📈 Daily Average", value=daily_growth, inline=True
                    )

                # Predict next milestone
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
                            f"**{next_milestone:,}** files\n"
                            f"~{int(days_until)} days (<t:{predicted_timestamp}:D>)"
                        )
                        files_embed.add_field(
                            name="🎯 Next Milestone", value=milestone_text, inline=False
                        )

                # Add milestone celebration if any
                if new_milestones:
                    celebration = "\n".join(
                        f"🎉 **{m:,}** files!" for m in new_milestones[-5:]
                    )
                    files_embed.add_field(
                        name="Achievements Unlocked!", value=celebration, inline=False
                    )

                current_timestamp = int(current_time.timestamp())
                files_embed.add_field(
                    name="Last Updated",
                    value=f"<t:{current_timestamp}:R>",
                    inline=False,
                )

                # Update stored data
                self.data["last_count"] = current_count
                self.data["last_update"] = current_timestamp

                # Store historical data point
                current_data = {"timestamp": current_timestamp, "count": current_count}
                self.data["historical_counts"].append(current_data)

                # Trim historical data to last 30 days
                cutoff = current_timestamp - (30 * 24 * 60 * 60)
                self.data["historical_counts"] = [
                    entry
                    for entry in self.data["historical_counts"]
                    if entry["timestamp"] > cutoff
                ]

                self.save_data()

                view = PersistentView()
                view.add_item(RefreshButton())

                try:
                    await interaction.message.edit(embed=files_embed, view=view)
                except Exception as e:
                    await interaction.followup.send(embed=files_embed, view=view)

            else:
                await interaction.followup.send(
                    "Failed to fetch the current file count. Please try again later.",
                    ephemeral=True,
                )
        except Exception as e:
            logger.error(f"Error in handle_refresh: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    "An error occurred while processing the request. Please try again later.",
                    ephemeral=True,
                )
            except:
                pass

    def format_time_ago(self, delta: timedelta) -> str:
        """Format a timedelta into a human-readable string."""
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"


async def setup(bot):
    await bot.add_cog(FileTracker(bot))
