import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import logging


class EnteStatus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_url = "https://api.ente.io/ping"
        self.ente_status = None  # Store the latest status
        self.logger = logging.getLogger(__name__)

    @commands.Cog.listener()
    async def on_ready(self):
        # Sync the slash commands
        try:
            synced = await self.bot.tree.sync()
            print(f"Synced {len(synced)} command(s).")
        except Exception as e:
            self.logger.error(f"Error syncing commands: {e}")

        self.check_status.start()  # Start the background task only after the bot is ready

    @tasks.loop(minutes=5)
    async def check_status(self):
        """
        Background task to check the status of Ente periodically.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("message") == "pong":
                            status_embed = discord.Embed(
                                title="Ente Status",
                                description="Ente is operational! ✅",
                                color=discord.Color.green(),
                            )
                        else:
                            status_embed = discord.Embed(
                                title="Ente Status",
                                description="Unexpected response received. ⚠️",
                                color=discord.Color.orange(),
                            )
                    else:
                        status_embed = discord.Embed(
                            title="Ente Status",
                            description="Ente might be down. ⚠️",
                            color=discord.Color.red(),
                        )
        except Exception as e:
            status_embed = discord.Embed(
                title="Ente Status",
                description=f"Error checking status: {e}",
                color=discord.Color.orange(),
            )

        self.ente_status = status_embed

    @check_status.before_loop
    async def before_check_status(self):
        """
        Ensure the bot is ready before starting the loop.
        """
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="status", description="Check the current status of Ente."
    )
    async def ente_status(self, interaction: discord.Interaction):
        """
        Slash command to check the current status of Ente.
        """
        try:
            if self.ente_status is None:
                await interaction.response.send_message(
                    "Status is currently being checked, please try again in a moment.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=self.ente_status, ephemeral=True
                )
        except Exception as e:
            self.logger.error(f"Error in ente_status command: {e}")
            await interaction.response.send_message(
                f"An error occurred while checking the status: {e}", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(EnteStatus(bot))
