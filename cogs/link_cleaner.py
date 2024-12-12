import discord
from discord.ext import commands
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


class LinkCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Add common tracking parameters to remove
        self.tracking_params = [
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
        ]

    def clean_url(self, url):
        """Remove tracking parameters from a URL."""
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

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Check for URLs in the message
        words = message.content.split()
        cleaned_links = []
        for word in words:
            if urlparse(word).scheme in ("http", "https"):
                cleaned_links.append(self.clean_url(word))

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
