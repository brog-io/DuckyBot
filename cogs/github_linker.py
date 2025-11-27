import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
import os
import asyncio
import logging
from urllib.parse import urlencode
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

load_dotenv()

WORKER_URL = "https://brog.io"
ROLE_NAME = "Contributor"
STAR_ROLE_NAME = "Stargazer"
REPO_OWNER = "ente-io"
REPO_NAME = "ente"
API_KEY = os.getenv("LOOKUP_API_KEY")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

LINK_PROMPT = "You have not linked your GitHub account, use `/linkgithub`. \n -# After linking, use `/role contributor` or `/role stargazer` to get the role. It might take a minute for command to work after linking."


class LinkGithubButton(ui.View):
    def __init__(self, oauth_url: str, *, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(
            discord.ui.Button(
                label="Link GitHub via Discord",
                url=oauth_url,
                style=discord.ButtonStyle.link,
            )
        )


class GithubRolesCog(commands.Cog):
    role = app_commands.Group(
        name="role",
        description="Get GitHub related roles",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Shared HTTP session for all worker and GitHub calls made by this cog.
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()

        missing_env = []
        if API_KEY is None:
            missing_env.append("LOOKUP_API_KEY")
        if DISCORD_CLIENT_ID is None:
            missing_env.append("DISCORD_CLIENT_ID")
        if missing_env:
            logger.error(
                "Missing required environment variables: %s",
                ", ".join(missing_env),
            )
        if GITHUB_TOKEN is None:
            logger.warning(
                "GITHUB_TOKEN is not set, GitHub API calls will be unauthenticated"
            )

        if not self.weekly_verification.is_running():
            self.weekly_verification.start()

    def cog_unload(self) -> None:
        self.weekly_verification.cancel()
        if not self.session.closed:
            # Close the aiohttp session asynchronously so unload does not block.
            self.bot.loop.create_task(self.session.close())

    def _get_github_headers(self) -> Dict[str, str]:
        """Build GitHub API headers using the bot token if available."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "brogio-discord-link",
        }
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        return headers

    async def _get_linked_github(self, discord_id: str) -> Optional[Dict[str, Any]]:
        """Look up the linked GitHub info from your worker by Discord user id."""
        if API_KEY is None:
            logger.error("LOOKUP_API_KEY is not configured")
            return None

        try:
            async with self.session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}",
                headers={"x-api-key": API_KEY},
            ) as resp:
                if resp.status != 200:
                    logger.info(
                        "Lookup for Discord id %s returned status %s",
                        discord_id,
                        resp.status,
                    )
                    return None
                return await resp.json()
        except Exception as exc:
            logger.error("Error during GitHub lookup for %s: %s", discord_id, exc)
            return None

    async def _store_state(self, state: str, discord_id: str, ttl: int = 600) -> bool:
        """Store a temporary OAuth state in your worker."""
        if API_KEY is None:
            logger.error("LOOKUP_API_KEY is not configured")
            return False

        try:
            async with self.session.put(
                f"{WORKER_URL}/api/stateset",
                headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                json={"state": state, "discord_id": discord_id, "ttl": ttl},
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Failed to store state for %s, status %s, text %s",
                        discord_id,
                        resp.status,
                        await resp.text(),
                    )
                    return False
                return True
        except Exception as exc:
            logger.error("Exception while storing state for %s: %s", discord_id, exc)
            return False

    async def _has_starred_repo(self, github_username: str) -> str:
        """
        Check if a GitHub user starred the configured repo.

        Returns: "valid", "invalid", or "error".
        """
        max_retries = 3
        retry_delay_seconds = 2
        max_pages = 10
        headers = self._get_github_headers()

        for attempt in range(max_retries):
            try:
                page = 1
                while page <= max_pages:
                    url = (
                        f"https://api.github.com/users/{github_username}/starred"
                        f"?per_page=100&page={page}"
                    )
                    async with self.session.get(url, headers=headers) as resp:
                        if resp.status == 403:
                            logger.warning(
                                "Rate limited when checking stars for %s "
                                "(attempt %s of %s)",
                                github_username,
                                attempt + 1,
                                max_retries,
                            )
                            break

                        if resp.status != 200:
                            logger.warning(
                                "GitHub API returned %s when checking stars for %s",
                                resp.status,
                                github_username,
                            )
                            return "error"

                        stars = await resp.json()
                        target_full_name = f"{REPO_OWNER}/{REPO_NAME}".lower()
                        if any(
                            r.get("full_name", "").lower() == target_full_name
                            for r in stars
                        ):
                            return "valid"

                        if len(stars) < 100:
                            break

                        page += 1

                # No star found in scanned pages
                # If we hit a 403 above we treat it as an error and retry
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay_seconds * (attempt + 1))
                    continue
                return "invalid"

            except Exception as exc:
                logger.error(
                    "Exception while checking stars for %s: %s",
                    github_username,
                    exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay_seconds * (attempt + 1))
                    continue
                return "error"

        return "error"

    async def _is_contributor(self, github_id: int) -> str:
        """
        Check if a GitHub user id is a contributor to the configured repo.

        Returns: "valid", "invalid", or "error".
        """
        headers = self._get_github_headers()
        max_pages_contributors = 50
        max_pages_prs = 10

        try:
            # Contributors list
            page = 1
            while page <= max_pages_contributors:
                url = (
                    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
                    f"/contributors?per_page=100&page={page}&anon=1"
                )
                async with self.session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "GitHub API returned %s for contributors check",
                            resp.status,
                        )
                        break

                    page_data = await resp.json()
                    if any(
                        c.get("id") is not None and int(c["id"]) == int(github_id)
                        for c in page_data
                    ):
                        return "valid"

                    if len(page_data) < 100:
                        break

                    page += 1

            # Fallback to merged PRs
            page = 1
            while page <= max_pages_prs:
                url = (
                    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
                    f"/pulls?state=closed&per_page=100&page={page}"
                )
                async with self.session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "GitHub API returned %s for PR check",
                            resp.status,
                        )
                        break

                    prs = await resp.json()
                    for pr in prs:
                        user = pr.get("user")
                        if user and user.get("id") == github_id and pr.get("merged_at"):
                            return "valid"

                    if len(prs) < 100:
                        break

                    page += 1

            return "invalid"

        except Exception as exc:
            logger.error("Exception while checking contributor status: %s", exc)
            return "error"

    async def verify_guild_roles(self, guild: discord.Guild) -> None:
        """
        Weekly verification of Stargazer roles in a guild.

        If the user no longer has a valid GitHub link or no longer stars the repo,
        their Stargazer role is removed.
        """
        if not GITHUB_TOKEN:
            logger.warning(
                "Skipping verification for guild %s, no GitHub token configured",
                guild.name,
            )
            return

        stargazer_role = discord.utils.get(guild.roles, name=STAR_ROLE_NAME)
        if not stargazer_role:
            return

        removed_count = 0
        error_count = 0

        for member in list(stargazer_role.members):
            try:
                data = await self._get_linked_github(str(member.id))
                if not data:
                    # No GitHub data, remove the role
                    await member.remove_roles(
                        stargazer_role,
                        reason="Weekly check, GitHub link missing",
                    )
                    removed_count += 1
                    logger.info(
                        "Removed Stargazer role from %s, no GitHub data",
                        member.display_name,
                    )
                    await asyncio.sleep(1)
                    continue

                github_username = data.get("github_username")
                if not github_username:
                    await member.remove_roles(
                        stargazer_role,
                        reason="Weekly check, missing GitHub username",
                    )
                    removed_count += 1
                    logger.info(
                        "Removed Stargazer role from %s, missing GitHub username",
                        member.display_name,
                    )
                    await asyncio.sleep(1)
                    continue

                result = await self._has_starred_repo(github_username)

                if result == "invalid":
                    await member.remove_roles(
                        stargazer_role,
                        reason="Weekly check, no longer stars the repo",
                    )
                    removed_count += 1
                    logger.info(
                        "Removed Stargazer role from %s, no longer stars the repo",
                        member.display_name,
                    )
                    await asyncio.sleep(1)
                elif result == "error":
                    error_count += 1
                    logger.warning(
                        "Could not verify Stargazer status for %s, keeping role",
                        member.display_name,
                    )
                # "valid" keeps role

            except Exception as exc:
                logger.error(
                    "Error verifying Stargazer role for %s: %s",
                    member.display_name,
                    exc,
                )
                error_count += 1
                continue

        logger.info(
            "Guild %s, Stargazer verification complete. Removed: %s, Errors: %s",
            guild.name,
            removed_count,
            error_count,
        )

    def _ensure_guild(self, interaction: Interaction) -> bool:
        """Make sure command is used in a guild where roles exist."""
        if interaction.guild is None:
            # Response is always deferred before calling this helper
            self.bot.loop.create_task(
                interaction.followup.send(
                    "This command can only be used inside a server.",
                    ephemeral=True,
                )
            )
            return False
        return True

    @tasks.loop(hours=168)
    async def weekly_verification(self) -> None:
        """Weekly task to verify all GitHub roles, removing invalid ones."""
        try:
            logger.info("Starting weekly GitHub role verification")
            for guild in self.bot.guilds:
                await self.verify_guild_roles(guild)
            logger.info("Weekly GitHub role verification completed")
        except Exception as exc:
            logger.error("Error during weekly verification: %s", exc)

    @weekly_verification.error
    async def weekly_verification_error(self, error: Exception) -> None:
        logger.error("Weekly verification task error: %s", error)
        if not self.weekly_verification.is_running():
            self.weekly_verification.restart()

    @weekly_verification.before_loop
    async def before_weekly_verification(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="linkgithub",
        description="Link your GitHub account to your Discord.",
    )
    async def linkgithub(self, interaction: Interaction) -> None:
        """
        Starts the Discord OAuth flow that your worker uses to look up GitHub.
        This command can be used in DMs or in servers.
        """
        await interaction.response.defer(ephemeral=True)

        if DISCORD_CLIENT_ID is None:
            await interaction.followup.send(
                "Discord client id is not configured for linking.",
                ephemeral=True,
            )
            return

        discord_id = str(interaction.user.id)
        state = os.urandom(16).hex()

        stored = await self._store_state(state, discord_id, ttl=600)
        if not stored:
            await interaction.followup.send(
                "Failed to generate a secure link, please try again later.",
                ephemeral=True,
            )
            return

        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": f"{WORKER_URL}/auth/discord/callback",
            "response_type": "code",
            "scope": "connections",
            "state": state,
        }
        oauth_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
        view = LinkGithubButton(oauth_url)

        await interaction.followup.send(
            "Click below to link your GitHub account then use `/role contributor` or `/role stargazer` in a server.",
            view=view,
            ephemeral=True,
        )

    @role.command(
        name="contributor",
        description="Get the Contributor role for ente-io/ente.",
    )
    @app_commands.guild_only()
    async def contributor(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self._ensure_guild(interaction):
            return

        discord_id = str(interaction.user.id)
        data = await self._get_linked_github(discord_id)
        if not data:
            await interaction.followup.send(LINK_PROMPT, ephemeral=True)
            return

        github_id = data.get("github_id")
        if not github_id:
            await interaction.followup.send(LINK_PROMPT, ephemeral=True)
            return

        result = await self._is_contributor(int(github_id))

        if result == "error":
            await interaction.followup.send(
                "There was an error while checking your contributor status. Please try again later.",
                ephemeral=True,
            )
            return

        if result == "invalid":
            await interaction.followup.send(
                f"{interaction.user.mention}, you are not a contributor to `{REPO_OWNER}/{REPO_NAME}`.",
                ephemeral=True,
            )
            return

        role = discord.utils.get(interaction.guild.roles, name=ROLE_NAME)
        if not role:
            await interaction.followup.send(
                f"The role `{ROLE_NAME}` does not exist on this server.",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="GitHub contributor")
            await interaction.followup.send(
                f"{interaction.user.mention}, you have been given the `{ROLE_NAME}` role.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to assign roles.",
                ephemeral=True,
            )

    @role.command(
        name="stargazer",
        description="Get the Stargazer role if you starred ente-io/ente.",
    )
    @app_commands.guild_only()
    async def starred(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self._ensure_guild(interaction):
            return

        discord_id = str(interaction.user.id)
        data = await self._get_linked_github(discord_id)
        if not data:
            await interaction.followup.send(LINK_PROMPT, ephemeral=True)
            return

        github_username = data.get("github_username")
        if not github_username:
            await interaction.followup.send(LINK_PROMPT, ephemeral=True)
            return

        result = await self._has_starred_repo(github_username)

        if result == "error":
            await interaction.followup.send(
                "There was an error while checking your stars. Please try again later.",
                ephemeral=True,
            )
            return

        if result == "invalid":
            await interaction.followup.send(
                f"{interaction.user.mention}, you have not starred `{REPO_OWNER}/{REPO_NAME}`.",
                ephemeral=True,
            )
            return

        role = discord.utils.get(interaction.guild.roles, name=STAR_ROLE_NAME)
        if not role:
            await interaction.followup.send(
                f"The role `{STAR_ROLE_NAME}` does not exist on this server.",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="GitHub stargazer")
            await interaction.followup.send(
                f"{interaction.user.mention}, you have been given the `{STAR_ROLE_NAME}` role.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to assign roles.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GithubRolesCog(bot))
