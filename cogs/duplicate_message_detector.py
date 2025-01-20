import discord
from discord.ext import commands
from collections import defaultdict
import time
import json


class DuplicateMessageDetector(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message_cache = defaultdict(lambda: [])  # Cache for tracking messages
        self.config = self.load_config()  # Load configuration
        self.time_window = self.config[
            "time_window"
        ]  # Time window for duplicate detection
        self.min_message_length = self.config[
            "min_message_length"
        ]  # Minimum message length
        self.log_channel_id = self.config["log_channel_id"]  # Log channel ID
        self.role_whitelist = self.config["role_whitelist"]  # Whitelisted roles

    def load_config(self):
        """Loads the configuration from the config.json file."""
        with open("config.json", "r") as file:
            return json.load(file)

    def is_whitelisted(self, member):
        """Checks if a user has a whitelisted role."""
        if not self.role_whitelist:  # If no roles are specified, no one is whitelisted
            return False
        member_roles = {role.id for role in member.roles}
        return any(role_id in member_roles for role_id in self.role_whitelist)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bot messages
        if message.author.bot:
            return

        user_id = message.author.id
        content = message.content
        timestamp = time.time()

        # Check if the user is whitelisted
        if self.is_whitelisted(message.author):
            return

        # Skip short messages
        if len(content) < self.min_message_length:
            return

        # Check for repeated messages
        user_messages = self.message_cache[user_id]
        for msg_content, msg_time in user_messages:
            if content == msg_content and (timestamp - msg_time) <= self.time_window:
                await message.delete()  # Delete the duplicate message
                await self.log_deletion(message, content)  # Log the deletion
                return

        # Add the current message to the cache
        user_messages.append((content, timestamp))
        # Keep only recent messages in the cache
        self.message_cache[user_id] = [
            (msg_content, msg_time)
            for msg_content, msg_time in user_messages
            if (timestamp - msg_time) <= self.time_window
        ]

    async def log_deletion(self, message, content):
        """Logs deleted messages to the configured log channel in an embed format."""
        log_channel = self.bot.get_channel(self.log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="Duplicate Message Deleted",
                description=f"Message from {message.author.mention} ({message.author.id}) was deleted for being a duplicate.",
                color=0xFFCD3F,
            )
            embed.add_field(name="Channel", value=message.channel.mention)
            embed.add_field(name="Content", value=f"`{content}`", inline=False)
            embed.set_footer(
                text=f"Message deleted at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}"
            )

            await log_channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DuplicateMessageDetector(bot))
