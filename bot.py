import discord
from discord.ui import Button, View
from discord import app_commands
import time
import asyncio
import aiohttp
import json
import os
import logging
import re
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MessageLinkButton(Button):
    def __init__(self, url: str):
        super().__init__(label="Go to Message", url=url, style=discord.ButtonStyle.link)


class RefreshButton(Button):
    # Class variable to store last usage per user.
    _cooldowns = {}
    COOLDOWN_DURATION = 30  # Cooldown in seconds

    def __init__(self, bot):
        super().__init__(label="Refresh Count", style=discord.ButtonStyle.primary)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        # Check cooldown
        current_time = time.time()
        user_id = interaction.user.id
        last_used = self._cooldowns.get(user_id, 0)

        # If user is on cooldown
        remaining = self.COOLDOWN_DURATION - (current_time - last_used)
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {int(remaining)} seconds before refreshing again.",
                ephemeral=True,
            )
            return

        # Update cooldown
        self._cooldowns[user_id] = current_time

        # Defer the response immediately to prevent timeout
        await interaction.response.defer()

        try:
            async with self.bot.http_session.get(
                "https://api.ente.io/files/count"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    current_count = data.get("count")

                    embed = discord.Embed(
                        title="Ente Files Count",
                        description=f"Currently tracking **{current_count:,}** files",
                        color=0xFFCD3F,
                        timestamp=discord.utils.utcnow(),
                    )

                    # Create new view with fresh button
                    view = View()
                    view.add_item(RefreshButton(self.bot))

                    # Use edit_original_message since we deferred
                    await interaction.edit_original_response(embed=embed, view=view)
                else:
                    await interaction.followup.send(
                        "Failed to fetch the current file count. Please try again later.",
                        ephemeral=True,
                    )
        except Exception as e:
            await interaction.followup.send(
                "An error occurred while refreshing the count. Please try again later.",
                ephemeral=True,
            )


class EnteDiscordBot:
    def __init__(self, config_path: str = "config.json"):
        """
        Initialize the Discord bot with configuration from a JSON file

        :param config_path: Path to the configuration JSON file
        """
        # Load configuration
        self.config = self.load_config(config_path)

        # Discord client intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Enable member events

        # Discord client
        self.client = discord.Client(intents=intents)

        # Command tree for slash commands
        self.tree = app_commands.CommandTree(self.client)

        # Track last file count to avoid repeated messages
        self.last_count: Optional[int] = None

        # HTTP client for API requests
        self.http_session: Optional[aiohttp.ClientSession] = None

        # Setup event handlers and commands
        self.setup_event_handlers()
        self.setup_commands()

        # Message link regex pattern
        self.message_link_pattern = re.compile(
            r"https?:\/\/(?:.*\.)?discord\.com\/channels\/(\d+)\/(\d+)\/(\d+)"
        )

    def load_config(self, config_path: str) -> dict:
        """
        Load configuration from JSON file

        :param config_path: Path to the configuration file
        :return: Dictionary of configuration settings
        """
        try:
            # Check if config file exists, if not create a template
            if not os.path.exists(config_path):
                default_config = {
                    "discord_token": "YOUR_DISCORD_BOT_TOKEN",
                    "channel_id": "YOUR_CHANNEL_ID",
                    "welcome_channel_id": "YOUR_WELCOME_CHANNEL_ID",
                    "update_interval": 300,  # 5 minutes
                }
                with open(config_path, "w") as f:
                    json.dump(default_config, f, indent=4)

                logger.error(
                    f"Config file created at {config_path}. Please edit with your details."
                )
                raise ValueError(f"Please edit the config file at {config_path}")

            # Load existing config
            with open(config_path, "r") as f:
                config = json.load(f)

            # Validate required keys
            required_keys = ["discord_token", "channel_id"]
            for key in required_keys:
                if key not in config or not config[key]:
                    raise ValueError(f"Missing required config key: {key}")

            return config

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in config file: {config_path}")
            raise
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise

    def setup_commands(self):
        """
        Setup slash commands
        """

        @self.tree.command(
            name="files", description="Get the current number of files tracked by Ente"
        )
        async def files(interaction: discord.Interaction):
            try:
                async with self.http_session.get(
                    "https://api.ente.io/files/count"
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        current_count = data.get("count")

                        embed = discord.Embed(
                            title="Ente Files Count",
                            description=f"Currently tracking **{current_count:,}** files",
                            color=0xFFCD3F,
                            timestamp=discord.utils.utcnow(),
                        )

                        # Create view with refresh button
                        view = View()
                        view.add_item(RefreshButton(self))

                        await interaction.response.send_message(embed=embed, view=view)
                    else:
                        await interaction.response.send_message(
                            "Failed to fetch the current file count. Please try again later.",
                            ephemeral=True,
                        )
            except Exception as e:
                logger.error(f"Error fetching file count: {e}")
                await interaction.response.send_message(
                    "An error occurred while fetching the count. Please try again later.",
                    ephemeral=True,
                )

    def setup_event_handlers(self):
        """
        Setup Discord client event handlers
        """

        @self.client.event
        async def on_ready():
            logger.info(f"Logged in as {self.client.user}")
            # Set initial status
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="files",
                details="Monitoring files",
            )
            await self.client.change_presence(
                status=discord.Status.online, activity=activity
            )
            await self.start_file_count_monitoring()

        @self.client.event
        async def on_message(message):
            """
            Handle message link detection and embed creation
            """
            if message.author.bot:
                return

            # Check for message links
            matches = self.message_link_pattern.finditer(message.content)
            for match in matches:
                guild_id, channel_id, message_id = match.groups()

                try:
                    # Get the referenced message
                    channel = self.client.get_channel(int(channel_id))
                    if not channel:
                        continue

                    referenced_message = await channel.fetch_message(int(message_id))
                    if not referenced_message:
                        continue

                    # Create embed
                    embed = discord.Embed(
                        description=referenced_message.content,
                        timestamp=referenced_message.created_at,
                        color=0xFFCD3F,  # Discord Blurple
                    )

                    # Add author info
                    embed.set_author(
                        name=referenced_message.author.display_name,
                        icon_url=referenced_message.author.display_avatar.url,
                    )

                    # Add attachment preview if exists
                    if referenced_message.attachments:
                        if (
                            referenced_message.attachments[0]
                            .url.lower()
                            .endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                        ):
                            embed.set_image(url=referenced_message.attachments[0].url)

                    # Create view with message link button
                    view = View()
                    view.add_item(MessageLinkButton(match.group(0)))

                    await message.reply(embed=embed, view=view, mention_author=False)

                except Exception as e:
                    logger.error(f"Error processing message link: {e}")

        @self.client.event
        async def on_member_update(before, after):
            """
            Handle nickname updates to remove flag emojis
            """
            if before.display_name != after.display_name:
                sanitized_name = self.remove_flags_from_name(after.display_name)
                if sanitized_name != after.display_name:
                    try:
                        await after.edit(nick=sanitized_name)
                        logger.info(
                            f"Removed flags from {after.display_name}, updated to: {sanitized_name}"
                        )
                    except discord.Forbidden:
                        logger.warning(
                            f"Could not change nickname for {after.name}: Missing permissions"
                        )

        @self.client.event
        async def on_member_join(member):
            """
            Send a welcome message when a new member joins the server
            """
            welcome_channel_id = int(self.config.get("welcome_channel_id", 0))
            if welcome_channel_id:
                channel = self.client.get_channel(welcome_channel_id)
                if channel:
                    try:
                        await channel.send(
                            f"Welcome to the server, {member.mention}! We're glad to have you here. <:lilducky:1069841394929238106>"
                        )
                        logger.info(f"Welcome message sent to {member.name}")
                    except Exception as e:
                        logger.error(f"Error sending welcome message: {e}")
                else:
                    logger.error(
                        f"Could not find welcome channel with ID {welcome_channel_id}"
                    )
            else:
                logger.warning("No welcome channel ID set in the config.")

    def remove_flags_from_name(self, name: str) -> str:
        """
        Remove Unicode flag emojis from a string

        :param name: String to sanitize
        :return: Sanitized string without flags
        """
        flag_pattern = re.compile(
            r"[\U0001F1E6-\U0001F1FF]{2}"  # Matches regional indicator symbols (flag emojis)
        )
        return flag_pattern.sub("", name).strip()

    async def start_file_count_monitoring(self):
        """
        Start the background task for monitoring file count
        """
        # Create HTTP session
        self.http_session = aiohttp.ClientSession()

        # Start monitoring task
        self.client.loop.create_task(self.monitor_file_count())

    async def monitor_file_count(self):
        """
        Background task to periodically check Ente.io file count and update status
        """
        await self.client.wait_until_ready()

        # Get channel
        channel = self.client.get_channel(int(self.config["channel_id"]))
        if not channel:
            logger.error(f"Could not find channel with ID {self.config['channel_id']}")
            return

        # Update interval from config
        update_interval = self.config.get("update_interval", 300)

        while not self.client.is_closed():
            try:
                # Fetch file count
                async with self.http_session.get(
                    "https://api.ente.io/files/count"
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        current_count = data.get("count")

                        # Update if count has changed
                        if current_count != self.last_count:
                            # Update channel name
                            await channel.edit(name=f"📊 {current_count:,} Files")

                            # Update bot status with fixed format
                            activity = discord.Activity(
                                type=discord.ActivityType.watching,
                                name=f"{current_count:,} files",
                            )
                            await self.client.change_presence(
                                status=discord.Status.online, activity=activity
                            )

                            logger.info(f"Updated status with {current_count:,} files")
                            self.last_count = current_count
                    else:
                        logger.error(
                            f"API request failed with status {response.status}"
                        )

            except Exception as e:
                logger.error(f"Error in file count monitoring: {e}")

            # Wait before next check
            await asyncio.sleep(update_interval)

    async def start(self):
        """
        Start the Discord bot
        """
        try:
            # Login and sync commands before connecting
            await self.client.login(self.config["discord_token"])
            await self.tree.sync()
            await self.client.connect()
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
        finally:
            # Ensure HTTP session is closed
            if self.http_session:
                await self.http_session.close()


def main():
    try:
        # Create bot instance
        bot = EnteDiscordBot()

        # Run the bot
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
