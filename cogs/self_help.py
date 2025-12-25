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

# Load environment variables
load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")  # Your docs search API, already in use
AO_API_KEY = os.getenv("ANSWEROVERFLOW_API_KEY")  # New API key for AnswerOverflow

TARGET_GUILD_ID = 948937918347608085

SELFHELP_CHANNEL_IDS = [1364139133794123807, 1383504546361380995]
SOLVED_ONLY_CHANNEL_IDS = [1121126215995113552]

# Tag IDs to apply when solved
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

    ###########################################################################
    # Utility helpers
    ###########################################################################

    def _is_target_guild(self, guild_id: int) -> bool:
        """Check if the guild ID matches the target server."""
        return guild_id == TARGET_GUILD_ID

    async def post_setup(self):
        """Fetch slash command IDs so you can hint with </command:id> style."""
        try:
            commands_list = await self.bot.tree.fetch_commands()
            for cmd in commands_list:
                if cmd.name == "solved":
                    self.solved_command_id = cmd.id
                elif cmd.name == "unsolve":
                    self.unsolve_command_id = cmd.id
                elif cmd.name == "docsearch":
                    self.docsearch_command_id = cmd.id
        except Exception as e:
            logger.error(f"Failed to fetch command IDs: {e}")

    async def update_answer_overflow_solution(
        self, thread_message_id: str, solution_message_id: str | None
    ):
        """
        Call AnswerOverflow API to set or remove a solution.
        thread_message_id is the ID of the thread starter message.
        solution_message_id is the message that solved the thread, or None to unsolve.
        """
        if not AO_API_KEY:
            logger.warning("ANSWEROVERFLOW_API_KEY not set, skipping AO update")
            return

        url = f"https://www.answeroverflow.com/api/v1/messages/{thread_message_id}"
        payload = {"solutionId": solution_message_id}

        headers = {
            "Content-Type": "application/json",
            "x-api-key": AO_API_KEY,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 300:
                    text = await resp.text()
                    logger.error(f"AO update failed {resp.status}: {text}")
                else:
                    logger.info("AnswerOverflow solution update succeeded")

    def parse_message_link(self, link: str):
        """
        Parse a Discord message link and return (guild_id, channel_id, message_id).
        The format is:
        https://discord.com/channels/<guild>/<channel>/<message>
        """
        try:
            parts = urlparse(link).path.strip("/").split("/")
            # parts = ['channels', guild_id, channel_id, message_id]
            return int(parts[1]), int(parts[2]), int(parts[3])
        except Exception:
            raise ValueError("Invalid message link format")

    ###########################################################################
    # Existing docs search integration
    ###########################################################################

    async def query_api(
        self, title: str, body: str = "", tags: list[str] = None
    ) -> str:
        """Query your docs API."""
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

    ###########################################################################
    # Thread handling
    ###########################################################################

    async def delayed_close_thread(self, thread: discord.Thread, delay: int = 1800):
        """Close the thread after a delay unless cancelled."""
        try:
            await asyncio.sleep(delay)
            await thread.send("This thread is now closed.")
            await thread.edit(archived=True, locked=True)
            self.pending_closures.pop(thread.id, None)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Failed to close thread {thread.id}: {e}")

    async def process_forum_thread(
        self, thread: discord.Thread, initial_message: discord.Message = None
    ):
        """Run docs search and send a suggested reply."""
        if not self._is_target_guild(thread.guild.id):
            return

        if thread.id in self.processed_threads:
            return
        self.processed_threads.add(thread.id)

        if thread.parent_id in SELFHELP_CHANNEL_IDS:
            analyzing_msg = await thread.send("Analyzing your question, please wait...")

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
            docsearch_hint = (
                f"</docsearch:{self.docsearch_command_id}>"
                if self.docsearch_command_id
                else "`/docsearch`"
            )

            response_text = (
                f"{answer}\n-# If your issue is resolved, feel free to use the {solved_hint} command to close this thread. "
                f"If you'd like to ask me another question use {docsearch_hint}"
            )

            try:
                await analyzing_msg.edit(content=response_text)
            except Exception as e:
                logger.error(f"Failed to edit analyzing message: {e}")

        elif thread.parent_id in SOLVED_ONLY_CHANNEL_IDS:
            solved_hint = (
                f"</solved:{self.solved_command_id}>"
                if self.solved_command_id
                else "`/solved`"
            )
            await thread.send(
                f"Feel free to use {solved_hint} to mark your thread as solved if your question is answered."
            )

    ###########################################################################
    # Slash commands
    ###########################################################################

    @app_commands.command(
        name="solved",
        description="Mark a thread as solved and link the solving message.",
    )
    @app_commands.describe(message_link="Link to the message that solved your question")
    async def solved(self, interaction: discord.Interaction, message_link: str):
        """
        Mark a thread as solved.
        This now:
        1. Applies the solved tag
        2. Starts the close timer
        3. Updates AnswerOverflow
        """

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

        # Parse the solving message link
        try:
            guild_id, channel_id, solution_message_id = self.parse_message_link(
                message_link
            )
        except ValueError:
            await interaction.response.send_message(
                "Invalid message link. Please provide a full Discord message URL.",
                ephemeral=True,
            )
            return

        if guild_id != thread.guild.id:
            await interaction.response.send_message(
                "The solving message must be in this server.", ephemeral=True
            )
            return

        # Start the close timer display
        close_time = int(datetime.now(timezone.utc).timestamp()) + 1800
        await interaction.response.send_message(
            f"Thread marked as solved. It will be closed in <t:{close_time}:R>.",
            ephemeral=False,
        )
        timer_message = await interaction.original_response()

        # Add solved tag
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

        # Start delayed close
        task = asyncio.create_task(self.delayed_close_thread(thread))
        self.pending_closures[thread.id] = {
            "task": task,
            "timer_message_id": timer_message.id,
        }

        # Update AnswerOverflow using the thread starter message id (same as thread.id)
        await self.update_answer_overflow_solution(
            thread_message_id=str(thread.id),
            solution_message_id=str(solution_message_id),
        )

    @app_commands.command(
        name="unsolve", description="Cancel auto-close and remove thread solution"
    )
    async def unsolve(self, interaction: discord.Interaction):
        """
        Remove solved state from the thread and cancel AnswerOverflow solution.
        """

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

        # Cancel pending closure timer if present
        if thread.id in self.pending_closures:
            closure = self.pending_closures[thread.id]
            closure["task"].cancel()
            timer_message_id = closure.get("timer_message_id")
            if timer_message_id:
                try:
                    msg = await thread.fetch_message(timer_message_id)
                    await msg.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete timer message: {e}")
            del self.pending_closures[thread.id]

        await thread.edit(locked=False, archived=False)

        # Remove solved tag
        if isinstance(thread.parent, discord.ForumChannel):
            solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
            if solved_tag_id:
                new_tags = [
                    tag for tag in thread.applied_tags if tag.id != solved_tag_id
                ]
                await thread.edit(applied_tags=new_tags)

        # Remove AnswerOverflow solution
        await self.update_answer_overflow_solution(
            thread_message_id=str(thread.id),
            solution_message_id=None,
        )

        await interaction.response.send_message(
            "Thread has been reopened and unmarked as solved."
        )

    ###########################################################################
    # Event listeners
    ###########################################################################

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if not self._is_target_guild(thread.guild.id):
            return

        if (
            thread.parent_id not in SELFHELP_CHANNEL_IDS
            and thread.parent_id not in SOLVED_ONLY_CHANNEL_IDS
        ):
            return

        await asyncio.sleep(1)
        await self.process_forum_thread(thread)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Suggest using /solved automatically when the user thanks someone."""
        if (
            not hasattr(message, "guild")
            or message.guild is None
            or not self._is_target_guild(message.guild.id)
        ):
            return

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
                and message.id != message.channel.id
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
                        "reply",
                        "?",
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
                    f"-# <:cornerdownright:1439928795875512330> If your issue is resolved, you can use the {solved_hint} command to close the thread. If not, use {unsolve_hint} to cancel.",
                    mention_author=True,
                )
                self.hint_sent_threads.add(message.channel.id)


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
