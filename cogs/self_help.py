import discord
from discord.ext import commands
import aiohttp
import os
import logging
import asyncio
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")

SELFHELP_CHANNEL_IDS = [1364139133794123807]
SOLVED_ONLY_CHANNEL_IDS = [1121126215995113552, 1383504546361380995]

SOLVED_TAG_IDS = {
    1364139133794123807: 1364276749826920538,
    1121126215995113552: 1138421917406204016,
    1383504546361380995: 1383506837252472982,
}


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.processed_threads = set()
        self.hint_sent_threads = set()
        self.pending_closures = {}
        self.solved_command_id = None
        self.unsolve_command_id = None

    async def post_setup(self):
        try:
            commands = await self.bot.tree.fetch_commands()
            for cmd in commands:
                if cmd.name == "solved":
                    self.solved_command_id = cmd.id
                elif cmd.name == "unsolve":
                    self.unsolve_command_id = cmd.id
        except Exception as e:
            logger.error(f"Failed to fetch command IDs: {e}")

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

    async def delayed_close_thread(self, thread: discord.Thread, delay: int = 1800):
        try:
            await asyncio.sleep(delay)
            await thread.edit(archived=True)
            await asyncio.sleep(1)
            await thread.edit(locked=True)
            self.pending_closures.pop(thread.id, None)
            await thread.send("This thread is now closed.")
        except asyncio.CancelledError:
            pass

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
            solved_hint = (
                f"</solved:{self.solved_command_id}>"
                if self.solved_command_id
                else "`/solved`"
            )
            await thread.send(
                f"{answer}\nIf your issue is resolved, please use the {solved_hint} command to close this thread."
            )

            async for msg in thread.history(limit=5):
                if msg.content.startswith("Analyzing your question"):
                    await msg.delete()
                    break

        elif thread.parent_id in SOLVED_ONLY_CHANNEL_IDS:
            solved_hint = (
                f"</solved:{self.solved_command_id}>"
                if self.solved_command_id
                else "`/solved`"
            )
            await thread.send(f"If your issue is solved, please use {solved_hint}")

    @app_commands.command(
        name="solved", description="Manually mark a thread as solved."
    )
    async def solved(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "This command must be used in a thread.", ephemeral=True
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You don't have permission to mark this thread as solved.",
                ephemeral=True,
            )
            return

        if thread.id in self.pending_closures:
            await interaction.response.send_message(
                "This thread has already been marked as solved and is pending closure.",
                ephemeral=True,
            )
            return

        close_time = int(datetime.now(timezone.utc).timestamp()) + 1800
        await interaction.response.send_message(
            f"Thread marked as solved. It will be closed in <t:{close_time}:R>.",
            ephemeral=False,
        )

        try:
            if isinstance(thread.parent, discord.ForumChannel):
                current_tags = list(thread.applied_tags or [])
                solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
                solved_tag = next(
                    (
                        tag
                        for tag in thread.parent.available_tags
                        if tag.id == solved_tag_id
                    ),
                    None,
                )
                if solved_tag and solved_tag not in current_tags:
                    current_tags.append(solved_tag)
                    await thread.edit(applied_tags=current_tags)
        except Exception as e:
            await thread.send(f"Error tagging thread: {e}")

        task = asyncio.create_task(self.delayed_close_thread(thread))
        self.pending_closures[thread.id] = task

    @app_commands.command(
        name="unsolve", description="Cancel auto-close and reopen thread"
    )
    async def unsolve(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "This command must be used in a thread.", ephemeral=True
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You don't have permission to unsolve this thread.", ephemeral=True
            )
            return

        if thread.id in self.pending_closures:
            self.pending_closures[thread.id].cancel()
            del self.pending_closures[thread.id]

        await thread.edit(locked=False, archived=False)

        if isinstance(thread.parent, discord.ForumChannel):
            solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
            if solved_tag_id:
                new_tags = [
                    tag for tag in thread.applied_tags if tag.id != solved_tag_id
                ]
                await thread.edit(applied_tags=new_tags)

        await interaction.response.send_message(
            "Thread has been reopened and unmarked as solved."
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
        if (
            isinstance(message.channel, discord.Thread)
            and message.channel.parent_id
            in SELFHELP_CHANNEL_IDS + SOLVED_ONLY_CHANNEL_IDS
            and message.channel.owner_id == message.author.id
            and message.id == message.channel.id
        ):
            await self.process_forum_thread(message.channel, initial_message=message)

        if message.author.bot:
            return

        if isinstance(message.channel, discord.Thread):
            lowered = message.content.lower()
            if (
                message.channel.owner_id == message.author.id
                and message.channel.id not in self.hint_sent_threads
                and any(
                    kw in lowered
                    for kw in [
                        "thank you",
                        "thanks",
                        "ty",
                        "solved",
                        "resolved",
                        "thx",
                        "appreciate",
                        "helped",
                        "fixed",
                        "tsym",
                    ]
                )
                and not any(
                    nk in lowered
                    for nk in [
                        "not",
                        "didn't",
                        "didnt",
                        "doesn't",
                        "doesnt",
                        "wasn't",
                        "wasnt",
                        "isn't",
                        "isnt",
                        "unsolved",
                        "didn’t",
                        "didnt",
                    ]
                )
            ):
                solved_hint = (
                    f"</solved:{self.solved_command_id}>"
                    if self.solved_command_id
                    else "`/solved`"
                )
                unsolve_hint = (
                    f"</unsolve:{self.unsolve_command_id}>"
                    if self.unsolve_command_id
                    else "`/unsolve`"
                )
                await message.reply(
                    f"-# If your issue is resolved, you can use the {solved_hint} command to close the thread. If not, use {unsolve_hint} to cancel.",
                    mention_author=True,
                )
                self.hint_sent_threads.add(message.channel.id)


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
