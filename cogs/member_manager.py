from discord.ext import commands
from discord import Embed
import discord
import re
import logging

logger = logging.getLogger(__name__)


class MemberManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flag_pattern = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return

        if self.flag_pattern.search(str(payload.emoji)):
            # Remove straight from the IDs in the event: no message fetch and
            # no dependency on the user being in the member/user cache.
            try:
                message = self.bot.get_partial_messageable(
                    payload.channel_id
                ).get_partial_message(payload.message_id)
                await message.remove_reaction(
                    payload.emoji, discord.Object(id=payload.user_id)
                )
            except discord.HTTPException:
                pass

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


async def setup(bot):
    await bot.add_cog(MemberManager(bot))
