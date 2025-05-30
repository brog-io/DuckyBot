from discord.ext import commands
import discord
from discord.ui import View, Button
import re
import logging

logger = logging.getLogger(__name__)


class MessageLinkButton(Button):
    def __init__(self, url: str):
        super().__init__(label="Go to Message", url=url, style=discord.ButtonStyle.link)


class MessageLinks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message_link_pattern = re.compile(
            r"https?:\/\/(?:.*\.)?discord\.com\/channels\/(\d+)\/(\d+)\/(\d+)"
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        matches = self.message_link_pattern.finditer(message.content)
        for match in matches:
            guild_id, channel_id, message_id = match.groups()

            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    continue

                referenced_message = await channel.fetch_message(int(message_id))
                if not referenced_message:
                    continue

                embed = discord.Embed(
                    description=referenced_message.content,
                    timestamp=referenced_message.created_at,
                    color=0xFFCD3F,
                )

                embed.set_author(
                    name=referenced_message.author.display_name,
                    icon_url=referenced_message.author.display_avatar.url,
                )

                if referenced_message.attachments:
                    if (
                        referenced_message.attachments[0]
                        .url.lower()
                        .endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                    ):
                        embed.set_image(url=referenced_message.attachments[0].url)

                view = View()
                view.add_item(MessageLinkButton(match.group(0)))

                await message.reply(embed=embed, view=view, mention_author=False)

            except Exception as e:
                logger.error(f"Error processing message link: {e}")


async def setup(bot):
    await bot.add_cog(MessageLinks(bot))
