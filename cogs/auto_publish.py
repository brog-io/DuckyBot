import discord
from discord.ext import commands
import logging

# Set up logging
logger = logging.getLogger(__name__)


class AutoPublish(commands.Cog):
    """
    A cog that automatically publishes messages in announcement channels.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages sent by bots, including the bot itself
        if message.author.bot:
            return

        # Check if the channel is an announcement channel
        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.is_news()
        ):
            try:
                # Attempt to publish the message
                await message.publish()
                logger.info(
                    f"Published message in {message.channel.name} (ID: {message.channel.id})"
                )
            except discord.Forbidden:
                logger.warning(
                    f"Missing permissions to publish messages in {message.channel.name} (ID: {message.channel.id})"
                )
            except discord.HTTPException as e:
                logger.error(
                    f"Failed to publish message in {message.channel.name} (ID: {message.channel.id}): {str(e)}"
                )


async def setup(bot):
    await bot.add_cog(AutoPublish(bot))
