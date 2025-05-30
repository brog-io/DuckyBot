import discord
from discord.ext import commands
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import re


class LinkCleaner(commands.Cog):
    """Removes tracking parameters from URLs posted in chat, but skips Discord media/CDN links."""

    DISCORD_MEDIA_HOSTS = {"media.discordapp.net", "cdn.discordapp.com"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tracking_params = [
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "ref",
            "source",
            "tk",
            "aff_id",
            "aff_sub",
            "aff_click_id",
            "click_id",
            "campaign_id",
            "ad_id",
            "placement_id",
            "creative_id",
            "network_id",
            "utm_referrer",
            "referrer",
            "sref",
            "referer",
            "track_id",
            "tag",
            "subid",
            "subid2",
            "subid3",
            "rurl",
            "sid",
        ]
        self.url_pattern = re.compile(r"https?://\S+")

    def is_valid_url(self, url: str) -> bool:
        """Returns True if the string is a valid URL."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def clean_url(self, url: str) -> str:
        """Removes tracking parameters from a URL, but skips Discord media/CDN URLs."""
        try:
            parsed = urlparse(url)
            # Skip cleaning for Discord media/CDN URLs
            if parsed.netloc in self.DISCORD_MEDIA_HOSTS:
                return url
            params = parse_qs(parsed.query)
            cleaned = {k: v for k, v in params.items() if k not in self.tracking_params}
            query = urlencode(cleaned, doseq=True)
            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    query,
                    parsed.fragment,
                )
            )
        except Exception:
            return url

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        urls = self.url_pattern.findall(message.content)
        cleaned_links = []

        for url in urls:
            url = url.rstrip(".,!?)")
            if self.is_valid_url(url):
                cleaned = self.clean_url(url)
                if cleaned != url:
                    cleaned_links.append(cleaned)

        if cleaned_links:
            view = discord.ui.View()
            for link in cleaned_links:
                view.add_item(discord.ui.Button(label="Open Cleaned Link", url=link))
            await message.reply(
                "Here are the cleaned links without tracking parameters:",
                mention_author=False,
                view=view,
            )

        await self.bot.process_commands(message)


async def setup(bot):
    await bot.add_cog(LinkCleaner(bot))
