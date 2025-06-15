import discord
from discord.ext import commands
import aiohttp
import os
import logging
import asyncio
from discord import ui, app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")

SELFHELP_CHANNEL_IDS = [1364139133794123807]
SOLVED_ONLY_CHANNEL_IDS = [1121126215995113552, 1383504546361380995]

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
    1383504546361380995: 1383506837252472982,
}


class SupportView(ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        thread_owner: int,
        parent_channel_id: int,
        show_help_button: bool = True,
    ):
        super().__init__(timeout=None)
        self.bot = bot
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

        if user.guild_permissions.manage_threads:
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

            cog: SelfHelp = view.bot.get_cog("SelfHelp")
            if thread.id in cog.pending_closures:
                await interaction.response.send_message(
                    "This thread has already been marked as solved and is pending closure.",
                    ephemeral=True,
                )
                return

            close_time = int(datetime.now(timezone.utc).timestamp()) + 1800
            await interaction.response.send_message(
                f"Thread marked as solved. It will be automatically closed <t:{close_time}:R>. Use </unsolve:1383537581110853685> to cancel."
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

            self.disabled = True
            await interaction.message.edit(view=view)

            task = asyncio.create_task(cog.delayed_close_thread(thread))
            cog.pending_closures[thread.id] = task


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.processed_threads = set()
        self.pending_closures = {}
        self.hint_sent_threads = set()
        self.bot.add_view(SupportView(bot, thread_owner=0, parent_channel_id=0))

    async def delayed_close_thread(self, thread: discord.Thread, delay: int = 1800):
        try:
            await asyncio.sleep(delay)
            await thread.edit(locked=True, archived=True)
            self.pending_closures.pop(thread.id, None)
            await thread.send("This thread is now closed.")
        except asyncio.CancelledError:
            pass

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
                await message.reply(
                    "-# If your issue is resolved, you can use the **Mark as Solved** button or use </solved:1383537581110853686> to close the thread.",
                    mention_author=True,
                )
                self.hint_sent_threads.add(message.channel.id)

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
            show_help = thread.parent_id not in SOLVED_ONLY_CHANNEL_IDS

            await thread.send(
                answer,
                view=SupportView(
                    bot=self.bot,
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
                    bot=self.bot,
                    thread_owner=thread.owner_id,
                    parent_channel_id=thread.parent_id,
                    show_help_button=False,
                ),
            )


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
