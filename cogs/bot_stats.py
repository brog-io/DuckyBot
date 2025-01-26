import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands
import aiohttp
import os


def is_developer():
    """Check if the user is the developer"""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != 1086344307574837309:
            await interaction.response.send_message(
                "❌ Only the developer can use this command!", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


class DevOnlyCommand(app_commands.Command):
    """A custom command class that's only visible to the developer"""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == 1086344307574837309

    @property
    def command_ids(self):
        # Hide command from everyone except developer
        if hasattr(self, "_command_ids"):
            return self._command_ids
        return {}


class BotStats(commands.Cog):
    """A cog for bot statistics like uptime and ping."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()
        self.ptero_api_key = os.getenv("PTERO_API_KEY")
        self.ptero_url = os.getenv("PTERO_URL")
        self.server_id = os.getenv("PTERO_SERVER_ID")

        # Replace the restart command with our custom version
        restart_cmd = DevOnlyCommand(
            name="restart",
            description="Restart the bot server 🔄",
            callback=self.restart_server,
        )
        self.bot.tree.add_command(restart_cmd)

    @app_commands.command(
        name="stats", description="Check how long I've been waddling around! 🦆"
    )
    async def stats_slash(self, interaction: discord.Interaction):
        """Check the bot's uptime and ping with a slash command."""
        current_time = time.time()
        uptime_seconds = current_time - self.start_time
        uptime_str = str(datetime.timedelta(seconds=round(uptime_seconds)))

        latency = round(self.bot.latency * 1000)

        embed = discord.Embed(title="🦆 Ducky's Stats", color=0xFFCD3F)
        embed.add_field(
            name="Time Spent Waddling",
            value=f"I've been swimming around for {uptime_str}! 🏊‍♂️",
            inline=False,
        )
        embed.add_field(
            name="Duck Speed",
            value=f"Quack! My response time is {latency}ms 🏃‍♂️",
            inline=False,
        )
        embed.set_footer(text="Thanks for checking on me! *quack quack*")

        await interaction.response.send_message(embed=embed)

    async def send_power_action(self, action: str) -> bool:
        """Send power action to Pterodactyl server"""
        headers = {
            "Authorization": f"Bearer {self.ptero_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.ptero_url}/api/client/servers/{self.server_id}/power",
                headers=headers,
                json={"signal": action},
            ) as response:
                return response.status == 204

    async def restart_server(self, interaction: discord.Interaction):
        """Restart the bot server (Developer only)."""
        if interaction.user.id != 1086344307574837309:
            await interaction.response.send_message(
                "❌ Only the developer can use this command!", ephemeral=True
            )
            return

        if not self.ptero_api_key or not self.ptero_url or not self.server_id:
            await interaction.response.send_message(
                "❌ Pterodactyl API is not configured!", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        success = await self.send_power_action("restart")

        if success:
            await interaction.followup.send(
                "🔄 Server restart initiated! I'll be back soon!", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Failed to restart the server. Please check the logs.",
                ephemeral=True,
            )


def setup(bot):
    bot.add_cog(BotStats(bot))
