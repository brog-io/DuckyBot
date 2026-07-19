import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

INTRO_CHANNEL_ID = 1380262760994177135
WAVE_EMOJI = "👋"
THREAD_AUTO_ARCHIVE_MINUTES = 10080  # 7 days

STARTER_MESSAGE = (
    "Welcome, {mention}! {wave} Say hi to them in this thread.\n\n"
    "If you'd like to share more, here are some ideas:\n"
    "- What are you working on or building?\n"
    "- How did you find the server?\n"
    "- What do you hope to get out of the community?"
)


class Introductions(commands.Cog):
    """Creates a discussion thread for each introduction posted in the intro channel.

    Replies posted directly in the channel (outside threads) are removed and the
    author is pointed to the relevant thread, keeping the channel to one message
    per person.
    """

    def __init__(self, bot):
        self.bot = bot
        self.intro_channel_id = INTRO_CHANNEL_ID

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != self.intro_channel_id:
            return
        # Threads under the intro channel have their own channel IDs, so this
        # only fires for top-level messages in the channel itself.

        # System messages (e.g. thread-created notices) shouldn't get threads
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        if message.type is discord.MessageType.reply:
            await self._redirect_reply(message)
            return

        await self._handle_intro(message)

    async def _handle_intro(self, message: discord.Message):
        try:
            await message.add_reaction(WAVE_EMOJI)
        except discord.HTTPException:
            logger.warning("Failed to react to intro %s", message.id)

        thread_name = f"{WAVE_EMOJI} {message.author.display_name}'s intro"[:100]
        try:
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except discord.HTTPException:
            logger.exception("Failed to create thread for intro %s", message.id)
            return

        try:
            await thread.send(
                STARTER_MESSAGE.format(mention=message.author.mention, wave=WAVE_EMOJI)
            )
        except discord.HTTPException:
            logger.warning("Failed to send starter message in thread %s", thread.id)

        logger.info(
            "Created intro thread %s for %s", thread.id, message.author.display_name
        )

    async def _redirect_reply(self, message: discord.Message):
        """Delete a channel-level reply and point the author to the intro's thread."""
        thread_mention = None
        ref = message.reference
        if ref and ref.message_id:
            thread = message.channel.get_thread(ref.message_id)
            if thread:
                thread_mention = thread.mention

        try:
            await message.delete()
        except discord.HTTPException:
            logger.warning("Failed to delete channel reply %s", message.id)
            return

        destination = thread_mention or "their intro thread"
        try:
            await message.author.send(
                f"Hey! To keep the introductions channel tidy, replies go in "
                f"threads. Please post your message in {destination} instead."
            )
        except discord.HTTPException:
            pass  # DMs closed; message was already removed


async def setup(bot):
    await bot.add_cog(Introductions(bot))
