import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import logging
from typing import Optional
from aiohttp import ClientTimeout
import asyncio


class EnteStatus(commands.Cog):
    # Constants
    API_URL = "https://api.ente.io/ping"
    API_TIMEOUT = ClientTimeout(total=5)  # 5 seconds timeout

    def __init__(self, bot):
        self.bot = bot
        self.ente_status: Optional[discord.Embed] = None
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
        """Background task to check the status of Ente periodically."""
        try:
            async with aiohttp.ClientSession(timeout=self.API_TIMEOUT) as session:
                async with session.get(self.API_URL) as response:
                    status_embed = await self._create_status_embed(response)
        except asyncio.TimeoutError:
            status_embed = discord.Embed(
                title="Ente Status",
                description="Request timed out. Service might be experiencing delays. ⚠️",
                color=discord.Color.orange(),
            )
        except Exception as e:
            status_embed = discord.Embed(
                title="Ente Status",
                description=f"Error checking status: {str(e)}",
                color=discord.Color.orange(),
            )

        self.ente_status = status_embed

    async def _create_status_embed(
        self, response: aiohttp.ClientResponse
    ) -> discord.Embed:
        """Create status embed based on API response."""
        if response.status == 200:
            try:
                data = await response.json()
                if data.get("message") == "pong":
                    return discord.Embed(
                        title="Ente Status",
                        description="Ente is operational! ✅",
                        color=discord.Color.green(),
                    )
            except Exception:
                pass

            return discord.Embed(
                title="Ente Status",
                description="Unexpected response received. ⚠️",
                color=discord.Color.orange(),
            )
        else:
            return discord.Embed(
                title="Ente Status",
                description="Ente might be down. ⚠️",
                color=discord.Color.red(),
            )

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
