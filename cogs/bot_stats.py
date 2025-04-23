import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands
import aiohttp
import json


class BotStats(commands.Cog):
    """A cog for bot statistics like uptime and ping."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

        # Load config.json
        with open("config.json", "r") as f:
            config = json.load(f)

        self.api_key = config.get("PEBBLE_API_KEY")
        self.api_url = config.get("PEBBLE_API_URL")
        self.server_id = config.get("PEBBLE_SERVER_ID")

    @app_commands.command(
        name="stats", description="Check how long I've been waddling around! 🦆"
    )
    async def stats_slash(self, interaction: discord.Interaction):
        """Check the bot's uptime, ping, and server resources."""
        current_time = time.time()
        uptime_seconds = current_time - self.start_time
        uptime_str = str(datetime.timedelta(seconds=round(uptime_seconds)))
        latency = round(self.bot.latency * 1000)

        embed = discord.Embed(title="🦆 Ducky's Stats", color=0xFFCD3F)
        embed.add_field(name="Bot Uptime", value=uptime_str, inline=False)
        embed.add_field(name="Ping", value=f"{latency}ms", inline=False)

        # Fetch server stats
        if self.api_key and self.api_url and self.server_id:
            resources = await self.get_server_resources()
            if resources and "attributes" in resources:
                attrs = resources["attributes"]["resources"]

                memory = round(attrs.get("memory_bytes", 0) / 1024 / 1024, 2)
                cpu = round(attrs.get("cpu_absolute", 0), 2)
                disk = round(attrs.get("disk_bytes", 0) / 1024 / 1024 / 1024, 2)
                net_rx = round(attrs.get("network_rx_bytes", 0) / 1024, 2)
                net_tx = round(attrs.get("network_tx_bytes", 0) / 1024, 2)

                embed.add_field(
                    name="Server Resources",
                    value=f"Memory: {memory}MB\nCPU: {cpu}%\nDisk: {disk}GB",
                    inline=True,
                )
                embed.add_field(
                    name="Network Usage",
                    value=f"↓ {net_rx}KB\n↑ {net_tx}KB",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Server Stats Unavailable",
                    value="Could not retrieve server stats. Check API key or server status.",
                    inline=False,
                )

        embed.set_footer(text="Thanks for checking on me! *quack quack*")
        await interaction.response.send_message(embed=embed)

    async def get_server_resources(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.api_url}/api/client/servers/{self.server_id}/resources"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 403:
                    print(
                        "[PEBBLEHOST] ❌ Authorization failed. Check your API key and server access."
                    )
                elif response.status == 502:
                    print(
                        "[PEBBLEHOST] ⚠️ Daemon connection failed. Server may be offline."
                    )
                else:
                    print(
                        f"[PEBBLEHOST] ❌ Unexpected error {response.status}: {await response.text()}"
                    )
                return None


def setup(bot):
    bot.add_cog(BotStats(bot))
