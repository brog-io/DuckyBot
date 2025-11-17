from discord.ext import commands, tasks
import discord
from discord import app_commands
import logging
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta, UTC
from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

GITHUB_API_URL = "https://api.github.com/repos/ente-io/ente"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Load GitHub token from .env


class PersistentView(View):
    def __init__(self):
        super().__init__(timeout=None)


class RefreshButton(Button):
    def __init__(self):
        super().__init__(
            label="Refresh Stars",
            style=discord.ButtonStyle.primary,
            custom_id="refresh_stars",
            emoji=":refresh:1439928076195860613",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("StarCounter")
        if cog:
            await cog.handle_refresh(interaction)


class StarCounter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_count = None
        self.last_channel_edit = datetime.now(UTC)
        self.minimum_edit_interval = timedelta(minutes=5)
        self.star_count_cache = None
        self.last_cache_update = None
        self.cache_duration = timedelta(minutes=5)
        self.button_cooldowns = {}  # Add cooldown tracking
        self.monitor_stars.start()

    def cog_unload(self):
        self.monitor_stars.cancel()

    async def safe_channel_edit(self, channel, new_name):
        """Safely edit channel name with rate limit consideration"""
        now = datetime.now(UTC)
        time_since_last_edit = now - self.last_channel_edit

        if time_since_last_edit < self.minimum_edit_interval:
            wait_time = (
                self.minimum_edit_interval - time_since_last_edit
            ).total_seconds()
            logger.info(f"Waiting {wait_time:.2f}s before next channel edit")
            await asyncio.sleep(wait_time)

        try:
            await channel.edit(name=new_name)
            self.last_channel_edit = datetime.now(UTC)
        except discord.HTTPException as e:
            if e.code == 429:  # Rate limit error
                retry_after = e.retry_after
                logger.warning(
                    f"Rate limited on channel edit. Retry after: {retry_after}s"
                )
                if retry_after > self.minimum_edit_interval.total_seconds():
                    self.minimum_edit_interval = timedelta(
                        seconds=retry_after * 1.1
                    )  # Add 10% buffer
                await asyncio.sleep(retry_after)
                await channel.edit(name=new_name)
                self.last_channel_edit = datetime.now(UTC)
            else:
                raise

    async def fetch_star_count(self) -> int | None:
        """Fetch the star count from GitHub API with caching."""
        # Check cache first
        if (
            self.star_count_cache is not None
            and self.last_cache_update is not None
            and datetime.now(UTC) - self.last_cache_update < self.cache_duration
        ):
            return self.star_count_cache

        headers = {}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"

        try:
            async with self.bot.http_session.get(
                GITHUB_API_URL, headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.star_count_cache = data.get("stargazers_count")
                    self.last_cache_update = datetime.now(UTC)
                    return self.star_count_cache
                elif response.status == 403:
                    logger.error("GitHub API rate limit exceeded")
                    return self.star_count_cache  # Return cached value if available
                else:
                    logger.error(f"Failed to fetch star count: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching star count: {e}")
            return self.star_count_cache  # Return cached value if available

    @tasks.loop(seconds=300)
    async def monitor_stars(self):
        try:
            channel = self.bot.get_channel(int(self.bot.config["starchannel_id"]))
            if not channel:
                return

            current_count = await self.fetch_star_count()

            if current_count is not None and current_count != self.last_count:
                await self.safe_channel_edit(channel, f"⭐ {current_count:,} Stars")
                self.last_count = current_count
                logger.info(
                    f"Updated channel name to {current_count:,} stars. Minimum edit interval: {self.minimum_edit_interval.total_seconds()}s"
                )
        except Exception as e:
            logger.error(f"Error in star monitoring: {e}", exc_info=True)

    @monitor_stars.before_loop
    async def before_monitor_stars(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="stars", description="Get the current star count for ente-io on GitHub"
    )
    async def stars(self, interaction: discord.Interaction):
        await self.handle_refresh(interaction)

    async def handle_refresh(self, interaction: discord.Interaction):
        try:
            # Check cooldown
            user_id = interaction.user.id
            current_time = datetime.now(UTC)
            if user_id in self.button_cooldowns:
                time_elapsed = current_time - self.button_cooldowns[user_id]
                if time_elapsed < timedelta(seconds=30):  # 30 second cooldown
                    remaining = round(30 - time_elapsed.total_seconds())
                    await interaction.response.send_message(
                        f"🐣 *Quack!* I need to catch my breath! Try again in {remaining} seconds! 🕒",
                        ephemeral=True,
                    )
                    return

            await interaction.response.defer()
            # Force a fresh fetch by temporarily clearing the cache
            old_cache = self.star_count_cache
            old_cache_time = self.last_cache_update
            self.star_count_cache = None
            self.last_cache_update = None

            current_count = await self.fetch_star_count()

            # Restore cache if fetch failed
            if current_count is None:
                self.star_count_cache = old_cache
                self.last_cache_update = old_cache_time

            # Update cooldown
            self.button_cooldowns[user_id] = current_time

            if current_count is not None:
                star_embed = discord.Embed(
                    title="GitHub Star Count",
                    description=f"[`ente-io/ente`]({GITHUB_API_URL}) currently has **{current_count:,}** stars ⭐",
                    color=0xFFD700,
                    timestamp=discord.utils.utcnow(),
                )

                view = PersistentView()
                view.add_item(RefreshButton())

                try:
                    await interaction.message.edit(embed=star_embed, view=view)
                except Exception as e:
                    await interaction.followup.send(embed=star_embed, view=view)
            else:
                await interaction.followup.send(
                    "Failed to fetch the star count. Please try again later.",
                    ephemeral=True,
                )
        except Exception as e:
            logger.error(f"Error fetching star count: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while fetching the count. Please try again later.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(StarCounter(bot))
