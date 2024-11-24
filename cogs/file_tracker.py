from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View

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
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FileTracker")
        if cog:
            await cog.handle_refresh(interaction)


class FileTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_count = None
        self.monitor_files.start()

    def cog_unload(self):
        self.monitor_files.cancel()

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
                        await channel.edit(name=f"📊 {current_count:,} Files")
                        activity = discord.Activity(
                            type=discord.ActivityType.watching,
                            name=f"{current_count:,} files",
                        )
                        await self.bot.change_presence(
                            status=discord.Status.online, activity=activity
                        )
                        self.last_count = current_count
        except Exception as e:
            logger.error(f"Error in file monitoring: {e}")

    @monitor_files.before_loop
    async def before_monitor_files(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="files", description="Get the current number of files tracked by Ente"
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
                        description=f"Currently tracking **{current_count:,}** files",
                        color=0xFFCD3F,
                        timestamp=discord.utils.utcnow(),
                    )

                    view = PersistentView()
                    view.add_item(RefreshButton())

                    if isinstance(interaction.message, discord.Message):
                        # If this is a button interaction, edit the existing message
                        await interaction.message.edit(embed=embed, view=view)
                        await interaction.response.defer()
                    else:
                        # If this is a slash command, send a new message
                        await interaction.response.send_message(embed=embed, view=view)
                else:
                    await interaction.response.send_message(
                        "Failed to fetch the current file count. Please try again later.",
                        ephemeral=True,
                    )
        except Exception as e:
            logger.error(f"Error fetching file count: {e}")
            await interaction.response.send_message(
                "An error occurred while fetching the count. Please try again later.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(FileTracker(bot))
