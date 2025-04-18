import discord
from discord.ext import commands
from collections import defaultdict
import time
import json
from typing import Dict, List, Tuple, Set
import asyncio


class DuplicateMessageDetector(commands.Cog):
    # Constants
    DEFAULT_CONFIG = {
        "time_window": 60,
        "min_message_length": 10,
        "log_channel_id": None,
        "role_whitelist": [],
    }
    CLEANUP_INTERVAL = 120  # 2 minutes

    def __init__(self, bot: commands.Bot):
        """Initialize the duplicate message detector.

        Args:
            bot: The discord bot instance
        """
        self.bot = bot
        self.message_cache: Dict[int, List[Tuple[str, float]]] = defaultdict(list)
        self.config = self.load_config()
        self.time_window = self.config["time_window"]
        self.min_message_length = self.config["min_message_length"]
        self.log_channel_id = self.config["log_channel_id"]
        self.role_whitelist: Set[int] = set(self.config["role_whitelist"])

        # Start cleanup task
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_old_messages())

    def load_config(self) -> dict:
        """Load the configuration from config.json file.

        Returns:
            dict: Configuration settings
        """
        try:
            with open("config.json", "r") as file:
                config = json.load(file)
                # Merge with defaults to ensure all required fields exist
                return {**self.DEFAULT_CONFIG, **config}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading config, using defaults: {e}")
            return self.DEFAULT_CONFIG

    async def cleanup_old_messages(self):
        """Periodically clean up old messages from the cache."""
        while not self.bot.is_closed():
            try:
                current_time = time.time()
                for user_id in list(self.message_cache.keys()):
                    self.message_cache[user_id] = [
                        (content, msg_time)
                        for content, msg_time in self.message_cache[user_id]
                        if (current_time - msg_time) <= self.time_window
                    ]
                    # Remove empty lists
                    if not self.message_cache[user_id]:
                        del self.message_cache[user_id]
                await asyncio.sleep(self.CLEANUP_INTERVAL)
            except Exception as e:
                print(f"Error in cleanup task: {e}")
                await asyncio.sleep(self.CLEANUP_INTERVAL)

    def is_whitelisted(self, member):
        """Checks if a user has a whitelisted role."""
        if not self.role_whitelist:  # If no roles are specified, no one is whitelisted
            return False
        member_roles = {role.id for role in member.roles}
        return any(role_id in member_roles for role_id in self.role_whitelist)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
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
                if (
                    content == msg_content
                    and (timestamp - msg_time) <= self.time_window
                ):
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
        except Exception as e:
            print(f"Error processing message: {e}")

    async def log_deletion(self, message: discord.Message, content: str):
        """Log deleted messages to the configured log channel.

        Args:
            message: The deleted discord message
            content: Content of the deleted message
        """
        try:
            log_channel = self.bot.get_channel(self.log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="🦆 Duplicate Message Spotted!",
                    description=f"*Quack quack!* I noticed {message.author.mention} sent the same message twice, so I helped clean up! 🧹",
                    color=0xFFCD3F,
                )
                embed.add_field(name="Where", value=message.channel.mention)
                embed.add_field(name="The Message", value=f"`{content}`", inline=False)
                embed.set_footer(
                    text=f"Cleaned up at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} 🧼"
                )

                await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Error logging message deletion: {e}")

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        if hasattr(self, "cleanup_task"):
            self.cleanup_task.cancel()


async def setup(bot):
    await bot.add_cog(DuplicateMessageDetector(bot))
