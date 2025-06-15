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

logging.captureWarnings(True)
load_dotenv()

LOG_DIR = "logs"
LOG_FILE_SIZE = 10_000_000
LOG_BACKUP_COUNT = 30
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")


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
        super().__init__(command_prefix="d!", intents=intents)
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


async def main() -> None:
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
