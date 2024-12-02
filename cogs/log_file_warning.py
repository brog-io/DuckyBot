import discord
from discord.ext import commands
import logging


class LogFileWarning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from bots
        if message.author.bot:
            return

        # Check if any attachment has 'log' in its filename
        if message.attachments:
            for attachment in message.attachments:
                if "log" in attachment.filename.lower():
                    try:
                        await message.reply(
                            "⚠️ **Log File Security Notice**\n\n"
                            "It appears you've uploaded a log file. To protect your privacy and sensitive information:\n\n"
                            "• Log files may contain personal or sensitive data\n"
                            "• We recommend sending logs directly to our support team\n"
                            "• Please email logs to: support@ente.io\n\n"
                            "*This helps ensure the confidentiality of your information.*",
                            mention_author=True,
                        )
                        self.logger.info(
                            f"Log file uploaded by {message.author} in {message.channel}"
                        )
                    except discord.HTTPException as e:
                        self.logger.error(f"Failed to send log file warning: {e}")
                    break


async def setup(bot):
    await bot.add_cog(LogFileWarning(bot))
