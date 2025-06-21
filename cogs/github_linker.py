import discord
from discord import app_commands, Interaction
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import os

load_dotenv()

WORKER_URL = "https://brog.io"
ROLE_NAME = "Contributor"
STAR_ROLE_NAME = "Stargazer"
REPO_OWNER = "ente-io"
REPO_NAME = "ente"
API_KEY = os.getenv("LOOKUP_API_KEY")


class GithubRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    role = app_commands.Group(name="role", description="Get GitHub-related roles.")

    @role.command(name="contributor", description="Get the Contributor role.")
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
                        "Could not find a linked GitHub account. Use `/linkgithub` first.",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()
                github_username = data.get("github_username")

        if not github_username:
            await interaction.followup.send(
                "GitHub account not linked or visibility is private. Link it in Discord settings.",
                ephemeral=True,
            )
            return

        gh_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "discord-bot",
        }

        # Check commit contributions
        contributors = []
        page = 1
        while True:
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contributors?per_page=100&page={page}&anon=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=gh_headers) as resp:
                    if resp.status != 200:
                        break
                    new_data = await resp.json()
                    if not new_data:
                        break
                    contributors.extend(new_data)
                    if len(new_data) < 100:
                        break
                    page += 1

        is_commit_contributor = any(
            (c.get("login") or "").lower() == github_username.lower()
            for c in contributors
            if c.get("login")
        )

        # Check merged PRs
        is_pr_contributor = False
        page = 1
        while page <= 10:
            pr_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls?state=closed&per_page=100&page={page}"
            async with aiohttp.ClientSession() as session:
                async with session.get(pr_url, headers=gh_headers) as resp:
                    if resp.status != 200:
                        break
                    prs = await resp.json()
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
            role = discord.utils.get(interaction.guild.roles, name=ROLE_NAME)
            if not role:
                await interaction.followup.send(
                    f"Role `{ROLE_NAME}` not found. Ask an admin to create it.",
                    ephemeral=True,
                )
                return
            try:
                await interaction.user.add_roles(
                    role, reason="Verified GitHub contributor"
                )
                await interaction.followup.send(
                    f"✅ {interaction.user.mention}, you are a contributor and received the `{ROLE_NAME}` role.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I don't have permission to assign roles.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                f"❌ {interaction.user.mention}, you're not a contributor to `{REPO_OWNER}/{REPO_NAME}` ({github_username}).",
                ephemeral=True,
            )

    @role.command(name="stargazer", description="Get the Stargazer role.")
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
                        "Could not find your GitHub username. Use `/linkgithub` first.",
                        ephemeral=True,
                    )
                    return
                data = await resp.json()
                github_username = data.get("github_username")

        if not github_username:
            await interaction.followup.send(
                "GitHub account not linked or visibility is private. Link it in Discord settings.",
                ephemeral=True,
            )
            return

        # Check if the user has publicly starred the repo
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
                    f"Role `{STAR_ROLE_NAME}` not found. Ask an admin to create it.",
                    ephemeral=True,
                )
                return
            try:
                await interaction.user.add_roles(role, reason="Starred the repo")
                await interaction.followup.send(
                    f"🌟 {interaction.user.mention}, you starred `{REPO_OWNER}/{REPO_NAME}` and now have the `{STAR_ROLE_NAME}` role.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I don't have permission to assign roles.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                f"❌ {interaction.user.mention}, it seems you haven't publicly starred `{REPO_OWNER}/{REPO_NAME}`.",
                ephemeral=True,
            )

    @app_commands.command(description="Link your GitHub account through Discord.")
    async def linkgithub(self, interaction: Interaction):
        discord_id = str(interaction.user.id)
        headers = {"x-api-key": API_KEY}
        state = os.urandom(16).hex()

        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{WORKER_URL}/api/stateset",
                json={"state": state, "discord_id": discord_id, "ttl": 600},
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    await interaction.response.send_message(
                        "Failed to start link process. Try again later.",
                        ephemeral=True,
                    )
                    return

        link_url = f"{WORKER_URL}/link/discord?state={state}"
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Link GitHub via Discord",
                url=link_url,
                style=discord.ButtonStyle.link,
            )
        )
        await interaction.response.send_message(
            "Click the button to link your GitHub account through Discord. Your GitHub must be linked to your Discord **and public**.",
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(GithubRolesCog(bot))
