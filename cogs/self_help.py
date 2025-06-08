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
                    "Only moderators can use this button.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"<@&{SUPPORT_ROLE_ID}> User still needs help in {interaction.channel.mention}",
                ephemeral=False,
            )
            self.disabled = True
            await interaction.message.edit(view=view)
            cog = interaction.client.get_cog("SelfHelp")
            if cog and isinstance(interaction.channel, discord.Thread):
                thread_id = interaction.channel.id
                if thread_id in cog.thread_activity:
                    cog.thread_activity[thread_id]["last_active"] = datetime.utcnow()
                    cog.thread_activity[thread_id]["warned_at"] = None
                    cog.save_activity_data()

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
                    "Only moderators can mark this as solved.", ephemeral=True
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
                cog = interaction.client.get_cog("SelfHelp")
                if cog and isinstance(thread, discord.Thread):
                    cog.thread_activity.pop(thread.id, None)
                    cog.save_activity_data()
            except Exception as e:
                await thread.send(f"Error while closing thread: {e}")


class SelfHelp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SupportView(thread_owner=0, parent_channel_id=0))
        self.processed_threads = set()
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
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for channel_id in SELFHELP_CHANNEL_IDS:
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.ForumChannel):
                    continue
                try:
                    threads = await channel.active_threads()
                    for thread in threads:
                        if thread.id not in self.thread_activity and not thread.archived:
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
                    logger.warning(f"Failed to bootstrap threads in {channel.name}: {e}")

    @tasks.loop(hours=6)
    async def check_stale_threads(self):
        now = datetime.utcnow()
        for thread_id, data in list(self.thread_activity.items()):
            thread = self.bot.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                continue
            if thread.locked or thread.archived:
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


async def setup(bot):
    await bot.add_cog(SelfHelp(bot))
