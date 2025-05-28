import discord
from discord.ext import commands, tasks
import re
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

POGGERS_API_KEY = os.getenv("POGGERS_API_KEY")

SCAM_LIST_URL = "https://raw.githubusercontent.com/Discord-AntiScam/scam-links/refs/heads/main/list.txt"
SHORTENER_LIST_URL = (
    "https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list"
)

# Set of shortener domains you want to always allow and never unshorten/check
SHORTENER_WHITELIST = {"youtu.be", "discord.gg"}


def extract_urls(text):
    url_pattern = re.compile(r"https?://[^\s>]+")
    return url_pattern.findall(text)


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
        # Use 'completeUrl' for final destination
        return (
            data.get("completeUrl")
            if data.get("success") and data.get("completeUrl")
            else url
        )


class ScamLinkDetector(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scam_domains = set()
        self.shortener_domains = set()
        self.update_lists.start()

    def cog_unload(self):
        self.update_lists.cancel()

    @tasks.loop(hours=1)
    async def update_lists(self):
        self.scam_domains = await fetch_txt_list(SCAM_LIST_URL)
        self.shortener_domains = await fetch_txt_list(SHORTENER_LIST_URL)
        print(
            f"[ScamLinkDetector] Loaded {len(self.scam_domains)} scam domains and {len(self.shortener_domains)} shortener domains."
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update_lists.is_running():
            self.update_lists.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.content:
            return

        urls = extract_urls(message.content)
        for url in urls:
            domain = get_domain(url)

            if domain in self.shortener_domains:
                # Whitelisted shorteners: do nothing
                if domain in SHORTENER_WHITELIST:
                    continue

                # Not whitelisted: unshorten and check
                final_url = await poggers_unshorten(url)
                final_domain = get_domain(final_url)
                # Check if final domain is scam
                if any(
                    final_domain == scam or final_domain.endswith("." + scam)
                    for scam in self.scam_domains
                ):
                    try:
                        await message.delete()
                        print(
                            f"[ScamLinkDetector] Blocked {message.author} for scam domain after unshorten: {final_url}"
                        )
                    except Exception as e:
                        print(f"[ScamLinkDetector] Failed to delete message: {e}")
                    return  # Stop after first hit

            else:
                # Not a shortener, check directly
                if any(
                    domain == scam or domain.endswith("." + scam)
                    for scam in self.scam_domains
                ):
                    try:
                        await message.delete()
                        print(
                            f"[ScamLinkDetector] Blocked {message.author} for direct scam link: {url}"
                        )
                    except Exception as e:
                        print(f"[ScamLinkDetector] Failed to delete message: {e}")
                    return  # Stop after first hit


async def setup(bot):
    await bot.add_cog(ScamLinkDetector(bot))
