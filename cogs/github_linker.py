import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
import os
import asyncio
import logging
from urllib.parse import urlencode

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
            self.weekly_verification.start()

    def cog_unload(self):
        self.weekly_verification.cancel()

    @tasks.loop(hours=168)
    async def weekly_verification(self):
        """Weekly task to verify all GitHub roles, remove invalid ones"""
        try:
            logger.info("Starting weekly GitHub role verification")
            for guild in self.bot.guilds:
                await self.verify_guild_roles(guild)
            logger.info("Weekly GitHub role verification completed")
        except Exception as e:
            logger.error(f"Error during weekly verification: {e}")

    @weekly_verification.error
    async def weekly_verification_error(self, error):
        logger.error(f"Weekly verification task error: {error}")
        if not self.weekly_verification.is_running():
            self.weekly_verification.restart()

    @weekly_verification.before_loop
    async def before_weekly_verification(self):
        await self.bot.wait_until_ready()

    async def verify_guild_roles(self, guild):
        if not GITHUB_TOKEN:
            logger.info(
                f"Skipping verification for {guild.name}, no GitHub token configured"
            )
            return

        stargazer_role = discord.utils.get(guild.roles, name=STAR_ROLE_NAME)

        if not stargazer_role:
            return

        for member in list(stargazer_role.members):
            try:
                data = await self.get_github_data(str(member.id))
                if not data:
                    continue
                still_ok = await self.verify_role_qualification(data, "stargazer")
                if not still_ok:
                    await member.remove_roles(
                        stargazer_role,
                        reason="Weekly check, no longer qualifies for stargazer",
                    )
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error verifying {member.display_name}: {e}")
                continue
        logger.info(f"Guild {guild.name}: verification pass complete")

    async def get_github_data(self, discord_id: str):
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
        github_username = github_data.get("github_username")
        if not github_username:
            return False

        if role_type == "stargazer":
            return await self.check_stargazer_status(github_username)
        return False

    async def check_stargazer_status(self, github_username: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
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
            return True

    role = app_commands.Group(name="role", description="Get GitHub-related roles")

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
                        f"Failed to generate a secure link. Status: {resp.status} Text: {await resp.text()}",
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
            "Click below to link your GitHub account then use `/role contributor` or `/role stargazer`",
            view=view,
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
        if not github_id:
            await interaction.followup.send(
                "You haven't linked your GitHub account, use `/linkgithub`",
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
                    f"The role `{ROLE_NAME}` does not exist", ephemeral=True
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
                    "I lack permission to assign roles", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention}, you're not a contributor to `{REPO_OWNER}/{REPO_NAME}`",
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
                "You haven't linked your GitHub account, use `/linkgithub`",
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
                    f"The role `{STAR_ROLE_NAME}` does not exist", ephemeral=True
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
                    "I lack permission to assign roles", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention}, you haven't starred `{REPO_OWNER}/{REPO_NAME}`",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(GithubRolesCog(bot))
