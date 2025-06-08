import discord
from discord.ext import commands, tasks
import aiohttp
import os
import logging
import asyncio
import json
from discord import ui
from datetime import datetime, timedelta
from dotenv import load_dotenv

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

ACTIVITY_FILE = "thread_activity.json"


class SupportView(ui.View):
    """
    View for support forum threads, includes buttons for marking solved and requesting more help.
    """

    def __init__(
        self, thread_owner: int, parent_channel_id: int, show_help_button: bool = True
    ):
        super().__init__(timeout=None)
        self.thread_owner = thread_owner
        self.parent_channel_id = parent_channel_id
        if show_help_button:
            self.add_item(self.HelpButton())
        self.add_item(self.SolvedButton())

    def is_authorized(self, user: discord.Member) -> bool:
        if user.id == self.thread_owner:
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
            if not view.is_authorized(interaction.user):
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
            if not view.is_authorized(interaction.user):
                await interaction.response.send_message(
                    "Only the thread creator or moderators can mark this as solved.",
                    ephemeral=True,
                )
                return
            thread = interaction.channel
            if not isinstance(thread, discord.Thread):
                await interaction.response.send_message(
                    "This button must be used in a thread.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "Thread marked as solved and closed.", ephemeral=False
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
                        archived=True, locked=True, applied_tags=current_tags
                    )
                else:
                    await thread.edit(archived=True, locked=True)
            except Exception as e:
                await thread.send(f"Error while closing thread: {e}")


class SelfHelp(commands.Cog):
    """
    Self-help forum automation cog for Discord support and solved-only channels.
    """

    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SupportView(thread_owner=0, parent_channel_id=0))
        self.thread_activity = self.load_activity_data()
        self.check_stale_threads.start()
        bot.loop.create_task(self.bootstrap_existing_threads())

    def load_activity_data(self):
        if os.path.exists(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE, "r") as f:
                    raw = json.load(f)
                    return {
                        int(tid): {
                            "last_active": datetime.fromisoformat(data["last_active"]),
                            "warned_at": (
                                datetime.fromisoformat(data["warned_at"])
                                if data.get("warned_at")
                                else None
                            ),
                        }
                        for tid, data in raw.items()
                    }
            except Exception as e:
                logger.warning(f"Failed to load activity data: {e}")
        return {}

    def save_activity_data(self):
        try:
            with open(ACTIVITY_FILE, "w") as f:
                json.dump(
                    {
                        str(tid): {
                            "last_active": data["last_active"].isoformat(),
                            "warned_at": (
                                data["warned_at"].isoformat()
                                if data["warned_at"]
                                else None
                            ),
                        }
                        for tid, data in self.thread_activity.items()
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.warning(f"Failed to save activity data: {e}")

    async def bootstrap_existing_threads(self):
        """
        Loads threads from self-help channels into activity tracking on bot startup.
        Only tracks activity for self-help channels, not solved-only.
        """
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for channel_id in SELFHELP_CHANNEL_IDS:
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.ForumChannel):
                    continue
                try:
                    for thread in guild.threads:
                        if (
                            thread.parent_id == channel.id
                            and not thread.locked
                            and thread.id not in self.thread_activity
                        ):
                            self.thread_activity[thread.id] = {
                                "last_active": (
                                    thread.last_message.created_at
                                    if thread.last_message
                                    else thread.created_at
                                ),
                                "warned_at": None,
                            }
                    self.save_activity_data()
                except Exception as e:
                    logger.warning(
                        f"Failed to bootstrap threads in {channel.name}: {e}"
                    )

    @tasks.loop(hours=6)
    async def check_stale_threads(self):
        """
        Bump or close inactive threads in self-help channels.
        Solved-only channels are not bumped or auto-closed.
        """
        now = datetime.utcnow()
        for thread_id, data in list(self.thread_activity.items()):
            thread = self.bot.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                continue
            if thread.locked:
                continue
            if thread.parent_id not in SELFHELP_CHANNEL_IDS:
                continue

            last_active = data["last_active"]
            warned_at = data.get("warned_at")

            if (now - last_active).days >= 7 and not warned_at:
                try:
                    await thread.send(
                        "🕒 This thread hasn’t had activity in a while. If your issue is solved, press **Mark as Solved**. If not, just reply and I’ll keep it open."
                    )
                    self.thread_activity[thread_id]["warned_at"] = now
                except Exception as e:
                    logger.warning(f"Failed to bump thread {thread_id}: {e}")

            elif warned_at and (now - warned_at).days >= 7:
                try:
                    await thread.send(
                        "🔒 No response after reminder. This thread will now be closed."
                    )
                    await thread.edit(archived=True, locked=True)
                    self.thread_activity.pop(thread_id, None)
                except Exception as e:
                    logger.warning(f"Failed to close thread {thread_id}: {e}")

        self.save_activity_data()

    async def query_api(self, query: str, extra: str = "") -> str:
        """
        Calls the Poggers docs search API for AI answers.
        """
        payload = {
            "query": f"{query}\n{extra}".strip(),
            "key": API_KEY,
        }
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

    async def process_forum_thread(
        self, thread: discord.Thread, initial_message: discord.Message = None
    ):
        """
        Handles thread startup messages and views for both self-help and solved-only channels.
        """
        # Solved-only: only show solved button, no bumps, no AI
        if thread.parent_id in SOLVED_ONLY_CHANNEL_IDS:
            await thread.send(
                "Use the button below to mark your question as solved.",
                view=SupportView(
                    thread_owner=thread.owner_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=False,
                ),
            )
            return

        # Self-help: normal AI/help workflow
        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            await thread.send("Analyzing your question, please wait...")

            body = ""
            if initial_message:
                body = initial_message.content
            else:
                try:
                    async for msg in thread.history(limit=1, oldest_first=True):
                        body = msg.content
                        break
                except Exception as e:
                    logger.error(f"Failed to fetch first message: {e}")

            tag_names = []
            if isinstance(thread.parent, discord.ForumChannel):
                all_tags = {tag.id: tag.name for tag in thread.parent.available_tags}
                tag_names = [
                    all_tags.get(t.id if hasattr(t, "id") else t, "")
                    for t in thread.applied_tags or []
                ]

            query = thread.name or body
            context = (
                f"{body}\nTags: {', '.join(filter(None, tag_names))}"
                if body or tag_names
                else ""
            )

            answer = await self.query_api(query, context)
            await thread.send(
                answer,
                view=SupportView(
                    thread_owner=thread.owner_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=True,
                ),
            )

            async for msg in thread.history(limit=5):
                if msg.content.startswith("Analyzing your question"):
                    await msg.delete()
                    break

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if (
            thread.parent_id in SELFHELP_CHANNEL_IDS
            or thread.parent_id in SOLVED_ONLY_CHANNEL_IDS
        ):
            await asyncio.sleep(1)
            await self.process_forum_thread(thread)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            isinstance(message.channel, discord.Thread)
            and (
                message.channel.parent_id in SELFHELP_CHANNEL_IDS
                or message.channel.parent_id in SOLVED_ONLY_CHANNEL_IDS
            )
            and message.channel.owner_id == message.author.id
            and message.id == message.channel.id
        ):
            await self.process_forum_thread(message.channel, initial_message=message)

        if message.author.bot:
            return
        if (
            message.channel.id in SELFHELP_CHANNEL_IDS
            or message.channel.id in SOLVED_ONLY_CHANNEL_IDS
        ):
            return

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

        thread = await message.create_thread(name=message.content[:90])

        user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(
            payload.user_id
        )

        await thread.send(
            f"<@{user.id}> created this thread from a message by <@{message.author.id}>"
        )

        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            answer = await self.query_api(message.content)
            await thread.send(answer, view=SupportView(thread_owner=payload.user_id))
        else:
            await thread.send(
                "Use the button below to mark your question as solved.",
                view=SupportView(
                    thread_owner=payload.user_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=False,
                ),
            )


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
