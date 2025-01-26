from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from aiohttp import ClientTimeout

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

    def __init__(self, bot):
        self.bot = bot
        self.last_count: Optional[int] = None
        self.last_channel_edit: datetime = datetime.utcnow()
        self.minimum_edit_interval: timedelta = timedelta(minutes=5)
        self.button_cooldowns = {}  # Add cooldown tracking
        self.monitor_files.start()

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
    @app_commands.checks.cooldown(
        1, 30, key=lambda i: (i.guild_id, i.user.id)
    )  # 1 use per 30s per user per guild
    async def files(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    @files.error
    async def files_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"🦆 *Quack!* I need to catch my breath! Try again in {error.retry_after:.1f} seconds! 🕒",
                ephemeral=True,
            )

    async def handle_refresh(self, interaction: discord.Interaction):
        try:
            # Check cooldown
            user_id = interaction.user.id
            current_time = datetime.utcnow()
            if user_id in self.button_cooldowns:
                time_elapsed = current_time - self.button_cooldowns[user_id]
                if time_elapsed < timedelta(seconds=30):  # 30 second cooldown
                    remaining = round(30 - time_elapsed.total_seconds())
                    await interaction.response.send_message(
                        f"🐣 *Quack!* I need to catch my breath! Try again in {remaining} seconds! 🕒",
                        ephemeral=True,
                    )
                    return

            # Update cooldown
            self.button_cooldowns[user_id] = current_time

            # Defer the interaction first
            await interaction.response.defer()

            current_count = await self.fetch_file_count()
            if current_count is not None:
                files_embed = discord.Embed(
                    title="Ente Files Count",
                    description=f"Currently protecting **{current_count:,}** files",
                    color=0xFFCD3F,
                    timestamp=discord.utils.utcnow(),
                )

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
            logger.error(f"Error fetching file count: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while fetching the count. Please try again later.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(FileTracker(bot))
