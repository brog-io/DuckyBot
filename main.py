import discord
from discord.ext import commands
import asyncio
import aiohttp
import json
import logging
import os
from dotenv import load_dotenv
from utils.rate_limiter import RateLimiter
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from utils.logging_formatter import DetailedFormatter
import importlib.util
import inspect

# Load environment variables from .env file
load_dotenv()

# Constants
LOG_DIR = "logs"
LOG_FILE_SIZE = 10_000_000  # 10MB
LOG_BACKUP_COUNT = 30
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")


def setup_logging() -> logging.Logger:
    """
    Set up logging for the application, including console and file handlers, and sensitive data filtering.
    Returns the logger instance for this module.
    """
    # Create logs directory if it doesn't exist
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d")
    log_file = f"{LOG_DIR}/discord_{timestamp}.log"

    # Configure root logger
    root_logger = logging.getLogger()
    log_level = getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO)
    root_logger.setLevel(log_level)

    # Prevent duplicate handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Create console handler with a higher log level
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

    # Create file handler with rotation
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_FILE_SIZE, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)  # More detailed logging to file

    # Create formatters
    console_format = logging.Formatter("%(levelname)-8s %(name)-12s: %(message)s")
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s"
    )

    # Apply formatters
    console_handler.setFormatter(console_format)
    file_handler.setFormatter(file_format)

    # Add sensitive data filter
    sensitive_filter = SensitiveDataFilter()
    console_handler.addFilter(sensitive_filter)
    file_handler.addFilter(sensitive_filter)

    # Add handlers to root logger
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Set specific log levels for noisy libraries
    for logger_name in ["discord", "discord.http", "discord.gateway", "aiohttp"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    return logging.getLogger(__name__)


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter to sanitize sensitive data from log messages.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg"):
            record.msg = self._sanitize_message(record.msg)
        return True

    def _sanitize_message(self, message):
        # Add sanitization logic here (e.g., mask tokens, emails, etc.)
        return message


class EnteBot(commands.Bot):
    """
    Main Discord bot class for EnteBot.
    """

    def __init__(self, config_path: str = None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="d!", intents=intents)
        self.logger = logging.getLogger(__name__)
        self.config = self.load_config(config_path or DEFAULT_CONFIG_PATH)
        self.http_session: aiohttp.ClientSession | None = None
        self.user_limiter = RateLimiter(rate=1, per=30)
        self.guild_limiter = RateLimiter(rate=6, per=60)

    def load_config(self, config_path: str) -> dict:
        """
        Load and validate the bot configuration from a JSON file.
        """
        try:
            with open(config_path, "r") as config_file:
                config = json.load(config_file)
            # Validate config structure (example: check for required keys)
            required_keys = []  # Add required keys if any
            for key in required_keys:
                if key not in config:
                    raise KeyError(f"Missing required config key: {key}")
            return config
        except FileNotFoundError:
            self.logger.error(f"Configuration file '{config_path}' not found.")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing JSON configuration file: {e}")
            raise
        except KeyError as e:
            self.logger.error(f"Configuration validation error: {e}")
            raise

    async def setup_hook(self) -> None:
        """
        Async setup hook for loading cogs and persistent views.
        """
        try:
            self.logger.info("Starting setup_hook...")
            self.http_session = aiohttp.ClientSession()

            # Dynamically load all cogs from the cogs directory
            cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
            for filename in os.listdir(cogs_dir):
                if filename.endswith(".py") and not filename.startswith("__"):
                    module_name = f"cogs.{filename[:-3]}"
                    try:
                        spec = importlib.util.find_spec(module_name)
                        if spec is None:
                            self.logger.warning(
                                f"Could not find spec for {module_name}"
                            )
                            continue
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        # Find the first class that subclasses commands.Cog
                        for name, obj in inspect.getmembers(module, inspect.isclass):
                            if (
                                issubclass(obj, commands.Cog)
                                and obj is not commands.Cog
                            ):
                                cog_instance = obj(self)
                                await self.add_cog(cog_instance)
                                self.logger.info(f"Loaded cog: {obj.__name__}")
                                break
                        else:
                            self.logger.warning(f"No Cog class found in {module_name}")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to load cog from {module_name}: {e}", exc_info=True
                        )

            # Setup persistent view (manual for now, as these are not standard cogs)
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
            self.add_view(view)  # This makes the button persistent across restarts

            # Add StarCounter view setup
            star_view = StarCounterView()
            star_view.add_item(StarCounterRefresh())
            self.add_view(star_view)  # Make the StarCounter button persistent too

            # Log before syncing commands
            self.logger.info("Attempting to sync commands...")
            await self.tree.sync(guild=discord.Object(id=948937918347608085))
            self.logger.info("Command sync completed")

        except Exception as e:
            self.logger.error(f"Error in setup_hook: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        """
        Gracefully close the bot and its resources.
        """
        self.logger.info("Bot is shutting down...")
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.logger.info(f"Bot is ready. Logged in as {self.user}")


def validate_env_vars() -> None:
    """
    Validate required environment variables are set.
    """
    if os.getenv("DISCORD_TOKEN") is None:
        raise EnvironmentError("DISCORD_TOKEN environment variable not found.")


async def main() -> None:
    """
    Main entry point for running the bot.
    """
    logger = setup_logging()

    try:
        logger.info("Starting bot...")
        validate_env_vars()
        bot = EnteBot()
        async with bot:
            token = os.getenv("DISCORD_TOKEN")
            logger.info("Bot initialized, connecting to Discord...")
            try:
                await bot.start(token)
            except discord.LoginFailure as e:
                logger.error(
                    f"Failed to login: Invalid token or connection issues: {e}",
                    exc_info=True,
                )
                raise
            except discord.ConnectionClosed as e:
                logger.error(f"Discord connection closed: {e}", exc_info=True)
                raise
            except Exception as e:
                logger.error(f"Unexpected error during bot.start(): {e}", exc_info=True)
                raise
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
