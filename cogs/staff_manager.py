import discord
from discord.ext import commands
import logging

STAFF_ROLE_IDS = [950276268593659925, 956466393514143814, 947890664656474222]
TARGET_CHANNEL_ID = 1400770606777237555
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

        log_channel = guild.get_channel(TARGET_CHANNEL_ID)
        if not log_channel:
            logger.warning(f"Target log channel {TARGET_CHANNEL_ID} not found.")
            return

        embed = discord.Embed(
            description=message.content or "*[No content]*",
            color=0xFFCD3F,
            timestamp=message.created_at,
        )

        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )

        if thread_info and thread_info["is_forum_post"]:
            embed.add_field(
                name="Forum Thread",
                value=f"{thread_info['parent'].mention} → **{thread_info['name']}**",
                inline=False,
            )
        else:
            embed.add_field(name="Channel", value=channel.mention, inline=False)

        embed.set_footer(text=f"User ID: {message.author.id}")

        if message.attachments:
            embed.set_image(url=message.attachments[0].url)

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Jump to Message",
                url=message.jump_url,
                style=discord.ButtonStyle.link,
            )
        )

        try:
            await log_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send log message: {e}")


async def setup(bot):
    await bot.add_cog(MessageNoteLogger(bot))
