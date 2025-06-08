import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from datetime import datetime, timezone, timedelta
import json
import os
import asyncio
import logging

PHOTO_CHANNEL_ID = 1025978742318833684
ANNOUNCE_CHANNEL_ID = 953968250553765908
STAR_EMOJI = "⭐"
DATA_FILE = "photo_star_counts.json"

logger = logging.getLogger(__name__)


def start_of_month(dt=None):
    """Return the date of the first day of the current month in UTC."""
    dt = dt or datetime.now(timezone.utc)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()


class MessageLinkButton(Button):
    """A link button that jumps to the starred photo message."""

    def __init__(self, url: str):
        super().__init__(label="View Photo", url=url, style=discord.ButtonStyle.link)


class MonthlyPhotoStarCounter(commands.Cog):
    """Tracks photo stars and posts a monthly winner announcement."""

    def __init__(self, bot):
        self.bot = bot
        self.data = self.load_data()
        self._post_lock = asyncio.Lock()
        self.monthly_announcement.start()

    def load_data(self):
        """Load persistent star data from disk."""
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_data(self):
        """Save current star data to disk."""
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f)

    def get_current_period(self):
        """Get the current month as a string."""
        return str(start_of_month())

    def increment(self, period, message_id, user_id, jump_url):
        """Increment the star count for a photo message."""
        period = str(period)
        if period not in self.data:
            self.data[period] = {}
        if message_id not in self.data[period]:
            self.data[period][message_id] = {
                "user_id": user_id,
                "star_count": 0,
                "jump_url": jump_url,
            }
        self.data[period][message_id]["star_count"] += 1
        self.save_data()

    def decrement(self, period, message_id):
        """Decrement the star count for a photo message."""
        period = str(period)
        if period in self.data and message_id in self.data[period]:
            self.data[period][message_id]["star_count"] = max(
                0, self.data[period][message_id]["star_count"] - 1
            )
            self.save_data()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track stars added to photo messages in the photo channel."""
        if str(payload.emoji) != STAR_EMOJI or payload.channel_id != PHOTO_CHANNEL_ID:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception:
            return
        if not msg.attachments:
            return
        period = self.get_current_period()
        self.increment(period, str(msg.id), str(msg.author.id), msg.jump_url)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Track stars removed from photo messages in the photo channel."""
        if str(payload.emoji) != STAR_EMOJI or payload.channel_id != PHOTO_CHANNEL_ID:
            return
        period = self.get_current_period()
        self.decrement(period, str(payload.message_id))

    @tasks.loop(hours=1)
    async def monthly_announcement(self):
        """
        Once a month, post an announcement with the most-starred photo
        from the previous month in the configured channel.
        """
        now = datetime.now(timezone.utc)
        if now.day != 1 or now.hour != 0 or now.minute > 1:
            return
        async with self._post_lock:
            last_announced = self.data.get("last_announced", None)
            previous_period = str(start_of_month(now - timedelta(days=1)))
            if last_announced == previous_period:
                return
            month_data = self.data.get(previous_period, {})
            if not month_data:
                logger.info(
                    f"No photo stars for {previous_period}, skipping announcement."
                )
                self.data["last_announced"] = previous_period
                self.save_data()
                return
            top_msg = max(month_data.items(), key=lambda x: x[1]["star_count"])
            _, info = top_msg
            user_id = int(info["user_id"])
            star_count = info["star_count"]
            jump_url = info["jump_url"]
            guild = self.bot.guilds[0] if self.bot.guilds else None
            user = None
            if guild:
                user = guild.get_member(user_id) or await self.bot.fetch_user(user_id)
            else:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    user = None
            announce_channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
            if not announce_channel:
                logger.warning("Announcement channel not found.")
                return
            embed = discord.Embed(
                title="📸 Photo of the Month!",
                description=f"**{user.mention if user else f'User {user_id}'}** received the most ⭐ stars for their photo in {now.strftime('%B %Y')}!\n\n**{star_count}** stars.",
                color=0xFFCD3F,
            )
            embed.add_field(
                name="Jump to Photo", value=f"[Click here]({jump_url})", inline=False
            )
            embed.set_footer(text="Congrats! Think you can win next month?")
            view = View()
            view.add_item(MessageLinkButton(jump_url))
            await announce_channel.send(embed=embed, view=view)
            self.data["last_announced"] = previous_period
            self.save_data()

    @monthly_announcement.before_loop
    async def before_monthly(self):
        """Wait for the bot to be ready before starting scheduled tasks."""
        await self.bot.wait_until_ready()


async def setup(bot):
    """Load the MonthlyPhotoStarCounter cog."""
    await bot.add_cog(MonthlyPhotoStarCounter(bot))
