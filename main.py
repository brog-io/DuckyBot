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

# Load environment variables from .env file
load_dotenv()

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
        await self.add_cog(StarCounter(self))
        await self.add_cog(MessageLinks(self))
        await self.add_cog(MemberManager(self))
        await self.add_cog(LogFileWarning(self))
        await self.add_cog(EnteStatus(self))
        await self.add_cog(AutoThreadReactionsCog(self))
        await self.add_cog(AutoPublish(self))
        await self.add_cog(BotStats(self))
        await self.add_cog(InformationMessage(self))
        await self.add_cog(LinkCleaner(self))
        await self.add_cog(DuplicateMessageDetector(self))
        await self.add_cog(ScamDetection(self))

        # Setup persistent view
        from cogs.file_tracker import PersistentView, RefreshButton
        from cogs.star_counter import PersistentView, RefreshButton

        view = PersistentView()
        view.add_item(RefreshButton())
        self.add_view(view)  # This makes the button persistent across restarts

        # Sync commands to a specific guild
        await self.tree.sync(guild=discord.Object(id=948937918347608085))

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Bot is ready. Logged in as {self.user}")
        # Sync commands to a specific guild
        await self.tree.sync(guild=discord.Object(id=948937918347608085))


async def main():
    try:
        bot = EnteBot()
        async with bot:
            # Retrieve the token from the environment variable
            token = os.getenv("DISCORD_TOKEN")
            if token is None:
                logger.error("Discord token not found in environment variables.")
                return
            await bot.start(token)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
