import logging
import time
from datetime import timedelta
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

import discord
from discord.ext import commands

# Logger for debug/info messages about spam handling
logger = logging.getLogger(__name__)


class CrossChannelAntiSpam(commands.Cog):
    """
    Simple cross channel anti spam cog.

    Detects when a user sends the same content (text or attachments)
    in multiple channels in a short time window. When triggered:
    - Deletes the spam messages.
    - Attempts to timeout the user for a short duration.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Number of similar messages (same signature) within the window
        # that will trigger spam handling.
        self.threshold: int = 3

        # Time window in seconds to look back for repeated messages.
        self.window: int = 10

        # Store recent messages per user:
        # user_id -> deque of (timestamp, channel_id, signature, message)
        self.recent_messages: Dict[
            int, Deque[Tuple[float, int, str, discord.Message]]
        ] = defaultdict(lambda: deque(maxlen=30))

    def make_signature(self, message: discord.Message) -> str:
        """
        Create a signature for a message to detect repeats.

        For attachments, uses concatenated filenames.
        For text, uses normalized content (lowercased, stripped).
        """
        if message.attachments:
            names = ",".join(a.filename for a in message.attachments)
            return f"ATTACH:{names}"

        content = message.content.strip().lower()
        if not content:
            return ""
        return f"TEXT:{content}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listen to all messages and detect cross channel spam.
        """
        # Ignore bot messages and DMs.
        if message.author.bot:
            return
        if message.guild is None:
            return

        now = time.time()
        user_id = message.author.id
        signature = self.make_signature(message)

        # Ignore messages without a meaningful signature.
        if not signature:
            return

        history = self.recent_messages[user_id]

        # Remove messages outside the time window.
        while history and now - history[0][0] > self.window:
            history.popleft()

        # Count matching signatures and distinct channels.
        similar_count = 0
        channels_seen = set()

        for ts, ch_id, sig, msg in history:
            if sig == signature:
                similar_count += 1
                channels_seen.add(ch_id)

        # Add the current message to history.
        history.append((now, message.channel.id, signature, message))

        # Check spam condition:
        # 1) enough similar messages
        # 2) across more than one channel
        if similar_count + 1 >= self.threshold:
            all_channels = channels_seen | {message.channel.id}
            if len(all_channels) > 1:
                await self.handle_spam(message, signature, history)

    async def handle_spam(
        self,
        trigger_message: discord.Message,
        signature: str,
        history: Deque[Tuple[float, int, str, discord.Message]],
    ):
        """
        Delete matching spam messages and attempt to timeout the user.
        """
        member = trigger_message.guild.get_member(trigger_message.author.id)

        # Collect all recent messages with the same signature.
        to_delete = [m for (_, _, sig, m) in history if sig == signature]

        if trigger_message not in to_delete:
            to_delete.append(trigger_message)

        # Deduplicate by ID to avoid double deletes.
        unique_messages = {m.id: m for m in to_delete}.values()

        for msg in unique_messages:
            try:
                await msg.delete()
            except discord.HTTPException:
                # If deletion fails (missing perms or already deleted), skip it.
                logger.debug(
                    "Failed to delete spam message %s in #%s",
                    msg.id,
                    getattr(msg.channel, "name", "?"),
                )

        if member is None:
            logger.debug(
                "Cannot timeout user for spam, member not found in guild: %s",
                trigger_message.author.id,
            )
            return

        me = trigger_message.guild.me
        if me is None:
            logger.debug("Cannot resolve self member in guild for spam handling.")
            return

        # Check permission to timeout.
        if not me.guild_permissions.moderate_members:
            logger.debug(
                "Missing 'Moderate Members' permission to timeout user %s",
                member.id,
            )
            return

        # Check role hierarchy.
        if member.top_role >= me.top_role:
            logger.debug("Cannot timeout user %s due to role hierarchy.", member.id)
            return

        # Apply timeout.
        try:
            until = discord.utils.utcnow() + timedelta(minutes=5)
            await member.timeout(until, reason="Cross channel spam detected")
            logger.info(
                "Timed out user %s for cross channel spam.",
                member.id,
            )
        except Exception as e:
            logger.warning(
                "Failed to timeout user %s for spam: %s",
                member.id,
                e,
            )


async def setup(bot: commands.Bot):
    """
    Standard async setup function for discord.py 2.x extension loading.
    """
    await bot.add_cog(CrossChannelAntiSpam(bot))
