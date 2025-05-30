import json
import discord
from discord.ext import commands
import asyncio
import time
from datetime import datetime, timedelta

CONFIG_FILE = "config.json"
STARRED_MESSAGES_FILE = "starred_messages.json"

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)


# Load starred messages from file
def load_starred_messages():
    try:
        with open(STARRED_MESSAGES_FILE, "r") as f:
            content = f.read().strip()
            if not content:  # Check if file is empty
                return {}
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        # Handle both file not found and JSON decode errors
        return {}


# Save starred messages to file
def save_starred_messages(starred_messages):
    with open(STARRED_MESSAGES_FILE, "w") as f:
        json.dump(starred_messages, f)


class RateLimiter:
    def __init__(self, rate_limit_seconds=5):
        self.last_update = {}
        self.rate_limit_seconds = rate_limit_seconds
        self.retry_after = {}

    def can_update(self, message_id):
        """Check if we can update this message based on rate limit"""
        current_time = datetime.now()
        message_id = str(message_id)

        if message_id in self.retry_after:
            if current_time < self.retry_after[message_id]:
                return False
            else:
                del self.retry_after[message_id]

        last_time = self.last_update.get(message_id, datetime.min)
        if current_time - last_time >= timedelta(seconds=self.rate_limit_seconds):
            self.last_update[message_id] = current_time
            return True
        return False

    def set_retry_after(self, message_id, seconds):
        """Set a specific retry time based on Discord's rate limit response"""
        message_id = str(message_id)
        self.retry_after[message_id] = datetime.now() + timedelta(seconds=seconds)

    def get_next_retry_time(self, message_id):
        """Get the time remaining until the next retry for a message"""
        message_id = str(message_id)
        if message_id in self.retry_after:
            time_remaining = (
                self.retry_after[message_id] - datetime.now()
            ).total_seconds()
            return max(0, time_remaining)
        return 0


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.star_threshold = 2
        self.star_emoji = "💚"
        self.starboard_channel_id = config.get("starboard_channel_id")
        if not self.starboard_channel_id:
            raise ValueError("starboard_channel_id must be set in config.json")
        self.starred_messages = load_starred_messages()
        self.updating = set()
        self.rate_limiter = RateLimiter(rate_limit_seconds=5)
        self.pending_updates = set()
        self.save_lock = asyncio.Lock()

    async def update_starboard(self, message):
        message_id_str = str(message.id)

        if message_id_str in self.updating:
            return

        if not self.rate_limiter.can_update(message_id_str):
            self.pending_updates.add(message_id_str)
            return

        self.updating.add(message_id_str)

        try:
            guild = message.guild
            starboard_channel = guild.get_channel(self.starboard_channel_id)
            if not starboard_channel:
                return

            try:
                message = await message.channel.fetch_message(message.id)
            except discord.NotFound:
                if message_id_str in self.starred_messages:
                    await self.cleanup_missing_message(message_id_str)
                return
            except discord.Forbidden:
                return

            star_count = 0
            for reaction in message.reactions:
                if str(reaction.emoji) == self.star_emoji:
                    star_count = reaction.count
                    break

            starred_message = None
            if message_id_str in self.starred_messages:
                starred_message_id = self.starred_messages[message_id_str]
                try:
                    starred_message = await starboard_channel.fetch_message(
                        int(starred_message_id)
                    )
                except discord.NotFound:
                    async with self.save_lock:
                        if message_id_str in self.starred_messages:
                            del self.starred_messages[message_id_str]
                            save_starred_messages(self.starred_messages)
                    starred_message = None

            if star_count < self.star_threshold:
                if starred_message:
                    try:
                        await starred_message.delete()
                        async with self.save_lock:
                            if message_id_str in self.starred_messages:
                                del self.starred_messages[message_id_str]
                                save_starred_messages(self.starred_messages)
                    except discord.HTTPException:
                        pass
            else:
                embed = self.create_embed(message)
                view = self.create_view(message, star_count)

                if starred_message:
                    try:
                        await starred_message.edit(embed=embed, view=view)
                    except discord.HTTPException:
                        pass
                else:
                    try:
                        starred_message = await starboard_channel.send(
                            embed=embed, view=view
                        )
                        async with self.save_lock:
                            self.starred_messages[message_id_str] = str(
                                starred_message.id
                            )
                            save_starred_messages(self.starred_messages)
                    except discord.HTTPException:
                        pass
        finally:
            self.updating.remove(message_id_str)

    def create_embed(self, message):
        embed = discord.Embed(
            description=message.content or "*[No content]*", color=0xFFCD3F
        )
        author_name = message.author.display_name
        author_icon_url = message.author.avatar.url if message.author.avatar else None
        embed.set_author(name=author_name, icon_url=author_icon_url)

        if message.attachments:
            embed.set_image(url=message.attachments[0].url)

        embed.set_footer(
            text=f"Posted: {message.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return embed

    def create_view(self, message, star_count):
        view = discord.ui.View()
        jump_button = discord.ui.Button(
            label=f"Jump to Message | {self.star_emoji} {star_count}",
            url=message.jump_url,
            style=discord.ButtonStyle.link,
        )
        view.add_item(jump_button)
        return view

    async def cleanup_missing_message(self, message_id):
        if message_id not in self.starred_messages:
            return

        starred_message_id = self.starred_messages[message_id]
        for guild in self.bot.guilds:
            starboard_channel = guild.get_channel(self.starboard_channel_id)
            if starboard_channel:
                try:
                    starred_message = await starboard_channel.fetch_message(
                        int(starred_message_id)
                    )
                    await starred_message.delete()
                except discord.NotFound:
                    pass

        async with self.save_lock:
            if message_id in self.starred_messages:
                del self.starred_messages[message_id]
                save_starred_messages(self.starred_messages)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return

            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            await self.update_starboard(message)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return

            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            await self.update_starboard(message)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if str(message.id) in self.starred_messages:
            await self.cleanup_missing_message(str(message.id))


async def setup(bot):
    await bot.add_cog(Starboard(bot))
