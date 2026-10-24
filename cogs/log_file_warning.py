import discord
from discord.ext import commands
import logging
from collections import OrderedDict

# How many original-message -> warning links to remember for cleanup.
MAX_TRACKED_WARNINGS = 500


class LogFileWarning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        # original message id -> (channel id, warning message id). Stores IDs
        # instead of whole Message objects and drops the oldest past the cap;
        # it used to keep every warning message in memory forever.
        self.warning_messages: OrderedDict[int, tuple[int, int]] = OrderedDict()
        self.log_extensions = {
            ".zip",
            ".txt",
            ".log",
        }  # Define as a set for O(1) lookup

        self.target_server_id = 948937918347608085

    @commands.Cog.listener()
    async def on_message(self, message):
        # Only work in the specified server
        if not message.guild or message.guild.id != self.target_server_id:
            return

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
                            "• Please email logs to: support@ente.com\n\n"
                            "*This helps ensure the confidentiality of your information.*",
                            mention_author=True,
                        )
                        # Store the warning message linked to the original message
                        self.warning_messages[message.id] = (
                            warning_msg.channel.id,
                            warning_msg.id,
                        )
                        while len(self.warning_messages) > MAX_TRACKED_WARNINGS:
                            self.warning_messages.popitem(last=False)
                        self.logger.info(
                            f"Log file uploaded by {message.author} in {message.channel}"
                        )
                    except discord.HTTPException as e:
                        self.logger.error(f"Failed to send log file warning: {e}")
                    break

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # Raw event: fires even though the bot no longer caches messages.
        if payload.guild_id != self.target_server_id:
            return

        # Check if the deleted message had a corresponding warning
        entry = self.warning_messages.pop(payload.message_id, None)
        if entry is None:
            return
        channel_id, warning_id = entry
        try:
            await (
                self.bot.get_partial_messageable(channel_id)
                .get_partial_message(warning_id)
                .delete()
            )
        except discord.HTTPException:
            # Log if deletion fails (e.g., message already deleted)
            self.logger.info(
                f"Could not delete warning for message {payload.message_id}"
            )


async def setup(bot):
    await bot.add_cog(LogFileWarning(bot))
