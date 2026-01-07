import discord
from discord.ext import commands
import aiohttp
import os
import logging
import asyncio
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")
AO_API_KEY = os.getenv("ANSWEROVERFLOW_API_KEY")

TARGET_GUILD_ID = 948937918347608085

SELFHELP_CHANNEL_IDS = [1364139133794123807, 1383504546361380995]

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
        self.docsearch_command_id = None

    def _is_target_guild(self, guild_id: int) -> bool:
        return guild_id == TARGET_GUILD_ID

    async def post_setup(self):
        # Fetch slash command IDs so we can reference them in messages.
        try:
            cmds = await self.bot.tree.fetch_commands()
            for cmd in cmds:
                if cmd.name == "solved":
                    self.solved_command_id = cmd.id
                elif cmd.name == "unsolve":
                    self.unsolve_command_id = cmd.id
                elif cmd.name == "docsearch":
                    self.docsearch_command_id = cmd.id
        except Exception as e:
            logger.error(f"Failed to fetch commands: {e}")

    ########################################################################
    # AnswerOverflow syncing
    ########################################################################

    def parse_message_link(self, link: str):
        parts = urlparse(link).path.strip("/").split("/")
        if len(parts) < 4:
            raise ValueError("Invalid Discord message link")
        return int(parts[1]), int(parts[2]), int(parts[3])

    async def update_answer_overflow_solution(
        self, thread_first_message_id: str, solution_message_id: str
    ):
        """
        thread_first_message_id must be the message id of the first post in the thread
        solution_message_id should be a message id string
        or '' to clear the solution
        """
        if not AO_API_KEY:
            logger.warning("No AnswerOverflow API key configured")
            return

        url = (
            f"https://www.answeroverflow.com/api/v1/messages/{thread_first_message_id}"
        )
        payload = {"solutionId": solution_message_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": AO_API_KEY,
                    "User-Agent": "Mozilla/5.0",
                },
            ) as resp:
                text = await resp.text()
                if resp.status >= 300:
                    logger.error(f"AO update failed {resp.status}: {text}")
                else:
                    logger.info("AO update ok")

    ########################################################################
    # AI doc search and reply generator
    ########################################################################

    async def query_api(
        self, title: str, body: str = "", tags: list[str] = None
    ) -> str:
        tags_text = ", ".join(tags) if tags else "None"
        prompt = f"Title: {title}\nTags: {tags_text}\nMessage: {body.strip() or 'No content provided.'}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.poggers.win/api/ente/docs-search",
                json={"query": prompt, "key": API_KEY},
            ) as resp:
                if resp.status != 200:
                    return f"API error: {resp.status}"
                data = await resp.json()
                if data.get("success"):
                    return data.get("answer", "No answer returned.")
                return "Sorry, I could not find an answer."

    async def process_forum_thread(
        self, thread: discord.Thread, initial_message: discord.Message = None
    ):
        if not self._is_target_guild(thread.guild.id):
            return

        if thread.id in self.processed_threads:
            return

        self.processed_threads.add(thread.id)

        analyzing = await thread.send("Analyzing your question, please wait...")

        body = initial_message.content if initial_message else ""

        if not body:
            async for msg in thread.history(limit=1, oldest_first=True):
                body = msg.content
                break

        tag_names = []
        if isinstance(thread.parent, discord.ForumChannel):
            all_tags = {t.id: t.name for t in thread.parent.available_tags}
            tag_names = [
                all_tags.get(t.id if hasattr(t, "id") else t, "")
                for t in (thread.applied_tags or [])
            ]

        answer = await self.query_api(thread.name or body, body, tag_names)

        solved_hint = (
            f"</solved:{self.solved_command_id}>"
            if self.solved_command_id
            else "`/solved`"
        )
        docsearch_hint = (
            f"</docsearch:{self.docsearch_command_id}>"
            if self.docsearch_command_id
            else "`/docsearch`"
        )

        response = (
            f"{answer}\n"
            f"-# If your issue is resolved, use {solved_hint} to mark this thread as solved. "
            f"Use {docsearch_hint} if you want to ask something else."
        )

        await analyzing.edit(content=response)

    ########################################################################
    # Auto close thread scheduling
    ########################################################################

    async def delayed_close_thread(self, thread: discord.Thread, delay: int = 1800):
        try:
            await asyncio.sleep(delay)
            await thread.send("This thread is now closed.")
            await thread.edit(archived=True, locked=True)
        except asyncio.CancelledError:
            pass
        finally:
            self.pending_closures.pop(thread.id, None)

    ########################################################################
    # Slash commands
    ########################################################################

    @app_commands.command(name="solved", description="Mark a thread as solved")
    @app_commands.describe(message_link="Link to the message that solved your problem")
    async def solved(self, interaction: discord.Interaction, message_link: str):
        thread = interaction.channel

        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "Use this inside a thread.", ephemeral=True
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You cannot mark this thread as solved.", ephemeral=True
            )
            return

        guild_id, _, solution_message_id = self.parse_message_link(message_link)

        if guild_id != thread.guild.id:
            await interaction.response.send_message(
                "Message must be from this server.", ephemeral=True
            )
            return

        # Get the first message in the thread, this is the AO message id
        async for msg in thread.history(limit=1, oldest_first=True):
            first_message_id = msg.id
            break

        close_time = int(datetime.now(timezone.utc).timestamp()) + 1800
        await interaction.response.send_message(
            f"Solved. Auto closing <t:{close_time}:R>."
        )

        task = asyncio.create_task(self.delayed_close_thread(thread))
        self.pending_closures[thread.id] = {"task": task}

        if isinstance(thread.parent, discord.ForumChannel):
            solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
            if solved_tag_id:
                tag = next(
                    (t for t in thread.parent.available_tags if t.id == solved_tag_id),
                    None,
                )
                if tag and tag not in thread.applied_tags:
                    await thread.edit(applied_tags=[*thread.applied_tags, tag])

        # Always send string solution id
        await self.update_answer_overflow_solution(
            str(first_message_id),
            str(solution_message_id),
        )

    @app_commands.command(name="unsolve", description="Remove solved status")
    async def unsolve(self, interaction: discord.Interaction):
        thread = interaction.channel

        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "Use this inside a thread.", ephemeral=True
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You cannot unsolve this thread.", ephemeral=True
            )
            return

        if thread.id in self.pending_closures:
            self.pending_closures[thread.id]["task"].cancel()
            self.pending_closures.pop(thread.id, None)

        await thread.edit(archived=False, locked=False)

        if isinstance(thread.parent, discord.ForumChannel):
            solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
            await thread.edit(
                applied_tags=[t for t in thread.applied_tags if t.id != solved_tag_id]
            )

        # Get the first message id again
        async for msg in thread.history(limit=1, oldest_first=True):
            first_message_id = msg.id
            break

        # AO requires empty string to clear solution
        await self.update_answer_overflow_solution(
            str(first_message_id),
            "",
        )

        await interaction.response.send_message("Thread unsolved.")

    ########################################################################
    # Events that trigger AI answers
    ########################################################################

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if not self._is_target_guild(thread.guild.id):
            return

        if thread.parent_id not in SELFHELP_CHANNEL_IDS:
            return

        await asyncio.sleep(1)
        await self.process_forum_thread(thread)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if (
            isinstance(message.channel, discord.Thread)
            and message.channel.parent_id in SELFHELP_CHANNEL_IDS
            and message.id == message.channel.id
        ):
            await self.process_forum_thread(message.channel, initial_message=message)


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
