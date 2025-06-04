import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import secrets
import string
import os

load_dotenv()

WORKER_URL = "https://brog.io"
ROLE_NAME = "Contributor"
STAR_ROLE_NAME = "Stargazer"
REPO_OWNER = "ente-io"
REPO_NAME = "ente"
API_KEY = os.getenv("LOOKUP_API_KEY")  # Must be set as env var!


class LinkGithubButton(ui.View):
    def __init__(self, discord_id: str, worker_url: str, *, timeout=120):
        super().__init__(timeout=timeout)
        self.state = self.random_state(32)
        self.discord_id = discord_id
        self.worker_url = worker_url

    @staticmethod
    def random_state(length=32):
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @ui.button(label="Link GitHub", style=discord.ButtonStyle.link)
    async def link_button(self, interaction: Interaction, button: ui.Button):
        pass  # Button is just a link, handled below


class GithubRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    role = app_commands.Group(name="role", description="Get GitHub-related roles.")

    @role.command(
        name="contributor", description="Get the Contributor role for ente-io/ente."
    )
    async def contributor(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        headers = {"x-api-key": API_KEY}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}", headers=headers
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        "Failed to lookup your GitHub username. Have you linked your account?",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()
                github_username = data.get("github_username")
        if not github_username:
            await interaction.followup.send(
                "You haven't linked your GitHub account yet. Use `/linkgithub` first.",
                ephemeral=True,
            )
            return

        gh_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "brogio-discord-github-link",
        }
        contributors = []
        page = 1
        per_page = 100
        while True:
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contributors?per_page={per_page}&page={page}&anon=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=gh_headers) as resp:
                    if resp.status != 200:
                        break
                    new_contribs = await resp.json()
                    if not new_contribs:
                        break
                    contributors.extend(new_contribs)
                    if len(new_contribs) < per_page:
                        break
                    page += 1

        is_commit_contributor = any(
            (c.get("login") or "").lower() == github_username.lower()
            for c in contributors
            if c.get("login")
        )

        is_pr_contributor = False
        page = 1
        max_pages = 10
        while page <= max_pages:
            pr_api_url = (
                f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls"
                f"?state=closed&per_page=100&page={page}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(pr_api_url, headers=gh_headers) as resp:
                    if resp.status != 200:
                        break
                    prs = await resp.json()
                    if not prs:
                        break
                    for pr in prs:
                        if pr.get("user", {}).get(
                            "login", ""
                        ).lower() == github_username.lower() and pr.get("merged_at"):
                            is_pr_contributor = True
                            break
                    if is_pr_contributor or len(prs) < 100:
                        break
            page += 1

        if is_commit_contributor or is_pr_contributor:
            guild = interaction.guild
            member = interaction.user
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"The role `{ROLE_NAME}` does not exist. Ask an admin to create it.",
                    ephemeral=True,
                )
                return
            try:
                await member.add_roles(
                    role, reason=f"GitHub contributor to {REPO_OWNER}/{REPO_NAME}"
                )
                await interaction.followup.send(
                    f"✅ {member.mention}, you are a contributor to `{REPO_OWNER}/{REPO_NAME}` and have been given the `{ROLE_NAME}` role!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I do not have permission to assign that role.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"❌ {interaction.user.mention}, you are **not** a contributor to `{REPO_OWNER}/{REPO_NAME}` ({github_username}).",
                ephemeral=True,
            )

    @role.command(
        name="stargazer",
        description="Get the Stargazer role if you have starred ente-io/ente.",
    )
    async def starred(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        headers = {"x-api-key": API_KEY}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/api/lookup?discord_id={discord_id}", headers=headers
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        "Failed to lookup your GitHub info. Have you linked your account?",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()
                github_username = data.get("github_username")
                access_token = data.get("access_token")
        if not github_username or not access_token:
            await interaction.followup.send(
                "You haven't linked your GitHub account with the latest method (or need to relink for star check). Use `/linkgithub` first.",
                ephemeral=True,
            )
            return

        url = f"https://api.github.com/user/starred/{REPO_OWNER}/{REPO_NAME}"
        gh_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "brogio-discord-github-link",
            "Authorization": f"token {access_token}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=gh_headers) as resp:
                if resp.status == 204:
                    starred = True
                elif resp.status == 404:
                    starred = False
                else:
                    await interaction.followup.send(
                        "Failed to check star status from GitHub.", ephemeral=True
                    )
                    return

        if starred:
            guild = interaction.guild
            member = interaction.user
            role = discord.utils.get(guild.roles, name=STAR_ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"The role `{STAR_ROLE_NAME}` does not exist. Ask an admin to create it.",
                    ephemeral=True,
                )
                return
            try:
                await member.add_roles(
                    role, reason=f"Starred GitHub repo {REPO_OWNER}/{REPO_NAME}"
                )
                await interaction.followup.send(
                    f"🌟 {member.mention}, you have starred `{REPO_OWNER}/{REPO_NAME}` and have been given the `{STAR_ROLE_NAME}` role!",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I do not have permission to assign that role.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"❌ {interaction.user.mention}, you have **not** starred `{REPO_OWNER}/{REPO_NAME}` ({github_username}).",
                ephemeral=True,
            )

    @app_commands.command(
        description="Link your GitHub account to your Discord securely."
    )
    async def linkgithub(self, interaction: Interaction):
        discord_id = str(interaction.user.id)
        view = LinkGithubButton(discord_id, WORKER_URL)
        headers = {"x-api-key": API_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{WORKER_URL}/api/stateset",
                json={"state": view.state, "discord_id": discord_id, "ttl": 600},
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    await interaction.response.send_message(
                        "Failed to generate a secure link. Try again later.",
                        ephemeral=True,
                    )
                    return
        link_url = f"{WORKER_URL}/link/github?state={view.state}"
        button = discord.ui.Button(
            label="Link GitHub", url=link_url, style=discord.ButtonStyle.link
        )
        ephemeral_view = discord.ui.View()
        ephemeral_view.add_item(button)
        await interaction.response.send_message(
            "Click the button below to link your GitHub account. After linking, use `/role contributor` or `/role starred`.",
            view=ephemeral_view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(GithubRolesCog(bot))
