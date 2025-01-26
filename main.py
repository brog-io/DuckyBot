import discord
from discord.ext import commands
import asyncio
import aiohttp
import json
import logging
import os
from dotenv import load_dotenv
from cogs.file_tracker import FileTracker
from cogs.star_counter import StarCounter
from cogs.message_links import MessageLinks
from cogs.member_manager import MemberManager
from cogs.log_file_warning import LogFileWarning
from cogs.ente_status import EnteStatus
from cogs.auto_threads_reactions import AutoThreadReactionsCog
from cogs.auto_publish import AutoPublish
from cogs.bot_stats import BotStats
from cogs.information_message import InformationMessage
from cogs.link_cleaner import LinkCleaner
from cogs.duplicate_message_detector import DuplicateMessageDetector
from cogs.scam_detection import ScamDetection
from utils.rate_limiter import RateLimiter
from logging.handlers import RotatingFileHandler
from datetime import datetime
from utils.logging_formatter import DetailedFormatter

# Load environment variables from .env file
load_dotenv()


def setup_logging():
    # Create logs directory if it doesn't exist
    if not os.path.exists("logs"):
        os.makedirs("logs")

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d")
    log_file = f"logs/discord_{timestamp}.log"

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Create console handler with a higher log level
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Create file handler with rotation
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10_000_000, backupCount=30, encoding="utf-8"  # 10MB
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

    # Add handlers to root logger
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Set specific log levels for noisy libraries
    for logger_name in ["discord", "discord.http", "discord.gateway", "aiohttp"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    return logging.getLogger(__name__)


class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        # Remove sensitive data from logs
        if hasattr(record, "msg"):
            record.msg = self._sanitize_message(record.msg)
        return True

    def _sanitize_message(self, message):
        # Add sanitization logic here
        return message


class EnteBot(commands.Bot):
    def __init__(self, config_path: str = "config.json"):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix="d!", intents=intents, owner_id=1086344307574837309
        )
        self.logger = logging.getLogger(__name__)  # Set up logger first
        self.config = self.load_config(config_path)
        self.http_session = None
        self.user_limiter = RateLimiter(rate=1, per=30)
        self.guild_limiter = RateLimiter(rate=6, per=60)

    def load_config(self, config_path: str):
        try:
            with open(config_path, "r") as config_file:
                return json.load(config_file)
        except FileNotFoundError:
            self.logger.error(f"Configuration file '{config_path}' not found.")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing JSON configuration file: {e}")
            raise

    async def setup_hook(self):
        try:
            self.logger.info("Starting setup_hook...")
            self.http_session = aiohttp.ClientSession()

            # Load cogs
            cogs = [
                FileTracker(self),
                StarCounter(self),
                MessageLinks(self),
                MemberManager(self),
                LogFileWarning(self),
                EnteStatus(self),
                AutoThreadReactionsCog(self),
                AutoPublish(self),
                BotStats(self),
                InformationMessage(self),
                LinkCleaner(self),
                DuplicateMessageDetector(self),
                ScamDetection(self),
            ]

            for cog in cogs:
                try:
                    await self.add_cog(cog)
                    self.logger.info(f"Loaded cog: {cog.__class__.__name__}")
                except Exception as e:
                    self.logger.error(
                        f"Failed to load cog {cog.__class__.__name__}: {e}"
                    )
                    raise

            # Setup persistent view
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

    async def close(self):
        self.logger.info("Bot is shutting down...")
        if self.http_session:
            await self.http_session.close()
        await super().close()

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info(f"Bot is ready. Logged in as {self.user}")


async def main():
    logger = setup_logging()

    try:
        logger.info("Starting bot...")
        bot = EnteBot()
        async with bot:
            token = os.getenv("DISCORD_TOKEN")
            if token is None:
                raise ValueError("DISCORD_TOKEN environment variable not found")
            logger.info("Bot initialized, connecting to Discord...")
            try:
                await bot.start(token)
            except discord.LoginFailure as e:
                logger.error(
                    f"Failed to login: Invalid token or connection issues: {e}"
                )
                raise
            except discord.ConnectionClosed as e:
                logger.error(f"Discord connection closed: {e}")
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
