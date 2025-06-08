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
        for role in getattr(user, "roles", []):
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
                    solved_tag = next(
                        (
                            tag
                            for tag in forum_channel.available_tags
                            if tag.id == solved_tag_id
                        ),
                        None,
                    )
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
    def __init__(self, bot):
        self.bot = bot
        self.thread_activity = self.load_activity_data()
        self.check_stale_threads.start()
        bot.add_view(SupportView(thread_owner=0, parent_channel_id=0))
        bot.loop.create_task(self.bootstrap_existing_threads())

    def load_activity_data(self):
        if os.path.exists(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE, "r") as f:
                    raw = json.load(f)
                    fixed = {}
                    for tid, ts in raw.items():
                        try:
                            fixed[int(tid)] = datetime.fromisoformat(ts)
                        except Exception as e:
                            logger.warning(
                                f"Skipping thread {tid} due to invalid timestamp: {ts}"
                            )
                    return fixed
            except Exception as e:
                logger.warning(f"Failed to load activity data: {e}")
        return {}

    def save_activity_data(self):
        try:
            with open(ACTIVITY_FILE, "w") as f:
                json.dump(
                    {
                        str(tid): ts.isoformat()
                        for tid, ts in self.thread_activity.items()
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.warning(f"Failed to save activity data: {e}")

    async def query_api(self, query: str, extra: str = "") -> str:
        payload = {"query": f"{query}\n{extra}".strip(), "key": API_KEY}
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

    async def bootstrap_existing_threads(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for channel_id in SELFHELP_CHANNEL_IDS:
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.ForumChannel):
                    continue
                for thread in guild.threads:
                    if thread.parent_id == channel.id and not thread.locked:
                        self.thread_activity[thread.id] = (
                            thread.last_message.created_at
                            if thread.last_message
                            else thread.created_at
                        )
        self.save_activity_data()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not isinstance(message.channel, discord.Thread):
            return
        if message.author.bot:
            return
        if message.channel.parent_id not in SELFHELP_CHANNEL_IDS:
            return
        self.thread_activity[message.channel.id] = datetime.utcnow()
        self.save_activity_data()

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if (
            thread.parent_id in SELFHELP_CHANNEL_IDS
            or thread.parent_id in SOLVED_ONLY_CHANNEL_IDS
        ):
            await asyncio.sleep(1)
            await self.process_forum_thread(thread)

    async def process_forum_thread(
        self, thread: discord.Thread, initial_message: discord.Message = None
    ):
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
        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            await thread.send("Analyzing your question, please wait...")
            try:
                if not initial_message:
                    async for msg in thread.history(limit=1, oldest_first=True):
                        initial_message = msg
                        break
                body = initial_message.content if initial_message else ""
            except Exception as e:
                logger.error(f"Failed to fetch first message: {e}")
                body = ""
            tags = (
                [
                    t.name
                    for t in getattr(thread.parent, "available_tags", [])
                    if hasattr(t, "name")
                ]
                if isinstance(thread.parent, discord.ForumChannel)
                else []
            )
            answer = await self.query_api(
                thread.name or body, f"{body}\nTags: {', '.join(tags)}"
            )
            await thread.send(
                answer,
                view=SupportView(
                    thread_owner=thread.owner_id, parent_channel_id=thread.parent_id
                ),
            )
            async for msg in thread.history(limit=5):
                if msg.content.startswith("Analyzing your question"):
                    await msg.delete()
                    break

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
        await self.process_forum_thread(thread, initial_message=message)

    @tasks.loop(minutes=30)
    async def check_stale_threads(self):
        now = datetime.utcnow()
        for thread_id, last_active in list(self.thread_activity.items()):
            thread = self.bot.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                continue
            if thread.locked or thread.parent_id not in SELFHELP_CHANNEL_IDS:
                continue
            inactive_time = (now - last_active).days
            try:
                if inactive_time == 3:
                    await thread.send(
                        f"🕒 <@{thread.owner_id}>, this thread hasn’t had activity in a few days. "
                        "If your issue is solved, press **Mark as Solved**. If not, just reply and I’ll keep it open."
                    )
                elif inactive_time >= 6:
                    await thread.send(
                        "🔒 No response after reminder. This thread will now be closed."
                    )
                    await thread.edit(archived=True, locked=True)
                    self.thread_activity.pop(thread_id, None)
            except Exception as e:
                logger.warning(f"Thread {thread_id} activity check failed: {e}")
        self.save_activity_data()


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
