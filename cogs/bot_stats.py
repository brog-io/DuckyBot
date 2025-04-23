import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands
import aiohttp
import os


class BotStats(commands.Cog):
    """A cog for bot statistics like uptime and ping."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()
        self.ptero_api_key = os.getenv("PEBBLE_API_KEY")
        self.ptero_url = os.getenv("PEBBLE_API_URL")
        self.server_id = os.getenv("PEBBLE_SERVER_ID")

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
        embed.add_field(name="Bot Uptime", value=f"{uptime_str}", inline=False)
        embed.add_field(name="Ping", value=f"{latency}ms", inline=False)

        # Get PebbleHost server resources
        if self.ptero_api_key and self.ptero_url and self.server_id:
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
                    name="Network",
                    value=f"↓ {net_rx}KB\n↑ {net_tx}KB",
                    inline=True,
                )

        embed.set_footer(text="Thanks for checking on me! *quack quack*")
        await interaction.response.send_message(embed=embed)

    async def get_server_resources(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.ptero_api_key}",
            "Accept": "application/json",
        }

        url = f"{self.ptero_url}/api/client/servers/{self.server_id}/resources"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return None


def setup(bot):
    bot.add_cog(BotStats(bot))
