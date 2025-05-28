import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from openai import AsyncOpenAI
from cachetools import TTLCache
from datetime import datetime, timezone, timedelta
import re
import httpx
import json
import os
import logging
from dotenv import load_dotenv

load_dotenv()

POGGERS_API_KEY = os.getenv("POGGERS_API_KEY")
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

SCAM_LIST_URL = "https://raw.githubusercontent.com/Discord-AntiScam/scam-links/refs/heads/main/list.txt"
SHORTENER_LIST_URL = (
    "https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list"
)
SHORTENER_WHITELIST = {"youtu.be", "discord.gg"}

logger = logging.getLogger(__name__)


def extract_urls(text):
    url_pattern = re.compile(
        r"""(?xi)
        \b(
            (?:https?://)?               
            (?:www\.)?                   
            [a-z0-9\-]+(\.[a-z]{2,})+    
            (?:/[^\s]*)?                 
        )
        """
    )
    return [m.group(0) for m in url_pattern.finditer(text)]


def get_domain(url):
    domain = url.split("//")[-1].split("/")[0].lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if ":" in domain:
        domain = domain.split(":")[0]
    return domain


async def fetch_txt_list(url):
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        return set(
            line.strip().lower()
            for line in r.text.splitlines()
            if line and not line.startswith("#")
        )


async def poggers_unshorten(url):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.poggers.win/api/ente/unshorten-url",
            json={"url": url, "key": POGGERS_API_KEY},
            timeout=12,
        )
        data = r.json()
        return (
            data.get("completeUrl")
            if data.get("success") and data.get("completeUrl")
            else url
        )


async def check_scam_with_openai(message: str) -> bool:
    try:
        system_prompt = (
            "You are a Discord bot designed to detect scam messages, but you should ONLY focus on scams related to finance, real estate, or crypto (including trading and investments). "
            "You will be given a message and must determine if it is a scam, but ONLY in these domains. "
            'Return a JSON object with the following format: {"is_scam": <boolean> }. The is_scam value should be true if the message is likely a scam in finance, real estate, or crypto, and false otherwise.\n\n'
            "Examples of scam characteristics to look for:\n"
            '- Promises of unrealistic financial or real estate gains (e.g., $100k in a week, guaranteed returns, "get rich quick" in real estate)\n'
            "- Requests for upfront payment, deposits, or a percentage of profits in these domains\n"
            "- Requests to contact via external platforms (Telegram, WhatsApp, etc.) for financial, real estate, or crypto matters\n"
            "- Vague or generic language about investing, trading, crypto, or real estate\n"
            "- Targeted recruitment with urgency or exclusivity for financial, real estate, or crypto opportunities\n"
            "- Use of URLs to move conversation off Discord for these purposes\n\n"
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
                None, reason="Timeout manually removed via Ducky."
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
            await self.author.kick(reason="Scam message detected by Ducky.")
            await interaction.response.send_message(
                f"{self.author.name} was kicked.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.ban_members:
            await self.author.ban(reason="Scam message detected by Ducky.")
            await interaction.response.send_message(
                f"{self.author.name} was banned.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)


class ScamDetection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scam_domains = set()
        self.shortener_domains = set()
        self._scam_cache = TTLCache(maxsize=1024, ttl=300)
        self.cooldowns = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.channel
        )
        self.allowed_category_ids = allowed_category_ids
        self.update_lists.start()

    def cog_unload(self):
        self.update_lists.cancel()

    @tasks.loop(hours=1)
    async def update_lists(self):
        self.scam_domains = await fetch_txt_list(SCAM_LIST_URL)
        self.shortener_domains = await fetch_txt_list(SHORTENER_LIST_URL)
        print(
            f"[ScamDetection] Loaded {len(self.scam_domains)} scam domains and {len(self.shortener_domains)} shortener domains."
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update_lists.is_running():
            self.update_lists.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        # Only skip bots and non-guild messages
        if message.author == self.bot.user or message.author.bot or not message.guild:
            return

        content = message.content.strip()
        urls = extract_urls(content)
        for url in urls:
            domain = get_domain(url)
            # Blocklist: always checked!
            if domain in self.shortener_domains and domain not in SHORTENER_WHITELIST:
                final_url = await poggers_unshorten(url)
                final_domain = get_domain(final_url)
                if any(
                    final_domain == scam or final_domain.endswith("." + scam)
                    for scam in self.scam_domains
                ):
                    await self._handle_scam(message, content, reason="domain")
                    return
            else:
                if any(
                    domain == scam or domain.endswith("." + scam)
                    for scam in self.scam_domains
                ):
                    await self._handle_scam(message, content, reason="domain")
                    return

        # --- AI check (stricter, for spammy/young/unknown users) ---
        if (
            message.channel.category
            and message.channel.category.id in self.allowed_category_ids
        ):
            account_age = datetime.now(timezone.utc) - message.author.created_at
            if account_age < timedelta(days=2 * 365):
                if len(message.content) >= 30:
                    bucket = self.cooldowns.get_bucket(message)
                    if not bucket.update_rate_limit():
                        author_roles = [role.id for role in message.author.roles]
                        if not any(
                            role_id in whitelisted_role_ids for role_id in author_roles
                        ):
                            if content in self._scam_cache:
                                is_scam = self._scam_cache[content]
                            else:
                                is_scam = await check_scam_with_openai(content)
                                self._scam_cache[content] = is_scam
                            if is_scam:
                                await self._handle_scam(message, content, reason="ai")

    async def _handle_scam(self, message, content, reason="unknown"):
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Failed to delete message: {e}")
        try:
            timeout_until = discord.utils.utcnow() + timedelta(days=1)
            await message.author.timeout(
                timeout_until, reason="Scam message detected by Ducky."
            )
        except Exception as e:
            logger.warning(f"Failed to timeout user: {e}")

        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="🚨 Scam Message Detected & Auto-Deleted",
                description=f"{message.author.mention} in {message.channel.mention} was **timed out for 1 day**.\n\n**Reason:** `{reason}`\n\n**Message content:**\n{content}",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"User ID: {message.author.id}")
            view = ModerationButtons(message.author)
            await log_channel.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(ScamDetection(bot))
