import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="quack", description="How many times to quack")
    async def quack(self, interaction: discord.Interaction, times: int):
        times = max(1, min(times, 50))

        # 10% chance to refuse as a joke
        if random.random() < 0.10:
            responses = [
                "Sorry, I'm all out of quacks today.",
                "Quack limit reached. Try again later.",
                "The ducks are on strike.",
                "No quacks for you. 🦆",
                "Quack error: User too funny.",
            ]
            await interaction.response.send_message(
                random.choice(responses), ephemeral=True
            )
        else:
            await interaction.response.send_message("quack " * times, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if self.bot.user in message.mentions:
            await message.reply(
                "*Quack* <:lilducky:1069841394929238106>", mention_author=False
            )

    @app_commands.command(name="duck", description="Get a random duck image")
    async def duck(self, interaction: discord.Interaction):
        async with aiohttp.ClientSession() as session:
            async with session.get("https://random-d.uk/api/v2/quack") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    image_url = data.get("url")
                    embed = discord.Embed(
                        title="Quack!", color=discord.Color(0xFFCD3F)
                    ).set_image(url=image_url)
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "Couldn't fetch a duck image right now. Try again later.",
                        ephemeral=True,
                    )

    @app_commands.command(name="tip", description="Get a random helpful tip from Ente.")
    async def tip(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        payload = {"key": API_KEY}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.poggers.win/api/ente/tip", json=payload
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        "Failed to fetch a tip.", ephemeral=True
                    )
                    return

                data = await resp.json()
                tip = data.get("tip", "No tip found.")
                url = data.get("documentationUrl")

                if url:
                    button = discord.ui.Button(label="View Documentation", url=url)
                    view = discord.ui.View()
                    view.add_item(button)
                    await interaction.followup.send(tip, ephemeral=True, view=view)
                else:
                    await interaction.followup.send(tip, ephemeral=True)

    @app_commands.command(name="help", description="List all available commands.")
    async def help(self, interaction: discord.Interaction):
        cmds = []

        is_admin = interaction.user.guild_permissions.administrator

        for cmd in self.bot.tree.walk_commands():
            # Basic hardcoded filter: skip known admin-only commands
            if not is_admin and cmd.name in [
                "welcome"
            ]:  # Add more admin-only commands here
                continue

            cmds.append(
                f"**/{cmd.qualified_name}** — {cmd.description or 'No description'}"
            )

        embed = discord.Embed(
            title="Available Commands",
            description="\n".join(cmds) or "No commands available.",
            color=0xFFCD3F,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Misc(bot))
