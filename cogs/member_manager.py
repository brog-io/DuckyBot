import logging
import re

import discord
from cachetools import TTLCache
from discord.ext import commands

logger = logging.getLogger(__name__)

# Discord's nickname length limit.
MAX_NICK_LENGTH = 32


class MemberManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flag_pattern = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
        # Members already handled on sight (join / message). Stops a failed or
        # skipped rename from being retried on every message they send.
        self._checked: TTLCache = TTLCache(maxsize=10_000, ttl=6 * 3600)

    def remove_flags_from_name(self, name: str) -> str:
        """Strip flag emoji and tidy the whitespace they leave behind."""
        cleaned = self.flag_pattern.sub("", name)
        return re.sub(r"\s{2,}", " ", cleaned).strip()[:MAX_NICK_LENGTH]

    def _can_rename(self, member: discord.Member) -> bool:
        guild = member.guild
        me = guild.me
        if member.bot or me is None or member.id == guild.owner_id:
            return False
        if not me.guild_permissions.manage_nicknames:
            return False
        # Discord refuses renames of anyone at or above the bot's top role;
        # checking first avoids a pointless API call.
        return member.top_role < me.top_role

    async def sanitize_member(self, member: discord.Member) -> None:
        if not self.flag_pattern.search(member.display_name):
            return
        if not self._can_rename(member):
            return

        # Usernames can't contain emoji, so they're a safe fallback when the
        # name was nothing but flags.
        sanitized = self.remove_flags_from_name(member.display_name) or member.name
        if sanitized == member.display_name:
            return
        try:
            await member.edit(nick=sanitized, reason="Flag emoji in display name")
            logger.info(
                f"Removed flags from {member.display_name}, updated to: {sanitized}"
            )
        except discord.Forbidden:
            logger.warning(
                f"Could not change nickname for {member.name}: Missing permissions"
            )
        except discord.HTTPException as e:
            logger.warning(f"Could not change nickname for {member.name}: {e}")

    async def _sanitize_once(self, member: discord.Member) -> None:
        if member.id in self._checked:
            return
        self._checked[member.id] = True
        await self.sanitize_member(member)

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
            await self.sanitize_member(after)

    # The bot no longer downloads the full member list, so Discord only sends
    # on_member_update for members it has already seen. These two catch
    # everyone else: new members on arrival, existing ones when they post.
    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self._sanitize_once(member)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild and isinstance(message.author, discord.Member):
            await self._sanitize_once(message.author)


async def setup(bot):
    await bot.add_cog(MemberManager(bot))
