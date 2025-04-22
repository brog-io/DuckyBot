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
