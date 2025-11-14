import os
import io
import json
import asyncio
import aiohttp
import discord
from discord.ext import commands, tasks

# ------------------------------
# Configuration (hard-coded)
# ------------------------------
OWNER = "ente-io"
REPO = "ente"
CHANNEL_ID = 953689741432340540  # set your channel ID here
POLL_INTERVAL_SECONDS = 600  # check every 600 seconds (10 minutes)
STATE_FILE = "photos_release_state.json"

GITHUB_TOKEN = os.environ.get(
    "GITHUB_TOKEN"
)  # optional token for GitHub API rate limits


# ------------------------------
# GitHub API helper functions
# ------------------------------
async def fetch_releases(session: aiohttp.ClientSession, owner: str, repo: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"GitHub API error {resp.status}: {text}")
        return await resp.json()


def is_photos_release(release: dict) -> bool:
    tag = release.get("tag_name", "")
    return tag.startswith("photos-")


def choose_apk_asset(release: dict) -> dict | None:
    for asset in release.get("assets") or []:
        name = asset.get("name", "").lower()
        if name.endswith(".apk"):
            return asset
    return None


# ------------------------------
# Components v2 layout view
# ------------------------------
class PhotosReleaseLayout(discord.ui.LayoutView):
    def __init__(self, owner: str, repo: str, release: dict):
        super().__init__(timeout=None)
        self.owner = owner
        self.repo = repo
        self.release = release

        html_url = release.get("html_url", "")
        if html_url:
            self.add_item(
                discord.ui.Button(
                    label="View on GitHub", style=discord.ButtonStyle.link, url=html_url
                )
            )

        asset = choose_apk_asset(release)
        if asset:
            self.asset_url = asset.get("browser_download_url")
            asset_name = asset.get("name", "download.apk")
            self.add_item(
                discord.ui.Button(
                    label=f"Download {asset_name}",
                    style=discord.ButtonStyle.primary,
                    custom_id="download_photos_apk",
                )
            )
        else:
            self.asset_url = None

    @discord.ui.button(
        label="Download APK",
        style=discord.ButtonStyle.primary,
        custom_id="download_photos_apk",
    )
    async def download_apk(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self.asset_url:
            await interaction.response.send_message(
                "No APK asset available for this release.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with aiohttp.ClientSession() as session:
                headers = {}
                if GITHUB_TOKEN:
                    headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
                async with session.get(self.asset_url, headers=headers) as resp:
                    if resp.status != 200:
                        txt = await resp.text()
                        await interaction.followup.send(
                            f"Download failed (status {resp.status}): {txt}",
                            ephemeral=True,
                        )
                        return
                    data = await resp.read()

            filename = os.path.basename(self.asset_url)
            file_obj = discord.File(io.BytesIO(data), filename=filename)
            await interaction.followup.send(
                content=f"Here is the APK for `{self.owner}/{self.repo}` release `{self.release.get('tag_name')}`",
                file=file_obj,
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"Error downloading APK: {exc}", ephemeral=True
            )


# ------------------------------
# Cog definition
# ------------------------------
class EntePhotosReleaseCog(commands.Cog):
    """
    Cog that automatically posts new 'photos-' releases from ente-io/ente.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = {}
        self.load_state()
        self.check_task.start()

    def cog_unload(self):
        self.check_task.cancel()

    def load_state(self):
        """Load state from JSON file (create if not exists)."""
        if not os.path.exists(STATE_FILE):
            # create default state
            self.state = {"last_tag": ""}
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        else:
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
                # ensure last_tag present
                if "last_tag" not in self.state:
                    self.state["last_tag"] = ""
            except Exception as e:
                print(f"[EntePhotosReleaseCog] Failed to load state file: {e}")
                self.state = {"last_tag": ""}

    def save_state(self):
        """Persist state back to JSON file."""
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"[EntePhotosReleaseCog] Failed to save state file: {e}")

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def check_task(self):
        await self.bot.wait_until_ready()
        try:
            async with aiohttp.ClientSession() as session:
                releases = await fetch_releases(session, OWNER, REPO)
        except Exception as e:
            print(f"[EntePhotosReleaseCog] Error fetching releases: {e}")
            return

        photos = [r for r in releases if is_photos_release(r)]
        if not photos:
            return

        photos.sort(key=lambda r: r.get("published_at", ""), reverse=True)
        latest = photos[0]
        latest_tag = latest.get("tag_name")

        if latest_tag and latest_tag != self.state.get("last_tag"):
            channel = self.bot.get_channel(CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title=f"{latest.get('name') or latest_tag} ({latest_tag})",
                    description=latest.get("body") or "No release notes provided.",
                    url=latest.get("html_url"),
                    color=discord.Color.blurple(),
                )
                embed.set_author(
                    name=f"{OWNER}/{REPO} • New Photos Release",
                    url=f"https://github.com/{OWNER}/{REPO}",
                )
                embed.add_field(name="Tag", value=latest_tag, inline=True)
                author_login = (latest.get("author") or {}).get("login", "unknown")
                embed.add_field(name="Author", value=author_login, inline=True)
                published_at = latest.get("published_at")
                if published_at:
                    embed.add_field(
                        name="Published at", value=published_at, inline=False
                    )
                embed.set_footer(text="Source: GitHub Releases")

                view = PhotosReleaseLayout(OWNER, REPO, latest)

                body_text = latest.get("body") or ""
                filename = f"{OWNER}-{REPO}-{latest_tag}-notes.txt"
                file_buf = io.StringIO(body_text)
                notes_file = discord.File(file_buf, filename=filename)

                await channel.send(embed=embed, view=view, file=notes_file)

            # update state and persist
            self.state["last_tag"] = latest_tag
            self.save_state()

    async def cog_load(self):
        # On cog load you might want to trigger an immediate check (optional)
        # await self.check_task.invoke()  # if you want to run immediately
        pass


async def setup(bot: commands.Bot):
    await bot.add_cog(EntePhotosReleaseCog(bot))
