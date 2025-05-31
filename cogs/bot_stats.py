import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands
import aiohttp
import json
import logging
import os

logger = logging.getLogger(__name__)


class BotStats(commands.Cog):
    """Displays bot and server stats."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

        # Load config.json with error handling
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r") as f:
                    config = json.load(f)
            else:
                logger.warning("config.json not found, using empty config")
                config = {}
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")
            config = {}

        self.api_key = config.get("PEBBLE_API_KEY")
        self.api_url = config.get("PEBBLE_API_URL")
        self.server_id = config.get("PEBBLE_SERVER_ID")

    @app_commands.command(name="stats", description="Show bot stats")
    async def stats_slash(self, interaction: discord.Interaction):
        """Bot and PebbleHost server stats."""
        try:
            # Defer the response to prevent timeout
            await interaction.response.defer()

            bot_uptime = str(
                datetime.timedelta(seconds=round(time.time() - self.start_time))
            )
            latency = round(self.bot.latency * 1000)

            embed = discord.Embed(
                title="📊 Ducky's Performance Report",
                description="Real-time stats below:",
                color=0xFFD966,
            )

            embed.add_field(name="🤖 Bot Uptime", value=bot_uptime, inline=True)
            embed.add_field(name="📡 Ping", value=f"{latency}ms", inline=True)

            # Only try to get server stats if we have the required config
            if self.api_key and self.api_url and self.server_id:
                stats = await self.get_server_resources()
                if stats and "attributes" in stats:
                    state = stats["attributes"].get("state", "unknown")
                    res = stats["attributes"]["resources"]

                    if res.get("uptime", 0) > 0 and state == "running":
                        srv_uptime = str(
                            datetime.timedelta(seconds=res.get("uptime", 0))
                        )
                        mem = round(res.get("memory_bytes", 0) / 1024 / 1024, 2)
                        cpu = round(res.get("cpu_absolute", 0), 2)
                        disk = round(res.get("disk_bytes", 0) / 1024 / 1024 / 1024, 2)
                        net_rx = round(res.get("network_rx_bytes", 0) / 1024, 2)
                        net_tx = round(res.get("network_tx_bytes", 0) / 1024, 2)

                        embed.add_field(
                            name="🖥️ Server Uptime", value=srv_uptime, inline=True
                        )
                        embed.add_field(
                            name="💾 Resources",
                            value=f"RAM: {mem}MB\nCPU: {cpu}%\nDisk: {disk}GB",
                            inline=True,
                        )
                        embed.add_field(
                            name="📶 Network",
                            value=f"↓ {net_rx}KB / ↑ {net_tx}KB",
                            inline=True,
                        )
                    else:
                        embed.add_field(
                            name="🛑 Server Status",
                            value="Server is offline",
                            inline=False,
                        )
                else:
                    embed.add_field(
                        name="⚠️ Server Info",
                        value="Could not fetch server stats. API might be unavailable.",
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="⚠️ Server Info",
                    value="PebbleHost API configuration missing.",
                    inline=False,
                )

            embed.set_footer(text="Thanks for checking on me! *quack quack*")

            # Use followup since we deferred the response
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in stats command: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while fetching stats.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "An error occurred while fetching stats.", ephemeral=True
                )

    async def get_server_resources(self) -> dict:
        """Fetch server resources from PebbleHost API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.api_url}/api/client/servers/{self.server_id}/resources"

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 403:
                        logger.warning(
                            "[PEBBLEHOST] ❌ Unauthorized — check your API key and permissions."
                        )
                    elif response.status == 502:
                        logger.warning(
                            "[PEBBLEHOST] ⚠️ Daemon unreachable — server might be offline."
                        )
                    else:
                        logger.error(
                            f"[PEBBLEHOST] ❌ Unexpected error {response.status}: {await response.text()}"
                        )
        except Exception as e:
            logger.error(f"Error fetching server resources: {e}")

        return None


async def setup(bot):
    await bot.add_cog(BotStats(bot))
