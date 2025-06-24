import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
import os
import json
import asyncio
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)
load_dotenv()

WORKER_URL = "https://brog.io"
ROLE_NAME = "Contributor"
STAR_ROLE_NAME = "Stargazer"
SPONSOR_ROLE_NAME = "Sponsor"  # New sponsor role
REPO_OWNER = "ente-io"
REPO_NAME = "ente"
API_KEY = os.getenv("LOOKUP_API_KEY")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Required for GraphQL API


class LinkGithubButton(ui.View):
    def __init__(self, oauth_url: str, *, timeout=120):
        super().__init__(timeout=timeout)
        self.add_item(
            discord.ui.Button(
                label="Link GitHub via Discord",
                url=oauth_url,
                style=discord.ButtonStyle.link,
            )
        )


class GithubRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not self.weekly_verification.is_running():
            self.weekly_verification.start()  # Start the weekly task

    def cog_unload(self):
        self.weekly_verification.cancel()  # Clean up when cog is unloaded

    @tasks.loop(hours=168)  # 168 hours = 1 week
    async def weekly_verification(self):
        """Weekly task to verify all GitHub roles and remove invalid ones"""
        try:
            logger.info("Starting weekly GitHub role verification...")

            for guild in self.bot.guilds:
                await self.verify_guild_roles(guild)

            logger.info("Weekly GitHub role verification completed.")
        except Exception as e:
            logger.error(f"Error during weekly verification: {e}")

    @weekly_verification.error
    async def weekly_verification_error(self, error):
        logger.error(f"Weekly verification task error: {error}")
        # Optionally restart the task
        if not self.weekly_verification.is_running():
            self.weekly_verification.restart()

    @weekly_verification.before_loop
    async def before_weekly_verification(self):
        await self.bot.wait_until_ready()  # Wait until bot is ready

    async def verify_guild_roles(self, guild):
        """Verify GitHub roles for all members in a guild"""
        if not GITHUB_TOKEN:
            logger.warning(
                f"Skipping verification for {guild.name} - no GitHub token configured"
            )
            return

        # Get the roles we manage (excluding contributor - contributions are permanent)
        stargazer_role = discord.utils.get(guild.roles, name=STAR_ROLE_NAME)
        sponsor_role = discord.utils.get(guild.roles, name=SPONSOR_ROLE_NAME)

        roles_to_check = []
        if stargazer_role:
            roles_to_check.append(("stargazer", stargazer_role))
        if sponsor_role:
            roles_to_check.append(("sponsor", sponsor_role))

        if not roles_to_check:
            logger.info(f"No verifiable GitHub roles found in {guild.name}")
            return

        verification_count = 0
        removal_count = 0

        # Check each role type
        for role_type, role in roles_to_check:
            logger.info(f"Checking {role_type} role in {guild.name}...")

            for member in role.members:
                try:
                    verification_count += 1

                    # Get GitHub info for this user
                    github_data = await self.get_github_data(str(member.id))
                    if not github_data:
                        continue  # Skip if no GitHub data

                    # Check if they still qualify for this role
                    still_qualifies = await self.verify_role_qualification(
                        github_data, role_type
                    )

                    if not still_qualifies:
                        await member.remove_roles(
                            role,
                            reason=f"Weekly verification: no longer qualifies for {role_type}",
                        )
                        removal_count += 1
                        logger.info(
                            f"Removed {role_type} role from {member.display_name}"
                        )

                        # Add small delay to avoid rate limiting
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.info(f"Error verifying {member.display_name}: {e}")
                    continue

        logger.info(
            f"Guild {guild.name}: Verified {verification_count} users, removed {removal_count} roles"
        )

    async def get_github_data(self, discord_id: str):
        """Get GitHub data for a Discord user"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{WORKER_URL}/api/lookup?discord_id={discord_id}",
                    headers={"x-api-key": API_KEY},
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception:
            return None

    async def verify_role_qualification(self, github_data, role_type: str) -> bool:
        """Check if user still qualifies for a specific role type"""
        github_id = github_data.get("github_id")
        github_username = github_data.get("github_username")

        if not github_id or not github_username:
            return False

        try:
            if role_type == "stargazer":
                return await self.check_stargazer_status(github_username)
            elif role_type == "sponsor":
                return await self.check_sponsorship(github_id, REPO_OWNER)
            return False
        except Exception:
            return True  # If check fails, keep the role (benefit of doubt)

    async def check_stargazer_status(self, github_username: str) -> bool:
        """Check if user still has the repo starred"""
        try:
            async with aiohttp.ClientSession() as session:
                # Check if they starred the repo (simplified check)
                async with session.get(
                    f"https://api.github.com/users/{github_username}/starred?per_page=100"
                ) as resp:
                    if resp.status == 200:
                        stars = await resp.json()
                        return any(
                            r.get("full_name", "").lower()
                            == f"{REPO_OWNER}/{REPO_NAME}".lower()
                            for r in stars
                        )
            return False
        except Exception:
            return True  # Keep role if check fails

    role = app_commands.Group(name="role", description="Get GitHub-related roles.")

    @app_commands.command(
        name="linkgithub", description="Link your GitHub account to your Discord."
    )
    async def linkgithub(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        state = os.urandom(16).hex()

        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{WORKER_URL}/api/stateset",
                headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                json={"state": state, "discord_id": discord_id, "ttl": 600},
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"Failed to generate a secure link.\nStatus: {resp.status}\nText: {await resp.text()}",
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
            "Click the button below to link your GitHub account. Then run `/role contributor`, `/role stargazer`, or `/role sponsor`.",
            view=view,
            ephemeral=True,
        )

    async def check_sponsorship(self, github_id: str, sponsorable: str) -> bool:
        """Check if a user is sponsoring the given account using GraphQL API with pagination"""
        if not GITHUB_TOKEN:
            return False

        # GraphQL query with pagination support - using ID instead of login
        query = """
        query($sponsorable: String!, $after: String) {
          user(login: $sponsorable) {
            sponsorshipsAsMaintainer(first: 100, after: $after) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                sponsor {
                  id
                  login
                }
                isActive
              }
            }
          }
        }
        """

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
        }

        cursor = None

        async with aiohttp.ClientSession() as session:
            while True:
                variables = {"sponsorable": sponsorable, "after": cursor}

                async with session.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": query, "variables": variables},
                ) as resp:
                    if resp.status != 200:
                        return False

                    data = await resp.json()

                    if "errors" in data:
                        return False

                    user_data = data.get("data", {}).get("user")
                    if not user_data:
                        return False

                    sponsorships_data = user_data.get("sponsorshipsAsMaintainer", {})
                    sponsorships = sponsorships_data.get("nodes", [])

                    # Check if the github_id is in this batch of sponsors
                    for sponsorship in sponsorships:
                        sponsor = sponsorship.get("sponsor", {})
                        if sponsor.get("id") == github_id and sponsorship.get(
                            "isActive", False
                        ):
                            return True

                    # Check if there are more pages
                    page_info = sponsorships_data.get("pageInfo", {})
                    if not page_info.get("hasNextPage", False):
                        break

                    cursor = page_info.get("endCursor")
                    if not cursor:
                        break

                return False

    @role.command(
        name="sponsor", description="Get the Sponsor role if you sponsor ente-io."
    )
    async def sponsor(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # Check if GitHub token is configured
        if not GITHUB_TOKEN:
            await interaction.followup.send(
                "Sponsor checking is not configured. Please contact an administrator.",
                ephemeral=True,
            )
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}",
                headers={"x-api-key": API_KEY},
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"Lookup failed: {resp.status} {await resp.text()}",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()

        github_id = data.get("github_id")
        github_username = data.get("github_username")
        if not github_id:
            await interaction.followup.send(
                "You haven't linked your GitHub account. Use `/linkgithub`.",
                ephemeral=True,
            )
            return

        # Check if user is sponsoring the repository owner
        is_sponsor = await self.check_sponsorship(github_id, REPO_OWNER)

        if is_sponsor:
            role = discord.utils.get(interaction.guild.roles, name=SPONSOR_ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"The role `{SPONSOR_ROLE_NAME}` does not exist.", ephemeral=True
                )
                return
            try:
                await interaction.user.add_roles(role, reason="GitHub sponsor")
                await interaction.followup.send(
                    f"{interaction.user.mention}, you've been given the `{SPONSOR_ROLE_NAME}` role!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I lack permission to assign roles.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention}, you're not currently sponsoring `{REPO_OWNER}` ({github_username}). "
                f"Visit https://github.com/sponsors/{REPO_OWNER} to become a sponsor!",
                ephemeral=True,
            )

    @role.command(
        name="contributor", description="Get the Contributor role for ente-io/ente."
    )
    async def contributor(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}",
                headers={"x-api-key": API_KEY},
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"Lookup failed: {resp.status} {await resp.text()}",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()

        github_id = data.get("github_id")
        github_username = data.get("github_username")
        if not github_id:
            await interaction.followup.send(
                "You haven't linked your GitHub account. Use `/linkgithub`.",
                ephemeral=True,
            )
            return

        gh_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "brogio-discord-link",
        }

        contributors = []
        page = 1
        while True:
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contributors?per_page=100&page={page}&anon=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=gh_headers) as resp:
                    if resp.status != 200:
                        break
                    page_data = await resp.json()
                    if not page_data:
                        break
                    contributors.extend(page_data)
                    if len(page_data) < 100:
                        break
                    page += 1

        is_contributor = any(
            str(c.get("id")) == str(github_id) for c in contributors if c.get("id")
        )

        if not is_contributor:
            page = 1
            while page <= 10:
                url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls?state=closed&per_page=100&page={page}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=gh_headers) as resp:
                        if resp.status != 200:
                            break
                        prs = await resp.json()
                        for pr in prs:
                            user = pr.get("user")
                            if (
                                user
                                and user.get("id") == github_id
                                and pr.get("merged_at")
                            ):
                                is_contributor = True
                                break
                        if is_contributor or len(prs) < 100:
                            break
                page += 1

        if is_contributor:
            role = discord.utils.get(interaction.guild.roles, name=ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"The role `{ROLE_NAME}` does not exist.", ephemeral=True
                )
                return
            try:
                await interaction.user.add_roles(role, reason="GitHub contributor")
                await interaction.followup.send(
                    f"{interaction.user.mention}, you've been given the `{ROLE_NAME}` role!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I lack permission to assign roles.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention}, you're not a contributor to `{REPO_OWNER}/{REPO_NAME}` ({github_username}).",
                ephemeral=True,
            )

    @role.command(
        name="stargazer",
        description="Get the Stargazer role if you starred ente-io/ente.",
    )
    async def starred(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}",
                headers={"x-api-key": API_KEY},
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"Lookup failed: {resp.status} {await resp.text()}",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()

        github_username = data.get("github_username")
        if not github_username:
            await interaction.followup.send(
                "You haven't linked your GitHub account. Use `/linkgithub`.",
                ephemeral=True,
            )
            return

        starred = False
        page = 1
        while page <= 10:
            url = f"https://api.github.com/users/{github_username}/starred?per_page=100&page={page}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        break
                    stars = await resp.json()
                    if any(
                        r.get("full_name", "").lower()
                        == f"{REPO_OWNER}/{REPO_NAME}".lower()
                        for r in stars
                    ):
                        starred = True
                        break
                    if len(stars) < 100:
                        break
            page += 1

        if starred:
            role = discord.utils.get(interaction.guild.roles, name=STAR_ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"The role `{STAR_ROLE_NAME}` does not exist.", ephemeral=True
                )
                return
            try:
                await interaction.user.add_roles(role, reason="GitHub stargazer")
                await interaction.followup.send(
                    f"{interaction.user.mention}, you've been given the `{STAR_ROLE_NAME}` role!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I lack permission to assign roles.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention}, you haven't starred `{REPO_OWNER}/{REPO_NAME}` ({github_username}).",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(GithubRolesCog(bot))
