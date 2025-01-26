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
        self.ptero_api_key = os.getenv("PTERO_API_KEY")
        self.ptero_url = os.getenv("PTERO_URL")
        self.server_id = os.getenv("PTERO_SERVER_ID")

    @app_commands.command(
        name="stats", description="Check how long I've been waddling around! 🦆"
    )
    async def stats_slash(self, interaction: discord.Interaction):
        """Check the bot's uptime, ping, and server resources with a slash command."""
        current_time = time.time()
        uptime_seconds = current_time - self.start_time
        uptime_str = str(datetime.timedelta(seconds=round(uptime_seconds)))
        latency = round(self.bot.latency * 1000)

        embed = discord.Embed(title="🦆 Ducky's Stats", color=0xFFCD3F)
        embed.add_field(
            name="Bot Uptime",
            value=f"I've been swimming around for {uptime_str}! 🏊‍♂️",
            inline=False,
        )
        embed.add_field(
            name="Duck Speed",
            value=f"Quack! My response time is {latency}ms 🏃‍♂️",
            inline=False,
        )

        # Add server resource information if available
        if self.ptero_api_key and self.ptero_url and self.server_id:
            resources = await self.get_server_resources()
            if resources and "attributes" in resources:
                attrs = resources["attributes"]

                # Memory and CPU
                memory_used = round(
                    attrs.get("resources", {}).get("memory_bytes", 0) / 1024 / 1024, 2
                )
                cpu_usage = attrs.get("resources", {}).get("cpu_absolute", 0)

                # Network stats (convert to KB)
                network_rx = round(
                    attrs.get("resources", {}).get("network_rx_bytes", 0) / 1024, 2
                )
                network_tx = round(
                    attrs.get("resources", {}).get("network_tx_bytes", 0) / 1024, 2
                )

                embed.add_field(
                    name="Server Resources",
                    value=f"Memory: {memory_used}MB\nCPU: {cpu_usage}%",
                    inline=True,
                )
                embed.add_field(
                    name="Network Usage",
                    value=f"↓ {network_rx}KB\n↑ {network_tx}KB",
                    inline=True,
                )

        embed.set_footer(text="Thanks for checking on me! *quack quack*")
        await interaction.response.send_message(embed=embed)

    async def get_server_resources(self) -> dict:
        """Fetch server resource usage from Pterodactyl"""
        headers = {
            "Authorization": f"Bearer {self.ptero_api_key}",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.ptero_url}/api/client/servers/{self.server_id}/resources",
                headers=headers,
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None


def setup(bot):
    bot.add_cog(BotStats(bot))
