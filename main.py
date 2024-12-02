import discord
from discord.ext import commands
import asyncio
import aiohttp
import json
import os
import logging
from cogs.file_tracker import FileTracker
from cogs.message_links import MessageLinks
from cogs.member_manager import MemberManager
from cogs.log_file_warning import LogFileWarning
from utils.rate_limiter import RateLimiter

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class EnteBot(commands.Bot):
    def __init__(self, config_path: str = "config.json"):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="d!", intents=intents)
        self.config = self.load_config(config_path)
        self.http_session = None
        self.user_limiter = RateLimiter(rate=1, per=30)
        self.guild_limiter = RateLimiter(rate=6, per=60)

    def load_config(self, config_path: str):
        """
        Load the configuration file.
        :param config_path: Path to the JSON configuration file.
        :return: Dictionary with the configuration data.
        """
        try:
            with open(config_path, "r") as config_file:
                return json.load(config_file)
        except FileNotFoundError:
            logger.error(f"Configuration file '{config_path}' not found.")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON configuration file: {e}")
            raise

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

        # Load cogs
        await self.add_cog(FileTracker(self))
        await self.add_cog(MessageLinks(self))
        await self.add_cog(MemberManager(self))
        await self.add_cog(LogFileWarning(self))

        # Setup persistent view
        from cogs.file_tracker import PersistentView, RefreshButton

        view = PersistentView()
        view.add_item(RefreshButton())
        self.add_view(view)  # This makes the button persistent across restarts

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


async def main():
    try:
        bot = EnteBot()
        async with bot:
            await bot.start(bot.config["discord_token"])
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
