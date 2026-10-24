import discord
from discord.ext import commands
import asyncio
import aiohttp
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from utils.rate_limiter import RateLimiter
from datetime import datetime
import sys
import traceback
import time

logging.captureWarnings(True)
load_dotenv()

LOG_DIR = "logs"
LOG_FILE_SIZE = 10_000_000
LOG_BACKUP_COUNT = 30
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")

# Restart configuration
MAX_RESTART_ATTEMPTS = int(os.getenv("MAX_RESTART_ATTEMPTS", "10"))
RESTART_DELAY_BASE = int(os.getenv("RESTART_DELAY_BASE", "30"))  # Base delay in seconds
MAX_RESTART_DELAY = int(os.getenv("MAX_RESTART_DELAY", "300"))  # Max delay (5 minutes)

# Memory tuning
# Downloading every member of the server at startup ("chunking") is the single
# largest RAM cost of the bot. No cog needs the full member list, so it is off
# by default; members are still cached as they join or change. Set
# CHUNK_MEMBERS=true to restore the old behaviour.
CHUNK_MEMBERS = os.getenv("CHUNK_MEMBERS", "false").lower() in ("1", "true", "yes")
# discord.py keeps the last N messages in memory (default 1000). Every cog now
# uses raw events, so the cache is off unless you set a size here.
MESSAGE_CACHE_SIZE = int(os.getenv("MESSAGE_CACHE_SIZE", "0"))


def setup_logging() -> logging.Logger:
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    timestamp = datetime.now().strftime("%Y-%m-%d")
    log_file = f"{LOG_DIR}/discord_{timestamp}.log"
    root_logger = logging.getLogger()
    log_level = getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO)
    root_logger.setLevel(log_level)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_format = logging.Formatter("%(levelname)-8s %(name)-15s: %(message)s")
    console_handler.setFormatter(console_format)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_FILE_SIZE, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s"
    )
    file_handler.setFormatter(file_format)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    for logger_name in ["discord", "discord.http", "discord.gateway", "aiohttp"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    return logging.getLogger(__name__)


class EnteBot(commands.Bot):
    def __init__(self, config_path: str = None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.reactions = True
        # Events no cog listens to: dropping them means less gateway traffic
        # and no voice-state / invite / scheduled-event caches.
        intents.typing = False
        intents.voice_states = False
        intents.invites = False
        intents.webhooks = False
        intents.guild_scheduled_events = False
        intents.auto_moderation = False
        super().__init__(
            command_prefix="d!",
            intents=intents,
            max_messages=MESSAGE_CACHE_SIZE or None,
            chunk_guilds_at_startup=CHUNK_MEMBERS,
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents),
        )
        self.logger = logging.getLogger(__name__)
        self.config = self.load_config(config_path or DEFAULT_CONFIG_PATH)
        self.http_session: aiohttp.ClientSession | None = None
        self.user_limiter = RateLimiter(rate=1, per=30)
        self.guild_limiter = RateLimiter(rate=6, per=60)

    def load_config(self, config_path: str) -> dict:
        try:
            with open(config_path, "r") as config_file:
                config = json.load(config_file)
            return config
        except FileNotFoundError:
            self.logger.error(f"Configuration file '{config_path}' not found.")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing JSON configuration file: {e}")
            raise

    async def setup_hook(self) -> None:
        try:
            self.logger.info("Starting setup_hook...")
            self.http_session = aiohttp.ClientSession()
            cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
            for filename in os.listdir(cogs_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    ext = f"cogs.{filename[:-3]}"
                    try:
                        await self.load_extension(ext)
                        self.logger.info(f"Loaded extension: {ext}")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to load extension {ext}: {e}", exc_info=True
                        )

            from cogs.file_tracker import (
                PersistentView as FileTrackerView,
                RefreshButton as FileTrackerRefresh,
            )
            from cogs.star_counter import (
                PersistentView as StarCounterView,
                RefreshButton as StarCounterRefresh,
            )

            view = FileTrackerView()
            view.add_item(FileTrackerRefresh())
            self.add_view(view)
            star_view = StarCounterView()
            star_view.add_item(StarCounterRefresh())
            self.add_view(star_view)
            self.logger.info("Attempting to sync commands...")
            await self.tree.sync()
            cog = self.get_cog("SelfHelp")
            if cog and hasattr(cog, "post_setup"):
                await cog.post_setup()
                self.logger.info("Post-setup hook executed for SelfHelp")
            self.logger.info("Command sync completed")
        except Exception as e:
            self.logger.error(f"Error in setup_hook: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        self.logger.info("Bot is shutting down...")
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        self.logger.info(f"Bot is ready. Logged in as {self.user}")


def validate_env_vars() -> None:
    if os.getenv("DISCORD_TOKEN") is None:
        raise EnvironmentError("DISCORD_TOKEN environment variable not found.")


def is_recoverable_error(error: Exception) -> bool:
    """Determine if an error is recoverable and the bot should restart"""
    # Network/connection related errors - recoverable
    if isinstance(
        error,
        (
            discord.ConnectionClosed,
            discord.GatewayNotFound,
            aiohttp.ClientError,
            OSError,  # Network errors
            asyncio.TimeoutError,
        ),
    ):
        return True

    # Authentication errors - not recoverable
    if isinstance(error, discord.LoginFailure):
        return False

    # Configuration errors - not recoverable
    if isinstance(error, (EnvironmentError, FileNotFoundError, json.JSONDecodeError)):
        return False

    # For other errors, assume they might be recoverable
    return True


async def run_bot_with_restart() -> None:
    """Run the bot with automatic restart capability"""
    logger = setup_logging()
    restart_count = 0
    last_restart_time = 0

    while restart_count < MAX_RESTART_ATTEMPTS:
        try:
            logger.info(
                f"Starting bot... (Attempt {restart_count + 1}/{MAX_RESTART_ATTEMPTS})"
            )
            validate_env_vars()

            bot = EnteBot()
            async with bot:
                token = os.getenv("DISCORD_TOKEN")
                logger.info("Bot initialized, connecting to Discord...")
                await bot.start(token)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user - no restart")
            break

        except Exception as e:
            restart_count += 1
            error_type = type(e).__name__
            logger.error(f"Bot crashed with {error_type}: {e}", exc_info=True)

            # Check if error is recoverable
            if not is_recoverable_error(e):
                logger.error(
                    f"Non-recoverable error detected: {error_type}. Stopping restart attempts."
                )
                break

            if restart_count >= MAX_RESTART_ATTEMPTS:
                logger.error(
                    f"Maximum restart attempts ({MAX_RESTART_ATTEMPTS}) reached. Giving up."
                )
                break

            # Calculate restart delay with exponential backoff
            current_time = time.time()
            base_delay = RESTART_DELAY_BASE * (2 ** (restart_count - 1))
            delay = min(base_delay, MAX_RESTART_DELAY)

            # If we're restarting too quickly, add extra delay
            if (
                current_time - last_restart_time < 60
            ):  # Less than 1 minute since last restart
                delay = max(delay, 60)
                logger.warning("Rapid restart detected, adding extra delay")

            logger.info(
                f"Restarting in {delay} seconds... (Attempt {restart_count + 1}/{MAX_RESTART_ATTEMPTS})"
            )

            # Clean up any remaining tasks
            try:
                # Cancel all pending tasks. Skip this coroutine's own task:
                # cancelling it would abort the restart loop itself and leave
                # the old bot's tasks (and memory) alive.
                pending = [
                    t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                ]
                for task in pending:
                    if not task.done():
                        task.cancel()

                # Wait for tasks to complete cancellation
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            except Exception as cleanup_error:
                logger.warning(f"Error during cleanup: {cleanup_error}")

            await asyncio.sleep(delay)
            last_restart_time = time.time()

    if restart_count >= MAX_RESTART_ATTEMPTS:
        logger.critical("Bot failed to start after maximum restart attempts. Exiting.")
        sys.exit(1)


async def main() -> None:
    """Main entry point with restart logic"""
    await run_bot_with_restart()


if __name__ == "__main__":
    asyncio.run(main())
