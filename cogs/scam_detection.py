import discord
from discord.ext import commands
from discord.ui import Button, View
from openai import AsyncOpenAI
from cachetools import TTLCache
from datetime import datetime, timezone, timedelta
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
allowed_category_ids = config.get("allowed_category_ids", [])

logger = logging.getLogger(__name__)


async def check_scam_with_openai(message: str) -> bool:
    try:
        system_prompt = (
            "You are a Discord bot designed to detect scam messages. You will be given a message and must determine if it is a scam. "
            'Return a JSON object with the following format: {"is_scam": <boolean>}. The value should be true if the message is likely a scam and false if it is not.\n\n'
            "Here are some examples of scam characteristics to look for (but are not limited to):\n\n"
            "-   Promises of unrealistic financial gains: Claims of earning large sums of money in short periods (e.g., $100k in a week) are highly suspicious.\n"
            "-   Requests for upfront payment or a percentage of profits: Asking for reimbursement or a cut of future earnings is a common scam tactic.\n"
            "-   Requests to contact via external platforms: Directing users to Telegram, WhatsApp, or other messaging apps is a red flag, especially when combined with other suspicious claims.\n"
            "-   Generic or vague language: Scammers often use general terms like 'crypto market,' 'online business,' or 'trading' without providing specific details.\n"
            "-   Targeted recruitment with selective criteria: Messages like 'I'll teach 10 people...' aims to create a sense of urgency and exclusivity.\n"
            "-   Use of URLs that lead to other Discord channels or outside websites for further communication: This is an attempt by the scammers to get the victim off Discord's chat filter or get the Discord token by impersonating the external website.\n\n"
            "Consider all these factors carefully and provide the most accurate assessment possible. DO NOT provide additional context or explanation, only the JSON object."
        )

        response = await openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        result = json.loads(content)
        is_scam = bool(result.get("is_scam", False))
        return is_scam
    except Exception as e:
        logger.error(f"OpenAI scam detection error: {e}")
        return False


class ModerationButtons(View):
    def __init__(self, author):
        super().__init__(timeout=None)
        self.author = author

    @discord.ui.button(label="Remove Timeout", style=discord.ButtonStyle.primary)
    async def remove_timeout(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        try:
            await self.author.timeout(
                None, reason="Timeout manually removed via PhishHook."
            )
            await interaction.response.send_message(
                f"Timeout removed for {self.author.name}.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to remove timeout: {e}", ephemeral=True
            )

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
        self._scam_cache = TTLCache(maxsize=1024, ttl=300)
        self.cooldowns = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.channel
        )
        self.allowed_category_ids = allowed_category_ids

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user or message.author.bot or not message.guild:
            return

        if (
            not message.channel.category
            or message.channel.category.id not in self.allowed_category_ids
        ):
            return

        account_age = datetime.now(timezone.utc) - message.author.created_at
        if account_age >= timedelta(days=2 * 365):
            return

        if len(message.content) < 30:
            return

        bucket = self.cooldowns.get_bucket(message)
        if bucket.update_rate_limit():
            return

        author_roles = [role.id for role in message.author.roles]
        if any(role_id in whitelisted_role_ids for role_id in author_roles):
            return

        content = message.content.strip()
        if content in self._scam_cache:
            is_scam = self._scam_cache[content]
        else:
            is_scam = await check_scam_with_openai(content)
            self._scam_cache[content] = is_scam

        if not is_scam:
            return

        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Failed to delete message: {e}")

        try:
            timeout_until = discord.utils.utcnow() + timedelta(days=1)
            await message.author.timeout(
                timeout_until, reason="Scam message detected by PhishHook."
            )
        except Exception as e:
            logger.warning(f"Failed to timeout user: {e}")

        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="🚨 Scam Message Detected & Auto-Deleted",
                description=f"{message.author.mention} in {message.channel.mention} was **timed out for 1 day**.\n\n**Message content:**\n{content}",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"User ID: {message.author.id}")
            view = ModerationButtons(message.author)
            await log_channel.send(embed=embed, view=view)


def setup(bot):
    bot.add_cog(ScamDetection(bot))
