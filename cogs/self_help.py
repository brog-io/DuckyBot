import discord
from discord.ext import commands
import aiohttp
import os
import asyncio
from discord import ui
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")

SELFHELP_CHANNEL_IDS = [1364139133794123807]
SUPPORT_ROLE_ID = 1364141260708909117
SOLVED_TAG_ID = 1364276749826920538
REACTION_TRIGGER = "❓"
MOD_ROLE_IDS = [
    950276268593659925,
    956466393514143814,
    950275266045960254,
]


class SupportView(ui.View):
    def __init__(self, thread_owner: int):
        super().__init__(timeout=None)
        self.thread_owner = thread_owner

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

    @ui.button(
        label="This didn't help",
        style=discord.ButtonStyle.danger,
        custom_id="support_button",
    )
    async def help_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.is_authorized(interaction.user):
            await interaction.response.send_message(
                "Only the thread creator or moderators can use this button.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"<@&{SUPPORT_ROLE_ID}> User still needs help in {interaction.channel.mention}",
            ephemeral=False,
        )
        button.disabled = True
        await interaction.message.edit(view=self)

    @ui.button(
        label="Mark as Solved",
        style=discord.ButtonStyle.success,
        custom_id="mark_solved_button",
    )
    async def solved_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.is_authorized(interaction.user):
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
                current_tags = list(thread.applied_tags) if thread.applied_tags else []
                forum_channel = thread.parent
                solved_tag = None
                for tag in forum_channel.available_tags:
                    if tag.id == SOLVED_TAG_ID:
                        solved_tag = tag
                        break
                if solved_tag and solved_tag not in current_tags:
                    current_tags.append(solved_tag)
                await thread.edit(
                    archived=True,
                    locked=True,
                    applied_tags=current_tags,
                )
            else:
                await thread.edit(
                    archived=True,
                    locked=True,
                )
        except Exception as e:
            await thread.send(f"Error while closing thread: {e}")


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SupportView(thread_owner=0))  # persistent view

        # Keep track of already processed threads to avoid double-responses
        self.processed_threads = set()

    async def query_api(self, query: str, extra: str = "") -> str:
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
        """Processes a thread: calls API and sends support view if not done already."""
        if thread.id in self.processed_threads:
            return  # Already processed
        self.processed_threads.add(thread.id)

        await thread.send("Analyzing your question, please wait...")

        # Gather context
        body = ""
        if initial_message:
            body = initial_message.content
        else:
            # Try to get the starter message if not provided
            try:
                async for msg in thread.history(limit=1, oldest_first=True):
                    body = msg.content
                    break
            except Exception as e:
                print(f"Failed to fetch first message: {e}")

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
        await thread.send(answer, view=SupportView(thread_owner=thread.owner_id))

        async for msg in thread.history(limit=5):
            if msg.content.startswith("Analyzing your question"):
                await msg.delete()
                break

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if thread.parent_id not in SELFHELP_CHANNEL_IDS:
            return
        await asyncio.sleep(1)  # Correct fix for waiting a bit for thread to populate
        await self.process_forum_thread(thread)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Catch thread starter messages in forums as backup
        if (
            isinstance(message.channel, discord.Thread)
            and message.channel.parent_id in SELFHELP_CHANNEL_IDS
            and message.channel.owner_id == message.author.id
            and message.id == message.channel.id
        ):
            await self.process_forum_thread(message.channel, initial_message=message)

        # Optional: Prevent answering every message in selfhelp (don't auto-answer replies)
        if message.author.bot:
            return
        if message.channel.id in SELFHELP_CHANNEL_IDS:
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

        # Create thread with the message content as the title
        thread = await message.create_thread(name=message.content[:90])

        # Get the user who added the reaction
        user = self.bot.get_user(payload.user_id) or await self.bot.fetch_user(
            payload.user_id
        )

        await thread.send(
            f"<@{user.id}> created this thread from a message by <@{message.author.id}>"
        )

        answer = await self.query_api(message.content)
        await thread.send(answer, view=SupportView(thread_owner=payload.user_id))


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
