import discord
from discord.ext import commands, tasks
import time
from discord import app_commands
import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

PING_SECRET = os.getenv("PING_SECRET")


class Ping(commands.Cog):
    """Provides a ping command to measure latency and response times."""

    def __init__(self, bot):
        self.bot = bot
        self.heartbeat.start()

    def cog_unload(self):
        self.heartbeat.cancel()

    @tasks.loop(seconds=60)
    async def heartbeat(self):
        """Ping an external endpoint to signal that the bot is alive.

        Runs as a cog task on the bot's shared HTTP session, so it no longer
        opens a fresh session every minute or survives a bot restart.
        """
        try:
            async with self.bot.http_session.post(
                "https://brog.io/ping",
                headers={"x-auth-key": PING_SECRET},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                await resp.read()
        except Exception as e:
            logger.warning(f"Ping failed: {e}")

    @app_commands.command(
        name="ping", description="Check the bot's latency and response time."
    )
    async def ping_slash(self, interaction: discord.Interaction):
        """Responds with detailed latency stats."""
        try:
            # Record the time before the interaction response
            start_time = time.perf_counter()

            # Measure gateway latency (Discord WebSocket latency)
            gateway_latency = round(self.bot.latency * 1000)

            # Send a deferred response to avoid timeouts
            await interaction.response.defer(ephemeral=True)
            command_delay = round((time.perf_counter() - start_time) * 1000)

            # Measure total time including followup
            total_start = time.perf_counter()
            msg = await interaction.followup.send(
                "Pong!\nCalculating response time...", ephemeral=True
            )
            total_time = round((time.perf_counter() - total_start) * 1000)

            # Edit the message with full results
            await msg.edit(
                content=(
                    f"Pong!\n"
                    f"Gateway latency: `{gateway_latency} ms`\n"
                    f"Command delay: `{command_delay} ms`\n"
                    f"Time to respond: `{total_time} ms`"
                )
            )

        except Exception as e:
            logger.error(f"Error in ping command: {e}")
            await interaction.response.send_message(
                "An error occurred while running the ping test.", ephemeral=True
            )


async def setup(bot):
    """Setup function for adding the cog to the bot."""
    await bot.add_cog(Ping(bot))
