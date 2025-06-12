import discord
from discord.ext import commands
import aiohttp
import os
import logging
import asyncio
from discord import ui
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

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

BUMP_TRACK_FILE = "bumped_threads.json"


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
        if thread and self.thread_owner == 0 and user.id == thread.owner_id:
            return True
        if thread and user.id == thread.owner_id:
            return True
        if isinstance(user, discord.Member) and user.guild_permissions.manage_threads:
            return True
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
        self.processed_threads = set()
        self.bumped_threads = self.load_bump_data()
        self.bump_file_lock = asyncio.Lock()
        bot.add_view(SupportView(thread_owner=0, parent_channel_id=0))
        bot.loop.create_task(self.thread_bump_task())

    def should_show_help_button(self, channel_id: int) -> bool:
        return channel_id not in SOLVED_ONLY_CHANNEL_IDS

    def load_bump_data(self):
        if Path(BUMP_TRACK_FILE).exists():
            try:
                with open(BUMP_TRACK_FILE, "r") as f:
                    return {
                        int(k): datetime.fromisoformat(v)
                        for k, v in json.load(f).items()
                    }
            except Exception as e:
                logger.warning(f"Failed to load bump data: {e}")
        return {}

    def save_bump_data(self):
        tmp_file = BUMP_TRACK_FILE + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump({k: v.isoformat() for k, v in self.bumped_threads.items()}, f)
            os.replace(tmp_file, BUMP_TRACK_FILE)
        except Exception as e:
            logger.error(f"Failed to save bump data: {e}")

    async def thread_bump_task(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(timezone.utc)
            for guild in self.bot.guilds:
                for channel_id in SELFHELP_CHANNEL_IDS:
                    channel = guild.get_channel(channel_id)
                    if not isinstance(channel, discord.ForumChannel):
                        continue
                    async for thread in channel.active_threads():
                        if thread.archived:
                            continue
                        async for message in thread.history(limit=1):
                            last_msg_time = message.created_at
                            break
                        else:
                            continue

                        time_since = now - last_msg_time
                        if time_since > timedelta(days=4):
                            if thread.id in self.bumped_threads:
                                try:
                                    await thread.send(
                                        "This thread was automatically closed due to inactivity."
                                    )
                                    if isinstance(thread.parent, discord.ForumChannel):
                                        current_tags = (
                                            list(thread.applied_tags)
                                            if thread.applied_tags
                                            else []
                                        )
                                        solved_tag_id = SOLVED_TAG_IDS.get(
                                            thread.parent.id
                                        )
                                        solved_tag = next(
                                            (
                                                tag
                                                for tag in thread.parent.available_tags
                                                if tag.id == solved_tag_id
                                            ),
                                            None,
                                        )
                                        if (
                                            solved_tag
                                            and solved_tag not in current_tags
                                        ):
                                            current_tags.append(solved_tag)
                                        await thread.edit(
                                            archived=True,
                                            locked=True,
                                            applied_tags=current_tags,
                                        )
                                    else:
                                        await thread.edit(archived=True, locked=True)
                                except Exception as e:
                                    logger.error(
                                        f"Auto-close error for thread {thread.id}: {e}"
                                    )
                                self.bumped_threads.pop(thread.id, None)
                                self.save_bump_data()
                        elif (
                            time_since > timedelta(days=3)
                            and thread.id not in self.bumped_threads
                        ):
                            await thread.send(
                                "Need more help? Press 'This didn't help' to ping the support team or 'Mark as Solved' if your issue is resolved.",
                                view=SupportView(
                                    thread_owner=thread.owner_id,
                                    parent_channel_id=thread.parent_id,
                                    show_help_button=self.should_show_help_button(
                                        thread.parent_id
                                    ),
                                ),
                            )
                            self.bumped_threads[thread.id] = now
                            self.save_bump_data()
            await asyncio.sleep(3600)

    async def query_api(
        self, title: str, body: str = "", tags: list[str] = None
    ) -> str:
        tags_text = ", ".join(tags) if tags else "None"
        prompt = f"Title: {title}\nTags: {tags_text}\nMessage: {body.strip() or 'No content provided.'}"
        payload = {"query": prompt, "key": API_KEY}
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
        if thread.id in self.processed_threads:
            return
        self.processed_threads.add(thread.id)

        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            await thread.send("Analyzing your question, please wait...")
            body = initial_message.content if initial_message else ""
            if not body:
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

            async for msg in thread.history(limit=5):
                if msg.content.startswith("Analyzing your question"):
                    await msg.delete()
                    break

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
        answer = await self.query_api(message.content)
        await thread.send(
            answer,
            view=SupportView(
                thread_owner=payload.user_id,
                parent_channel_id=channel.id,
            ),
        )


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
