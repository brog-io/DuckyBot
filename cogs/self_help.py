import discord
from discord.ext import commands, tasks
import aiohttp
import os
import logging
import asyncio
from discord import ui
from dotenv import load_dotenv
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")

SELFHELP_CHANNEL_IDS = [1364139133794123807]
SOLVED_ONLY_CHANNEL_IDS = [1121126215995113552]

SUPPORT_ROLE_ID = 1364141260708909117
REACTION_TRIGGER = "❓"
MOD_ROLE_IDS = [
    950276268593659925,
    956466393514143814,
    950275266045960254,
]

SOLVED_TAG_IDS = {
    1364139133794123807: 1364276749826920538,
    1121126215995113552: 1138421917406204016,
}


class SupportView(ui.View):
    def __init__(
        self, thread_owner: int, parent_channel_id: int, show_help_button: bool = True
    ):
        super().__init__(timeout=None)
        self.thread_owner = thread_owner
        self.parent_channel_id = parent_channel_id
        if show_help_button:
            self.add_item(self.HelpButton())
        self.add_item(self.SolvedButton())

    def is_authorized(
        self, user: discord.Member, thread: discord.Thread = None
    ) -> bool:
        if user.id == self.thread_owner:
            return True

        # Simplified logic - check if user is thread owner
        if thread and user.id == thread.owner_id:
            return True

        if isinstance(user, discord.Member) and user.guild_permissions.manage_threads:
            return True

        if isinstance(user, discord.Member):
            for role in user.roles:
                if role.id in MOD_ROLE_IDS:
                    return True

        return False

    class HelpButton(ui.Button):
        def __init__(self):
            super().__init__(
                label="This didn't help",
                style=discord.ButtonStyle.danger,
                custom_id="support_button",
                row=0,
            )

        async def callback(self, interaction: discord.Interaction):
            view: SupportView = self.view
            thread = (
                interaction.channel
                if isinstance(interaction.channel, discord.Thread)
                else None
            )

            if not view.is_authorized(interaction.user, thread):
                await interaction.response.send_message(
                    "Only the thread creator or moderators can use this button.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"<@&{SUPPORT_ROLE_ID}> User still needs help in {interaction.channel.mention}",
                ephemeral=False,
            )
            self.disabled = True
            await interaction.message.edit(view=view)

    class SolvedButton(ui.Button):
        def __init__(self):
            super().__init__(
                label="Mark as Solved",
                style=discord.ButtonStyle.success,
                custom_id="mark_solved_button",
                row=0,
            )

        async def callback(self, interaction: discord.Interaction):
            view: SupportView = self.view
            thread = (
                interaction.channel
                if isinstance(interaction.channel, discord.Thread)
                else None
            )

            if not view.is_authorized(interaction.user, thread):
                await interaction.response.send_message(
                    "Only the thread creator or moderators can mark this as solved.",
                    ephemeral=True,
                )
                return

            if not isinstance(thread, discord.Thread):
                await interaction.response.send_message(
                    "This button must be used in a thread.", ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"Thread marked as solved and closed by {interaction.user.mention}.",
                ephemeral=False,
            )

            try:
                if isinstance(thread.parent, discord.ForumChannel):
                    current_tags = (
                        list(thread.applied_tags) if thread.applied_tags else []
                    )
                    forum_channel = thread.parent
                    solved_tag_id = SOLVED_TAG_IDS.get(forum_channel.id)
                    solved_tag = None
                    if solved_tag_id:
                        for tag in forum_channel.available_tags:
                            if tag.id == solved_tag_id:
                                solved_tag = tag
                                break
                    if solved_tag and solved_tag not in current_tags:
                        current_tags.append(solved_tag)
                    await thread.edit(
                        archived=True,
                        locked=True,
                        applied_tags=current_tags,
                    )
                    # Clean up data after archiving
                    view.parent_channel_id  # Access parent through view
                    if hasattr(self, "bot"):  # Check if we can access the cog
                        cog = self.bot.get_cog("SelfHelp")
                        if cog:
                            cog.cleanup_thread_data(thread.id)
                else:
                    await thread.edit(
                        archived=True,
                        locked=True,
                    )
                    # Clean up data after archiving
                    if hasattr(self, "bot"):  # Check if we can access the cog
                        cog = self.bot.get_cog("SelfHelp")
                        if cog:
                            cog.cleanup_thread_data(thread.id)
            except Exception as e:
                await thread.send(f"Error while closing thread: {e}")


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SupportView(thread_owner=0, parent_channel_id=0))
        self.processed_threads = set()
        self.thread_activity = {}
        self.check_stale_threads.start()

    def should_show_help_button(self, channel_id: int) -> bool:
        return channel_id not in SOLVED_ONLY_CHANNEL_IDS

    async def query_api(
        self, title: str, body: str = "", tags: list[str] = None
    ) -> str:
        tags_text = ", ".join(tags) if tags else "None"
        prompt = (
            f"Title: {title}\n"
            f"Tags: {tags_text}\n"
            f"Message: {body.strip() or 'No content provided.'}"
        )

        payload = {"query": prompt, "key": API_KEY}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.poggers.win/api/ente/docs-search", json=payload
                ) as resp:
                    if resp.status != 200:
                        return f"API error: {resp.status}"
                    data = await resp.json()
                    return (
                        data.get("answer", "No answer returned.")
                        if data.get("success")
                        else "Sorry, I couldn't find an answer."
                    )
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return "Sorry, I couldn't process your request due to a technical error."

    def cleanup_thread_data(self, thread_id: int):
        """Clean up data for archived/locked threads"""
        self.processed_threads.discard(thread_id)
        self.thread_activity.pop(thread_id, None)

    async def process_forum_thread(
        self, thread: discord.Thread, initial_message: discord.Message = None
    ):
        if thread.id in self.processed_threads:
            return
        self.processed_threads.add(thread.id)

        self.thread_activity[thread.id] = datetime.now(timezone.utc)

        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            await thread.send("Analyzing your question, please wait...")

            body = ""
            if initial_message:
                body = initial_message.content
            else:
                try:
                    # Add a small delay to ensure the initial message is available
                    await asyncio.sleep(0.5)
                    async for msg in thread.history(limit=1, oldest_first=True):
                        body = msg.content
                        break
                except Exception as e:
                    logger.error(f"Failed to fetch first message: {e}")

            tag_names = []
            if isinstance(thread.parent, discord.ForumChannel):
                # More efficient tag lookup
                tag_dict = {tag.id: tag.name for tag in thread.parent.available_tags}
                tag_names = [
                    tag_dict[tag.id]
                    for tag in (thread.applied_tags or [])
                    if tag.id in tag_dict
                ]

            query = thread.name or body
            context = (
                f"{body}\nTags: {', '.join(tag_names)}" if body or tag_names else ""
            )

            answer = await self.query_api(query, body, tag_names)
            show_help = self.should_show_help_button(thread.parent_id)
            await thread.send(
                answer,
                view=SupportView(
                    thread_owner=thread.owner_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=show_help,
                ),
            )

            # Clean up the "Analyzing" message
            try:
                async for msg in thread.history(limit=5):
                    if msg.content.startswith("Analyzing your question"):
                        await msg.delete()
                        break
            except Exception as e:
                logger.warning(f"Failed to delete analyzing message: {e}")

        elif thread.parent_id in SOLVED_ONLY_CHANNEL_IDS:
            await thread.send(
                "Use the button below to mark your question as solved.",
                view=SupportView(
                    thread_owner=thread.owner_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=False,
                ),
            )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if (
            thread.parent_id not in SELFHELP_CHANNEL_IDS
            and thread.parent_id not in SOLVED_ONLY_CHANNEL_IDS
        ):
            return
        await asyncio.sleep(1)
        await self.process_forum_thread(thread)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Fixed: Removed the impossible condition (message.id == message.channel.id)
        if (
            isinstance(message.channel, discord.Thread)
            and (
                message.channel.parent_id in SELFHELP_CHANNEL_IDS
                or message.channel.parent_id in SOLVED_ONLY_CHANNEL_IDS
            )
            and message.channel.owner_id == message.author.id
        ):
            await self.process_forum_thread(message.channel, initial_message=message)

        if not message.author.bot and isinstance(message.channel, discord.Thread):
            self.thread_activity[message.channel.id] = datetime.now(timezone.utc)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != REACTION_TRIGGER:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if message.author.bot:
            return

        # Fixed: Added fallback for empty message content
        thread_name = message.content[:90] if message.content.strip() else "Help Thread"
        thread = await message.create_thread(name=thread_name)

        user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(
            payload.user_id
        )

        await thread.send(
            f"<@{user.id}> created this thread from a message by <@{message.author.id}>"
        )

        answer = await self.query_api(message.content or "Help request")
        await thread.send(
            answer,
            view=SupportView(
                thread_owner=payload.user_id,
                parent_channel_id=channel.id,
            ),
        )

    @tasks.loop(minutes=30)
    async def check_stale_threads(self):
        now = datetime.now(timezone.utc)

        for thread_id, last_active in list(self.thread_activity.items()):
            thread = self.bot.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                # Remove invalid thread entries
                self.cleanup_thread_data(thread_id)
                continue

            # If thread is already archived/locked, clean up data
            if thread.archived or thread.locked:
                self.cleanup_thread_data(thread_id)
                continue

            if thread.parent_id not in SELFHELP_CHANNEL_IDS:
                continue

            inactive_days = (now - last_active).days
            try:
                if inactive_days == 3:
                    await thread.send(
                        f"🕒 <@{thread.owner_id}>, this thread hasn't had activity in a few days. If your issue is solved, press **Mark as Solved**. If not, just reply and I'll keep it open."
                    )
                elif inactive_days >= 6:
                    await thread.send(
                        "🔒 No response after reminder. This thread will now be closed."
                    )
                    await thread.edit(archived=True, locked=True)
                    # Clean up data after archiving
                    self.cleanup_thread_data(thread_id)
            except Exception as e:
                logger.warning(f"Failed to process stale thread {thread_id}: {e}")

    @check_stale_threads.before_loop
    async def before_check_stale_threads(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
