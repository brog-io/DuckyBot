import discord
from discord.ext import commands
from discord.ui import Button, View
from openai import AsyncOpenAI
import json
import os
import logging
from dotenv import load_dotenv

load_dotenv()

discord_token = os.getenv("DISCORD_BOT_TOKEN")
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    raise ValueError("OPENAI_API_KEY is not set in .env")

openai_client = AsyncOpenAI(api_key=openai_api_key)

config_file = "config.json"
with open(config_file) as f:
    config = json.load(f)

log_channel_id = config["log_channel_id"]
whitelisted_role_ids = config["role_whitelist"]

logger = logging.getLogger(__name__)


async def check_scam_with_openai(message: str) -> bool:
    try:
        system_prompt = (
            "You are a security expert. Determine if the following message is a scam or phishing attempt. "
            "Respond only with 'Yes' if it is a scam, or 'No' if it is not."
        )

        response = await openai_client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )

        answer = response.choices[0].message.content.strip().lower()
        is_scam = answer.startswith("yes")
        return is_scam
    except Exception as e:
        logger.error(f"OpenAI scam detection error: {e}")
        return False


class ModerationButtons(View):
    def __init__(self, message, author):
        super().__init__(timeout=None)
        self.message = message
        self.author = author
        self.is_deleted = False

        self.add_item(
            Button(label="View", style=discord.ButtonStyle.link, url=message.jump_url)
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: Button):
        if self.is_deleted:
            await interaction.response.send_message("Already deleted.", ephemeral=True)
            return

        if interaction.user.guild_permissions.manage_messages:
            await self.message.delete()
            self.is_deleted = True
            await interaction.response.send_message("Message deleted.", ephemeral=True)
            button.disabled = True
            await interaction.message.edit(view=self)
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary)
    async def kick_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.kick_members:
            await self.author.kick(reason="Scam message detected by PhishHook.")
            await interaction.response.send_message(
                f"{self.author.name} was kicked.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.ban_members:
            await self.author.ban(reason="Scam message detected by PhishHook.")
            await interaction.response.send_message(
                f"{self.author.name} was banned.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)


class ScamDetection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.user
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user or message.author.bot or not message.guild:
            return

        bucket = self.cooldowns.get_bucket(message)
        if bucket.update_rate_limit():
            return

        author_roles = [role.id for role in message.author.roles]
        if any(role_id in whitelisted_role_ids for role_id in author_roles):
            return

        is_scam = await check_scam_with_openai(message.content)

        if is_scam:
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="🚨 Scam Message Detected",
                    description=f"From {message.author.mention} in {message.channel.mention}:\n\n{message.content}",
                    color=discord.Color.red(),
                )
                embed.set_footer(
                    text=f"User ID: {message.author.id} | Message ID: {message.id}"
                )
                view = ModerationButtons(message, message.author)
                await log_channel.send(embed=embed, view=view)

    @commands.command()
    async def start(self, ctx):
        await ctx.send("PhishHook Scam Detection is running.")


def setup(bot):
    bot.add_cog(ScamDetection(bot))
