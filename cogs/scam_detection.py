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

# =========================
# Environment and config
# =========================

POGGERS_API_KEY = os.getenv("POGGERS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in .env")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

config_file = "config.json"
with open(config_file, "r", encoding="utf-8") as f:
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

# =========================
# Helpers
# =========================


def extract_urls(text: str) -> list[str]:
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


def get_domain(url: str) -> str:
    domain = url.split("//")[-1].split("/")[0].lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if ":" in domain:
        domain = domain.split(":")[0]
    return domain


async def fetch_txt_list(url: str) -> set[str]:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        return {
            line.strip().lower()
            for line in r.text.splitlines()
            if line and not line.startswith("#")
        }


async def poggers_unshorten(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.poggers.win/api/ente/unshorten-url",
            json={"url": url, "key": POGGERS_API_KEY},
            timeout=12,
        )
        data = r.json()
        if data.get("success") and data.get("completeUrl"):
            return data["completeUrl"]
        return url


# =========================
# OpenAI scam scoring
# =========================


async def score_scam_with_openai(payload: dict) -> int:
    """
    Returns an integer risk score between 0 and 100.
    Higher means more likely to be a scam.
    """

    system_prompt = (
        "You are a Discord moderation system that evaluates whether a message "
        "is likely to be a scam. You must consider ALL common Discord scam types, including:\n"
        "- Crypto, finance, investment scams\n"
        "- Fake giveaways and Nitro scams\n"
        "- Impersonation (staff, support, friends)\n"
        "- Account takeover attempts\n"
        "- Malware or phishing links\n"
        "- Requests to move conversations off Discord\n"
        "- Urgency, exclusivity, or pressure tactics\n\n"
        "You will be given a JSON object containing the message content and metadata.\n"
        "Analyze all signals together and return ONLY a JSON object in this format:\n"
        '{"risk": <integer from 0 to 100>}\n\n'
        "Risk guidelines:\n"
        "0–20: Very unlikely to be a scam\n"
        "21–40: Low suspicion\n"
        "41–60: Moderately suspicious\n"
        "61–80: Likely scam\n"
        "81–100: Almost certainly a scam\n\n"
        "Do not include explanations or extra fields."
    )

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        risk = int(data.get("risk", 0))

        return max(0, min(risk, 100))

    except Exception as e:
        logger.error(f"OpenAI scam scoring error: {e}")
        return 0


# =========================
# Moderation UI
# =========================


class ModerationButtons(View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=None)
        self.author = author

    async def disable_all(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Remove Timeout", style=discord.ButtonStyle.primary)
    async def remove_timeout(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("No permission.", ephemeral=True)
            await self.disable_all(interaction)
            return

        await self.author.timeout(None, reason="Timeout manually removed.")
        await interaction.response.send_message(
            f"Timeout removed for {self.author.name}.", ephemeral=True
        )
        await self.disable_all(interaction)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary)
    async def kick_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.kick_members:
            await self.author.kick(reason="Scam detected.")
            await interaction.response.send_message(
                f"{self.author.name} was kicked.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)

        await self.disable_all(interaction)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.ban_members:
            await self.author.ban(reason="Scam detected.")
            await interaction.response.send_message(
                f"{self.author.name} was banned.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No permission.", ephemeral=True)

        await self.disable_all(interaction)


# =========================
# Cog
# =========================


class ScamDetection(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scam_domains: set[str] = set()
        self.shortener_domains: set[str] = set()
        self.score_cache = TTLCache(maxsize=1024, ttl=300)

        self.cooldowns = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.channel
        )

        self.update_lists.start()

    def cog_unload(self):
        self.update_lists.cancel()

    @tasks.loop(hours=1)
    async def update_lists(self):
        self.scam_domains = await fetch_txt_list(SCAM_LIST_URL)
        self.shortener_domains = await fetch_txt_list(SHORTENER_LIST_URL)
        logger.info(
            f"[ScamDetection] Loaded {len(self.scam_domains)} scam domains and "
            f"{len(self.shortener_domains)} shorteners."
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update_lists.is_running():
            self.update_lists.start()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content.strip()
        urls = extract_urls(content)

        # Hard domain blocklist check
        for url in urls:
            domain = get_domain(url)

            if domain in self.shortener_domains and domain not in SHORTENER_WHITELIST:
                final_url = await poggers_unshorten(url)
                final_domain = get_domain(final_url)

                if any(
                    final_domain == scam or final_domain.endswith("." + scam)
                    for scam in self.scam_domains
                ):
                    await self._handle_scam(message, content, 100)
                    return

            if any(
                domain == scam or domain.endswith("." + scam)
                for scam in self.scam_domains
            ):
                await self._handle_scam(message, content, 100)
                return

        # AI scoring
        if len(content) < 10:
            return

        bucket = self.cooldowns.get_bucket(message)
        if bucket.update_rate_limit():
            return

        if any(role.id in whitelisted_role_ids for role in message.author.roles):
            return

        account_age = datetime.now(timezone.utc) - message.author.created_at

        payload = {
            "content": content,
            "account_age_days": account_age.days,
            "has_urls": bool(urls),
            "urls": urls,
            "mentions_external_contact": bool(
                re.search(r"(telegram|whatsapp|signal|dm me|contact me)", content, re.I)
            ),
            "mentions_money": bool(
                re.search(
                    r"(crypto|btc|eth|investment|profit|returns|money)", content, re.I
                )
            ),
        }

        cache_key = json.dumps(payload, sort_keys=True)
        if cache_key in self.score_cache:
            risk = self.score_cache[cache_key]
        else:
            risk = await score_scam_with_openai(payload)
            self.score_cache[cache_key] = risk

        if risk >= 65:
            await self._handle_scam(message, content, risk)

    async def _handle_scam(self, message: discord.Message, content: str, risk: int):
        try:
            await message.delete()
        except Exception:
            pass

        try:
            await message.author.timeout(
                discord.utils.utcnow() + timedelta(days=1),
                reason=f"Scam detected (risk {risk}).",
            )
        except Exception:
            pass

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            return

        embed = discord.Embed(
            title="Scam Message Detected",
            description=(
                f"{message.author.mention} in {message.channel.mention}\n\n"
                f"**Risk score:** {risk}/100\n\n"
                f"**Message:**\n{content}"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"User ID: {message.author.id}")

        view = ModerationButtons(message.author)
        await log_channel.send(embed=embed, view=view)


# =========================
# Setup
# =========================


async def setup(bot: commands.Bot):
    await bot.add_cog(ScamDetection(bot))
