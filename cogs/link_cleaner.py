import discord
from discord.ext import commands
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import re


class LinkCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Add common and extended tracking parameters to remove
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
        # Regex pattern to find URLs in text
        self.url_pattern = re.compile(r"https?://\S+")

    def is_valid_url(self, url):
        """Validate URL more thoroughly."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def clean_url(self, url):
        """Remove tracking parameters from a URL."""
        try:
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)

            # Remove tracking parameters
            cleaned_params = {
                key: value
                for key, value in query_params.items()
                if key not in self.tracking_params
            }

            # Reconstruct the URL without tracking parameters
            cleaned_query = urlencode(cleaned_params, doseq=True)
            cleaned_url = urlunparse(
                (
                    parsed_url.scheme,
                    parsed_url.netloc,
                    parsed_url.path,
                    parsed_url.params,
                    cleaned_query,
                    parsed_url.fragment,
                )
            )
            return cleaned_url
        except Exception as e:
            print(f"Error cleaning URL {url}: {e}")
            return url

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Find all URLs in the message content
        urls = self.url_pattern.findall(message.content)

        cleaned_links = []
        for url in urls:
            # Additional cleaning to remove potential punctuation at end of URL
            url = url.rstrip(".,!?)")

            if self.is_valid_url(url):
                cleaned_url = self.clean_url(url)
                # Only add if the URL actually changed
                if cleaned_url != url:
                    cleaned_links.append(cleaned_url)

        if cleaned_links:
            # Create buttons for each cleaned link
            view = discord.ui.View()
            for link in cleaned_links:
                button = discord.ui.Button(label="Open Cleaned Link", url=link)
                view.add_item(button)

            # Send the reply with cleaned links and buttons
            await message.reply(
                "Here are the cleaned links without tracking:",
                mention_author=False,
                view=view,
            )

        await self.bot.process_commands(message)


async def setup(bot):
    await bot.add_cog(LinkCleaner(bot))
