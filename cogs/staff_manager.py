import discord
from discord.ext import commands
import logging
import aiohttp
import os

STAFF_ROLE_IDS = [950276268593659925, 956466393514143814]
TARGET_CHANNEL_ID = 1381183345626447944
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Get from environment variable
NOTEPAD_EMOJI = "🗒️"

logger = logging.getLogger(__name__)


class MessageNoteLogger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def is_staff(self, member: discord.Member):
        if member.guild_permissions.manage_messages:
            return True
        return any(role.id in STAFF_ROLE_IDS for role in getattr(member, "roles", []))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != NOTEPAD_EMOJI:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or not self.is_staff(member):
            return

        channel = None
        thread_info = None
        channel = guild.get_channel(payload.channel_id)

        if not channel:
            try:
                channel = await guild.fetch_channel(payload.channel_id)
                if isinstance(channel, discord.Thread):
                    thread_info = {
                        "name": channel.name,
                        "parent": channel.parent,
                        "is_forum_post": isinstance(
                            channel.parent, discord.ForumChannel
                        ),
                    }
            except Exception as e:
                logger.warning(
                    f"Failed to fetch channel/thread {payload.channel_id}: {e}"
                )
                return

        if not channel:
            logger.warning(f"Could not find channel/thread {payload.channel_id}")
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception as e:
            logger.warning(f"Failed to fetch message: {e}")
            return

        if message.author.bot:
            return

        # Check if webhook URL is configured
        if not WEBHOOK_URL:
            logger.error("WEBHOOK_URL environment variable not set")
            return

        # Create embed data for webhook
        embed_data = {
            "description": message.content or "*[No content]*",
            "color": 0xFFCD3F,
            "timestamp": message.created_at.isoformat(),
            "author": {
                "name": message.author.display_name,
                "icon_url": str(message.author.display_avatar.url),
            },
            "footer": {"text": f"User ID: {message.author.id}"},
            "fields": [],
        }

        # Add channel/thread info
        if thread_info and thread_info["is_forum_post"]:
            embed_data["fields"].append(
                {
                    "name": "Forum Thread",
                    "value": f"{thread_info['parent'].mention} → **{thread_info['name']}**",
                    "inline": False,
                }
            )
        else:
            embed_data["fields"].append(
                {"name": "Channel", "value": channel.mention, "inline": False}
            )

        # Add image if attachments exist
        if message.attachments:
            embed_data["image"] = {"url": message.attachments[0].url}

        # Add clickable link to view message
        embed_data["fields"].append(
            {
                "name": "🔗 Message Link",
                "value": f"**[Click here to view message]({message.jump_url})**",
                "inline": False,
            }
        )

        # Send via webhook
        webhook_data = {
            "embeds": [embed_data],
            "username": "Message Logger",  # Optional: customize webhook appearance
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(WEBHOOK_URL, json=webhook_data) as response:
                    if response.status != 204:
                        logger.error(
                            f"Webhook request failed with status {response.status}: {await response.text()}"
                        )
                    else:
                        logger.info(
                            f"Successfully logged message {message.id} via webhook"
                        )
        except Exception as e:
            logger.error(f"Failed to send webhook message: {e}")


async def setup(bot):
    await bot.add_cog(MessageNoteLogger(bot))
