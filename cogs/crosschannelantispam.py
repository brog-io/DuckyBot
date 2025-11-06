import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

import discord
from discord.ext import commands


class CrossChannelAntiSpam(commands.Cog):
    """
    Simple anti spam cog that:
    - Tracks recent messages per user across all channels.
    - If a user sends the same message or same attachment in several channels
      within a short time, their spam is deleted.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # How many similar messages within the time window triggers spam
        self.threshold = 3

        # Time window in seconds to look back
        self.window = 10

        # user_id -> deque of (timestamp, channel_id, signature, message)
        self.recent_messages: Dict[
            int, Deque[Tuple[float, int, str, discord.Message]]
        ] = defaultdict(lambda: deque(maxlen=20))

    def make_signature(self, message: discord.Message) -> str:
        """
        Create a simple signature representing the message content or attachment.
        This allows us to detect copy-paste or repeated image spam across channels.
        """
        if message.attachments:
            # Use attachment file names as signature for image/file spam
            names = ",".join(a.filename for a in message.attachments)
            return f"ATTACH:{names}"
        # Normalize content: lowercase and strip whitespace
        return f"TEXT:{message.content.strip().lower()}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Monitor all messages and delete spammy cross-channel repeats.
        """
        if message.author.bot:
            return
        if not message.guild:
            return

        now = time.time()
        user_id = message.author.id
        signature = self.make_signature(message)

        # Ignore empty signatures (for example, blank messages)
        if not signature or signature == "TEXT:":
            return

        history = self.recent_messages[user_id]

        # Remove entries outside the time window
        while history and now - history[0][0] > self.window:
            history.popleft()

        # Count similar signatures across different channels
        similar_count = 0
        channels_seen = set()

        for ts, ch_id, sig, msg in history:
            if sig == signature:
                channels_seen.add(ch_id)
                similar_count += 1

        # Add current message to history
        history.append((now, message.channel.id, signature, message))

        # Check if spam condition reached:
        # same signature in at least `threshold` messages and in multiple channels
        if (
            similar_count + 1 >= self.threshold
            and len(channels_seen | {message.channel.id}) > 1
        ):
            # Delete all recent messages with this signature for this user
            to_delete = [m for (_, _, sig, m) in history if sig == signature]
            if message not in to_delete:
                to_delete.append(message)

            # Best effort delete
            for msg in set(to_delete):
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass

            try:
                # 5 minutes timeout as a simple reaction
                until = discord.utils.utcnow() + discord.timedelta(minutes=5)
                await message.author.timeout(
                    until, reason="Cross channel spam detected"
                )
            except Exception:
                # If timeout is not available or fails, ignore quietly
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CrossChannelAntiSpam(bot))
