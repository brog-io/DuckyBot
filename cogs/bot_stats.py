import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands
import aiohttp
import json


class BotStats(commands.Cog):
    """Displays bot and server stats."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

        # Load config.json
        with open("config.json", "r") as f:
            config = json.load(f)

        self.api_key = config.get("PEBBLE_API_KEY")
        self.api_url = config.get("PEBBLE_API_URL")
        self.server_id = config.get("PEBBLE_SERVER_ID")

    @app_commands.command(name="stats", description="Show bot & server stats 🦆")
    async def stats_slash(self, interaction: discord.Interaction):
        """Bot and PebbleHost server stats."""
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

        stats = await self.get_server_resources()
        if stats and "attributes" in stats:
            state = stats["attributes"].get("state", "unknown")
            res = stats["attributes"]["resources"]

            if res.get("uptime", 0) > 0 and state == "running":
                srv_uptime = str(datetime.timedelta(seconds=res.get("uptime", 0)))
                mem = round(res.get("memory_bytes", 0) / 1024 / 1024, 2)
                cpu = round(res.get("cpu_absolute", 0), 2)
                disk = round(res.get("disk_bytes", 0) / 1024 / 1024 / 1024, 2)
                net_rx = round(res.get("network_rx_bytes", 0) / 1024, 2)
                net_tx = round(res.get("network_tx_bytes", 0) / 1024, 2)

                embed.add_field(name="🖥️ Server Uptime", value=srv_uptime, inline=True)
                embed.add_field(
                    name="💾 Resources",
                    value=f"RAM: {mem}MB\nCPU: {cpu}%\nDisk: {disk}GB",
                    inline=True,
                )
                embed.add_field(
                    name="📶 Network", value=f"↓ {net_rx}KB / ↑ {net_tx}KB", inline=True
                )
            else:
                embed.add_field(
                    name="🛑 Server Status", value="Server is offline", inline=False
                )
        else:
            embed.add_field(
                name="⚠️ Server Info",
                value="Could not fetch server stats. API key or ID may be wrong.",
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
                        "[PEBBLEHOST] ❌ Unauthorized — check your API key and permissions."
                    )
                elif response.status == 502:
                    print(
                        "[PEBBLEHOST] ⚠️ Daemon unreachable — server might be offline."
                    )
                else:
                    print(
                        f"[PEBBLEHOST] ❌ Unexpected error {response.status}: {await response.text()}"
                    )
                return None


def setup(bot):
    bot.add_cog(BotStats(bot))
