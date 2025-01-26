import discord
from discord.ext import commands
import time
import datetime
from discord import app_commands


class BotStats(commands.Cog):
    """A cog for bot statistics like uptime and ping."""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

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


def setup(bot):
    bot.add_cog(BotStats(bot))
