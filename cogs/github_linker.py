import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import os
import logging
from urllib.parse import urlencode

# -------------------- Setup --------------------

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("linkgithub-bot")

WORKER_URL = "https://brog.io"
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
LOOKUP_API_KEY = os.getenv("LOOKUP_API_KEY")

if not DISCORD_CLIENT_ID:
    raise RuntimeError("DISCORD_CLIENT_ID is not set")

if not LOOKUP_API_KEY:
    raise RuntimeError("LOOKUP_API_KEY is not set")

# -------------------- UI --------------------


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


# -------------------- Bot --------------------


class LinkGithubCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self) -> None:
        if not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def _store_state(self, state: str, discord_id: str, ttl: int = 600) -> bool:
        """
        Store temporary OAuth state in the Worker.
        """
        try:
            async with self.session.put(
                f"{WORKER_URL}/api/stateset",
                headers={
                    "x-api-key": LOOKUP_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "state": state,
                    "discord_id": discord_id,
                    "ttl": ttl,
                },
            ) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.error("Failed to store state: %s", exc)
            return False

    @app_commands.command(
        name="linkgithub",
        description="Link your GitHub account for Discord Linked Roles.",
    )
    async def linkgithub(self, interaction: Interaction) -> None:
        """
        Convenience command that starts the Linked Roles OAuth flow.
        """
        await interaction.response.defer(ephemeral=True)

        state = os.urandom(16).hex()
        discord_id = str(interaction.user.id)

        ok = await self._store_state(state, discord_id)
        if not ok:
            await interaction.followup.send(
                "Failed to start linking flow. Please try again later.",
                ephemeral=True,
            )
            return

        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": f"{WORKER_URL}/auth/discord/callback",
            "response_type": "code",
            "scope": "identify connections role_connections.write",
            "state": state,
        }

        oauth_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
        view = LinkGithubButton(oauth_url)

        await interaction.followup.send(
            "Click below to link your GitHub account.\n\n"
            "After linking, Discord will automatically assign Linked Roles "
            "based on your GitHub activity.",
            view=view,
            ephemeral=True,
        )


# -------------------- Bot Entrypoint --------------------


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkGithubCog(bot))
