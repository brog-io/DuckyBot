import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if self.bot.user in message.mentions:
            await message.reply(
                "*Quack* <:lilducky:1069841394929238106>", mention_author=False
            )

    @app_commands.command(
        name="tip", description="Get a random helpful tip from Ente's documentation."
    )
    async def tip(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.send_tip(interaction)

    async def send_tip(self, interaction: discord.Interaction):
        prompt = "Give the user a helpful, concise tip from Ente's documentation or feature set."

        payload = {"query": prompt, "key": API_KEY}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.poggers.win/api/ente/docs-search", json=payload
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send("API error.", ephemeral=True)
                    return

                data = await resp.json()
                tip = data.get("answer", "No tip found.")
                await interaction.followup.send(tip, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Misc(bot))
