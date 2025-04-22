import discord
from discord.ext import commands
import aiohttp
import os
import re
from discord import ui
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")
SELFHELP_CHANNEL_IDS = [1364139133794123807]

REACTION_TRIGGER = "❓"


class SupportButton(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="This didn't help",
        style=discord.ButtonStyle.danger,
        custom_id="support_button",
    )
    async def help_button(self, interaction: discord.Interaction, button: ui.Button):
        support_role = discord.utils.get(interaction.guild.roles, name="Support")
        if support_role:
            await interaction.response.send_message(
                f"<@&{1364141260708909117}> User still needs help in {interaction.channel.mention}",
                ephemeral=False,
            )
        else:
            await interaction.response.send_message(
                "Support role not found.", ephemeral=True
            )


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SupportButton())

    async def query_api(self, query: str) -> str:
        payload = {"query": query, "key": API_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.poggers.win/api/ente/docs-search", json=payload
            ) as resp:
                if resp.status != 200:
                    return f"API error: {resp.status}"
                data = await resp.json()
                return (
                    data.get("answer", "No answer returned.")
                    if data.get("success")
                    else "Sorry, I couldn’t find an answer."
                )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if thread.parent_id not in SELFHELP_CHANNEL_IDS:
            return

        await thread.send("Analyzing your question, please wait...")
        answer = await self.query_api(thread.name)
        sent = await thread.send(answer, view=SupportButton())
        async for msg in thread.history(limit=5):
            if msg.content.startswith("Analyzing your question"):
                await msg.delete()
                break

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.channel.id in SELFHELP_CHANNEL_IDS:
            return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != REACTION_TRIGGER:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        message = await channel.fetch_message(payload.message_id)
        if message.author.bot:
            return

        answer = await self.query_api(message.content)
        thread = await message.create_thread(name=message.content[:90])
        await thread.send(answer, view=SupportButton())


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
