import logging
import time
from datetime import timedelta
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class CrossChannelAntiSpam(commands.Cog):
    """
    Simple cross channel anti spam cog.

    Detects when a user sends the same content (text or attachments)
    in multiple channels in a short time window. When triggered:
    - Deletes the spam messages.
    - Attempts to timeout the user for a short duration.
    - Sends a log message in the configured moderation log channel.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Number of similar messages (same signature) within the window
        # that will trigger spam handling.
        self.threshold: int = 3

        # Time window in seconds to look back for repeated messages.
        self.window: int = 10

        # Channel where moderation actions are reported.
        # Replace with your mod log channel ID if needed.
        self.log_channel_id: int = 953710561508618271

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
                await self.handle_spam(message, signature, history, all_channels)

    async def handle_spam(
        self,
        trigger_message: discord.Message,
        signature: str,
        history: Deque[Tuple[float, int, str, discord.Message]],
        channels_involved,
    ):
        """
        Delete matching spam messages, attempt to timeout the user,
        and report the action in the configured log channel.
        """
        guild = trigger_message.guild
        member = guild.get_member(trigger_message.author.id) if guild else None

        # Collect all recent messages with the same signature.
        to_delete = [m for (_, _, sig, m) in history if sig == signature]

        if trigger_message not in to_delete:
            to_delete.append(trigger_message)

        # Deduplicate by ID to avoid double deletes.
        unique_messages = list({m.id: m for m in to_delete}.values())

        deleted_count = 0
        for msg in unique_messages:
            try:
                await msg.delete()
                deleted_count += 1
            except discord.HTTPException:
                logger.debug(
                    "Failed to delete spam message %s in #%s",
                    msg.id,
                    getattr(msg.channel, "name", "?"),
                )

        # Attempt to timeout the member, if possible.
        timed_out = False
        timeout_error = None

        if member is not None and guild is not None:
            me = guild.me
            if me is not None and me.guild_permissions.moderate_members:
                if member.top_role < me.top_role:
                    try:
                        until = discord.utils.utcnow() + timedelta(minutes=5)
                        await member.timeout(
                            until,
                            reason="Cross channel spam detected",
                        )
                        timed_out = True
                        logger.info(
                            "Timed out user %s for cross channel spam.",
                            member.id,
                        )
                    except Exception as e:
                        timeout_error = str(e)
                        logger.warning(
                            "Failed to timeout user %s for spam: %s",
                            member.id,
                            e,
                        )
                else:
                    timeout_error = "Insufficient role hierarchy to timeout."
                    logger.debug(
                        "Cannot timeout user %s due to role hierarchy.",
                        member.id,
                    )
            else:
                timeout_error = "Missing Moderate Members permission or self member."
                logger.debug(
                    "Missing permission or self member to timeout user %s.",
                    member.id,
                )

        # Send a log message about this action.
        if guild is not None:
            log_channel = guild.get_channel(self.log_channel_id)

            # If not found in cache, you can optionally fetch:
            if log_channel is None:
                try:
                    log_channel = await guild.fetch_channel(self.log_channel_id)
                except Exception:
                    log_channel = None

            if log_channel is not None:
                try:
                    user_display = (
                        f"{member.mention} ({member.id})"
                        if member is not None
                        else f"{trigger_message.author} ({trigger_message.author.id})"
                    )
                    channels_list = ", ".join(
                        f"<#{cid}>" for cid in sorted(channels_involved)
                    )

                    description_lines = [
                        f"Cross channel spam detected.",
                        f"User: {user_display}",
                        f"Deleted messages: {deleted_count}",
                        f"Channels involved: {channels_list}",
                    ]

                    if timed_out:
                        description_lines.append(
                            "Action: User has been timed out for 5 minutes."
                        )
                    else:
                        description_lines.append("Action: Messages deleted.")
                        if timeout_error:
                            description_lines.append(
                                f"Note: Timeout not applied ({timeout_error})."
                            )

                    await log_channel.send("\n".join(description_lines))
                except Exception as e:
                    logger.warning(
                        "Failed to send spam log message in %s: %s",
                        self.log_channel_id,
                        e,
                    )

    async def cog_unload(self):
        """
        Clean up in memory state when the cog is unloaded.
        """
        self.recent_messages.clear()


async def setup(bot: commands.Bot):
    await bot.add_cog(CrossChannelAntiSpam(bot))
