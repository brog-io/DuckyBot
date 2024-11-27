from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PersistentView(View):
    def __init__(self):
        super().__init__(timeout=None)


class RefreshButton(Button):
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
    def __init__(self, bot):
        self.bot = bot
        self.last_count = None
        self.last_channel_edit = datetime.utcnow()
        self.minimum_edit_interval = timedelta(minutes=5)
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

    @tasks.loop(seconds=300)
    async def monitor_files(self):
        try:
            channel = self.bot.get_channel(int(self.bot.config["channel_id"]))
            if not channel:
                return

            async with self.bot.http_session.get(
                "https://api.ente.io/files/count"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    current_count = data.get("count")

                    if current_count != self.last_count:
                        await self.safe_channel_edit(
                            channel, f"📊 {current_count:,} Files"
                        )

                        activity = discord.Activity(
                            type=discord.ActivityType.custom,
                            name=f"Securing {current_count:,} files",
                            state=f"Securing {current_count:,} files",
                        )
                        await self.bot.change_presence(
                            status=discord.Status.online, activity=activity
                        )
                        self.last_count = current_count

                        # Log current rate limit settings
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
    async def files(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    async def handle_refresh(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        guild_allowed, guild_wait = self.bot.guild_limiter.check(guild_id)
        if not guild_allowed:
            await interaction.response.send_message(
                f"This server is being rate limited. Please wait {guild_wait:.1f} seconds.",
                ephemeral=True,
            )
            return

        user_allowed, user_wait = self.bot.user_limiter.check(user_id)
        if not user_allowed:
            await interaction.response.send_message(
                f"Please wait {user_wait:.1f} seconds before using this command again.",
                ephemeral=True,
            )
            return

        try:
            async with self.bot.http_session.get(
                "https://api.ente.io/files/count"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    current_count = data.get("count")

                    embed = discord.Embed(
                        title="Ente Files Count",
                        description=f"Currently protecting **{current_count:,}** files",
                        color=0xFFCD3F,
                        timestamp=discord.utils.utcnow(),
                    )

                    view = PersistentView()
                    view.add_item(RefreshButton())

                    if isinstance(interaction.message, discord.Message):
                        await interaction.message.edit(embed=embed, view=view)
                        await interaction.response.defer()
                    else:
                        await interaction.response.send_message(embed=embed, view=view)
                else:
                    await interaction.response.send_message(
                        "Failed to fetch the current file count. Please try again later.",
                        ephemeral=True,
                    )
        except Exception as e:
            logger.error(f"Error fetching file count: {e}", exc_info=True)
            await interaction.response.send_message(
                "An error occurred while fetching the count. Please try again later.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(FileTracker(bot))
