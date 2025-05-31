import discord
from discord import app_commands, Interaction
from discord.ext import commands
import aiohttp
import jwt
import time
import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class GitHubDiscussions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.forum_channel_id = 1121126215995113552
        self.github_repo = "ente-io/ente"
        self.github_discussion_category_id = None

        self.github_app_id = os.getenv("GITHUB_APP_ID")
        self.github_installation_id = os.getenv("GITHUB_INSTALLATION_ID")

        # Load GitHub private key from file path specified in env
        private_key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if private_key_path and os.path.exists(private_key_path):
            with open(private_key_path, "rb") as f:
                self.github_private_key = f.read()
        else:
            raise ValueError(
                "GitHub private key file not found or GITHUB_PRIVATE_KEY_PATH not set"
            )

        # Cache for discussion categories
        self._discussion_categories = None

    async def get_jwt(self):
        """Create a JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago to account for clock skew
            "exp": now
            + (9 * 60),  # Expires in 9 minutes (well under the 10 minute limit)
            "iss": self.github_app_id,
        }
        return jwt.encode(payload, self.github_private_key, algorithm="RS256")

    async def get_installation_token(self):
        """Get an installation token for the GitHub App."""
        jwt_token = await self.get_jwt()
        url = f"https://api.github.com/app/installations/{self.github_installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "discord-github-bot",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 201:
                    logger.error("Failed to get installation token:", data)
                    return None
                return data["token"]

    async def get_repository_id(self):
        """Get the repository ID needed for GraphQL."""
        token = await self.get_installation_token()
        if not token:
            return None

        query = """
        query($owner: String!, $name: String!) {
            repository(owner: $owner, name: $name) {
                id
                discussionCategories(first: 10) {
                    nodes {
                        id
                        name
                    }
                }
            }
        }
        """

        owner, name = self.github_repo.split("/")
        variables = {"owner": owner, "name": name}

        url = "https://api.github.com/graphql"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "discord-github-bot",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"query": query, "variables": variables}, headers=headers
            ) as resp:
                data = await resp.json()
                if resp.status != 200 or "errors" in data:
                    logger.error("Failed to get repository info:", data)
                    return None

                repo_data = data["data"]["repository"]
                logger.info("Available discussion categories:")
                for category in repo_data["discussionCategories"]["nodes"]:
                    logger.info(f"  {category['name']}: {category['id']}")

                return repo_data["id"]

    async def get_discussion_categories(self):
        """Get available discussion categories."""
        if self._discussion_categories is not None:
            return self._discussion_categories

        token = await self.get_installation_token()
        if not token:
            return []

        owner, name = self.github_repo.split("/")
        query = """
        query($owner: String!, $name: String!) {
            repository(owner: $owner, name: $name) {
                discussionCategories(first: 20) {
                    nodes {
                        id
                        name
                        description
                    }
                }
            }
        }
        """
        variables = {"owner": owner, "name": name}

        url = "https://api.github.com/graphql"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "discord-github-bot",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"query": query, "variables": variables}, headers=headers
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and "data" in data:
                    categories = data["data"]["repository"]["discussionCategories"][
                        "nodes"
                    ]
                    self._discussion_categories = categories
                    return categories
                else:
                    logger.error("Failed to get categories:", data)
                    return []

    async def create_github_discussion(self, title, body, category_id=None):
        """Create a new GitHub discussion using GraphQL."""
        token = await self.get_installation_token()
        if not token:
            return None

        repo_id = await self.get_repository_id()
        if not repo_id:
            return None

        # If no category specified, use the first available or the default
        if not category_id:
            category_id = self.github_discussion_category_id
            if not category_id:
                categories = await self.get_discussion_categories()
                if categories:
                    category_id = categories[0]["id"]
                else:
                    logger.warning("No discussion categories found")
                    return None

        # GraphQL mutation to create a discussion
        mutation = """
        mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
            createDiscussion(input: {
                repositoryId: $repositoryId,
                categoryId: $categoryId,
                title: $title,
                body: $body
            }) {
                discussion {
                    url
                    id
                    title
                }
            }
        }
        """

        variables = {
            "repositoryId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        }

        url = "https://api.github.com/graphql"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "discord-github-bot",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"query": mutation, "variables": variables}, headers=headers
            ) as resp:
                data = await resp.json()
                if (
                    resp.status == 200
                    and "data" in data
                    and data["data"]["createDiscussion"]
                ):
                    return data["data"]["createDiscussion"]["discussion"]["url"]
                else:
                    logger.error("GitHub GraphQL error:", data)
                    return None

    @app_commands.command(
        name="discussion", description="Post this thread to GitHub Discussions"
    )
    @app_commands.describe(category="Choose a discussion category (optional)")
    @app_commands.default_permissions(administrator=True)
    async def discussion(self, interaction: Interaction, category: str = None):
        thread = interaction.channel
        # Only allow in a forum thread
        if (
            not isinstance(thread, discord.Thread)
            or thread.parent_id != self.forum_channel_id
        ):
            await interaction.response.send_message(
                "This command can only be used in a forum thread.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # Find the category ID if a category name was provided
        category_id = None
        if category:
            categories = await self.get_discussion_categories()
            for cat in categories:
                if cat["name"].lower() == category.lower():
                    category_id = cat["id"]
                    break
            if not category_id:
                available_categories = ", ".join([cat["name"] for cat in categories])
                await interaction.followup.send(
                    f"Category '{category}' not found. Available categories: {available_categories}",
                    ephemeral=True,
                )
                return

        thread_title = thread.name
        async for msg in thread.history(oldest_first=True, limit=1):
            starter_message = msg.content
            break
        else:
            starter_message = "*No message found.*"

        discussion_url = await self.create_github_discussion(
            title=thread_title,
            body=f"Imported from Discord forum thread: [{thread.jump_url}]({thread.jump_url})\n\n{starter_message}",
            category_id=category_id,
        )
        if not discussion_url:
            await interaction.followup.send(
                "Failed to create GitHub Discussion.", ephemeral=True
            )
            return

        category_text = f" in category '{category}'" if category else ""
        await interaction.followup.send(
            f"Discussion created{category_text}: {discussion_url}"
        )

    @discussion.autocomplete("category")
    async def category_autocomplete(self, interaction: Interaction, current: str):
        """Provide autocomplete suggestions for discussion categories."""
        categories = await self.get_discussion_categories()
        choices = []
        for cat in categories:
            if current.lower() in cat["name"].lower():
                # Limit to 25 choices (Discord's limit)
                if len(choices) < 25:
                    choices.append(
                        app_commands.Choice(name=cat["name"], value=cat["name"])
                    )
        return choices


async def setup(bot):
    await bot.add_cog(GitHubDiscussions(bot))
