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
API_KEY = os.getenv("POGGERS_API_KEY")
AO_API_KEY = os.getenv("ANSWEROVERFLOW_API_KEY")

TARGET_GUILD_ID = 948937918347608085

SELFHELP_CHANNEL_IDS = [1364139133794123807, 1383504546361380995]
SOLVED_ONLY_CHANNEL_IDS = [1121126215995113552]

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
    # Helpers
    ###########################################################################

    def _is_target_guild(self, guild_id: int) -> bool:
        return guild_id == TARGET_GUILD_ID

    async def post_setup(self):
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

    def parse_message_link(self, link: str):
        """
        Parse a Discord message link of format:
        https://discord.com/channels/<guild>/<channel>/<message>
        """
        parts = urlparse(link).path.strip("/").split("/")
        if len(parts) < 4:
            raise ValueError("Invalid Discord message link")
        return int(parts[1]), int(parts[2]), int(parts[3])

    async def update_answer_overflow_solution(
        self, thread_message_id: str, solution_message_id: str | None
    ):
        """
        thread_message_id must be the FIRST MESSAGE in the thread.
        solution_message_id must be a reply in the same thread.
        To unsolve, pass solution_message_id=None.
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

        logger.info(
            f"AO update => thread_message_id={thread_message_id}, solution_message_id={solution_message_id}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status >= 300:
                    logger.error(f"AO update failed {resp.status}: {text}")
                else:
                    logger.info(f"AO update success: {text}")

    ###########################################################################
    # Docs search
    ###########################################################################

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
                    else "Sorry, I could not find an answer."
                )

    ###########################################################################
    # Thread autoclose
    ###########################################################################

    async def delayed_close_thread(self, thread: discord.Thread, delay: int = 1800):
        try:
            await asyncio.sleep(delay)
            await thread.send("This thread is now closed.")
            await thread.edit(archived=True, locked=True)
            self.pending_closures.pop(thread.id, None)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Failed to close thread {thread.id}: {e}")

    ###########################################################################
    # Slash commands
    ###########################################################################

    @app_commands.command(
        name="solved",
        description="Mark a thread as solved. Requires the link of the solving message.",
    )
    @app_commands.describe(message_link="Link to the message that solved your problem")
    async def solved(self, interaction: discord.Interaction, message_link: str):
        thread = interaction.channel

        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "This command must be used inside a thread.",
                ephemeral=True,
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You do not have permission to mark this thread as solved.",
                ephemeral=True,
            )
            return

        if thread.id in self.pending_closures:
            await interaction.response.send_message(
                "This thread is already marked as solved.",
                ephemeral=True,
            )
            return

        try:
            guild_id, channel_id, solution_message_id = self.parse_message_link(
                message_link
            )
        except ValueError:
            await interaction.response.send_message(
                "Invalid message link.",
                ephemeral=True,
            )
            return

        if guild_id != thread.guild.id:
            await interaction.response.send_message(
                "That message is not in this server.",
                ephemeral=True,
            )
            return

        close_time = int(datetime.now(timezone.utc).timestamp()) + 1800
        await interaction.response.send_message(
            f"Thread marked as solved. It will close in <t:{close_time}:R>."
        )
        timer_message = await interaction.original_response()

        # Add solved tag
        try:
            if isinstance(thread.parent, discord.ForumChannel):
                solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
                if solved_tag_id:
                    tag = next(
                        (
                            t
                            for t in thread.parent.available_tags
                            if t.id == solved_tag_id
                        ),
                        None,
                    )
                    if tag and tag not in thread.applied_tags:
                        await thread.edit(applied_tags=[*thread.applied_tags, tag])
        except Exception as e:
            logger.error(f"Tagging failed: {e}")

        # Start close timer
        task = asyncio.create_task(self.delayed_close_thread(thread))
        self.pending_closures[thread.id] = {
            "task": task,
            "timer_message_id": timer_message.id,
        }

        # IMPORTANT PART
        await self.update_answer_overflow_solution(
            thread_message_id=str(thread.id),  # Discord thread starter message id
            solution_message_id=str(solution_message_id),
        )

    @app_commands.command(
        name="unsolve", description="Remove solved status and cancel closure timer."
    )
    async def unsolve(self, interaction: discord.Interaction):
        thread = interaction.channel

        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "This command must be used inside a thread.",
                ephemeral=True,
            )
            return

        if (
            thread.owner_id != interaction.user.id
            and not interaction.user.guild_permissions.manage_threads
        ):
            await interaction.response.send_message(
                "You do not have permission to unsolve this thread.",
                ephemeral=True,
            )
            return

        if thread.id in self.pending_closures:
            closure = self.pending_closures.pop(thread.id)
            closure["task"].cancel()

            try:
                msg = await thread.fetch_message(closure["timer_message_id"])
                await msg.delete()
            except Exception:
                pass

        await thread.edit(locked=False, archived=False)

        # Remove solved tag
        try:
            if isinstance(thread.parent, discord.ForumChannel):
                solved_tag_id = SOLVED_TAG_IDS.get(thread.parent.id)
                if solved_tag_id:
                    await thread.edit(
                        applied_tags=[
                            t for t in thread.applied_tags if t.id != solved_tag_id
                        ]
                    )
        except Exception:
            pass

        # IMPORTANT: remove solution
        await self.update_answer_overflow_solution(
            thread_message_id=str(thread.id),
            solution_message_id=None,
        )

        await interaction.response.send_message("Thread unmarked as solved.")

    ###########################################################################
    # Events
    ###########################################################################

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self._is_target_guild(thread.guild.id):
            await asyncio.sleep(1)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
