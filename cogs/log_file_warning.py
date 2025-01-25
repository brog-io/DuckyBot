import discord
from discord.ext import commands
import logging
import os


class LogFileWarning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self.warning_messages = {}
        self.log_extensions = {
            ".zip",
            ".txt",
            ".log",
        }  # Define as a set for O(1) lookup

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from bots
        if message.author.bot:
            return

        # Check if any attachment has 'log' in its filename and has an acceptable extension
        if message.attachments:
            for attachment in message.attachments:
                filename = attachment.filename.lower()
                if "log" in filename and any(
                    filename.endswith(ext) for ext in self.log_extensions
                ):
                    try:
                        # Send reply and store the warning message
                        warning_msg = await message.reply(
                            "⚠️ **Log File Security Notice**\n\n"
                            "It appears you've uploaded a log file. To protect your privacy and sensitive information:\n\n"
                            "• Log files may contain personal or sensitive data\n"
                            "• We recommend sending logs directly to our support team\n"
                            "• Please email logs to: support@ente.io\n\n"
                            "*This helps ensure the confidentiality of your information.*",
                            mention_author=True,
                        )

                        # Store the warning message linked to the original message
                        self.warning_messages[message.id] = warning_msg

                        self.logger.info(
                            f"Log file uploaded by {message.author} in {message.channel}"
                        )
                    except discord.HTTPException as e:
                        self.logger.error(f"Failed to send log file warning: {e}")
                    break

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        # Check if the deleted message had a corresponding warning
        if message.id in self.warning_messages:
            try:
                # Delete the associated warning message
                warning_msg = self.warning_messages[message.id]
                await warning_msg.delete()

                # Remove the entry from the tracking dictionary
                del self.warning_messages[message.id]
            except discord.HTTPException:
                # Log if deletion fails (e.g., message already deleted)
                self.logger.info(f"Could not delete warning for message {message.id}")


async def setup(bot):
    await bot.add_cog(LogFileWarning(bot))
