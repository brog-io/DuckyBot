from discord.ext import commands
import discord
import re
import logging

logger = logging.getLogger(__name__)


class MemberManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flag_pattern = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

    def remove_flags_from_name(self, name: str) -> str:
        return self.flag_pattern.sub("", name).strip()

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.display_name != after.display_name:
            sanitized_name = self.remove_flags_from_name(after.display_name)
            if sanitized_name != after.display_name:
                try:
                    await after.edit(nick=sanitized_name)
                    logger.info(
                        f"Removed flags from {after.display_name}, updated to: {sanitized_name}"
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"Could not change nickname for {after.name}: Missing permissions"
                    )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        welcome_channel_id = int(self.bot.config.get("welcome_channel_id", 0))
        if welcome_channel_id:
            channel = self.bot.get_channel(welcome_channel_id)
            if channel:
                try:
                    await channel.send(
                        f"Welcome to the server, {member.mention}! We're glad to have you here. <:lilducky:1069841394929238106>"
                    )
                    logger.info(f"Welcome message sent to {member.name}")
                except Exception as e:
                    logger.error(f"Error sending welcome message: {e}")
            else:
                logger.error(
                    f"Could not find welcome channel with ID {welcome_channel_id}"
                )
