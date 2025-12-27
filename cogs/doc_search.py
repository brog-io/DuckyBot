from __future__ import annotations
import os
import re
import time
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("POGGERS_API_KEY")

EXAMPLE_QUERIES = [
    "Why are my authenticator codes different?",
    "Does Ente have a family plan?",
    "How to share an album?",
    "How do I pronounce Ente?",
    "Is there a student discount?",
    "How to reset my password if I lost it?",
    "Can I search for photos using the descriptions I've added?",
    "Does Ente Auth require an account?",
]


async def autocomplete_doc_query(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Provide simple substring based autocomplete for the docsearch command."""
    lowered = current.lower()
    return [
        app_commands.Choice(name=q, value=q)
        for q in EXAMPLE_QUERIES
        if lowered in q.lower()
    ]


class DocSearch(commands.Cog):
    """
    Cog that provides:
      1) Passive channel auto replies (auth tip, selfhosting redirect).
      2) A /docsearch command that queries an external API.

    Adds:
      - ROLE_BLACKLIST: members with these roles are ignored.
      - COOLDOWN: each user can only trigger an auto reply once every 15 minutes.
    """

    def __init__(self, bot: commands.Bot | commands.AutoShardedBot):
        self.bot = bot

        self.COMMUNITY_GUILD_ID = 948937918347608085

        # Channels
        self.SELFHOSTING_CHANNEL_ID = 1383504546361380995
        self.INTROS_CHANNEL_ID = 1380262760994177135

        # Exempt channels
        self.SELFHOSTING_EXEMPT_CHANNELS = [
            self.SELFHOSTING_CHANNEL_ID,
            self.INTROS_CHANNEL_ID,
        ]

        # Role blacklist
        self.ROLE_BLACKLIST = [
            950276268593659925,
            950275266045960254,
        ]

        # Cooldown: user_id -> timestamp of last trigger
        self.user_cooldowns: dict[int, float] = {}
        self.COOLDOWN_SECONDS = 30 * 60  # 15 minutes

        # Channel specific auto replies
        self.CHANNEL_AUTO_REPLIES: dict[int, dict[str, object]] = {
            1051153671985045514: {
                "trigger_keywords": [
                    "2fa",
                    "authenticator",
                    "code",
                    "codes",
                    "auth",
                    "otp",
                ],
                "problem_keywords": [
                    "wrong",
                    "different",
                    "not working",
                    "don't work",
                    "dont work",
                    "doesnt work",
                    "doesn't work",
                    "invalid",
                    "issue",
                    "problem",
                ],
                "response": (
                    "If the authenticator codes on your PC and phone are different, "
                    "make sure the time is correct on both devices. The codes are time based, "
                    "so even a small time drift can cause invalid codes."
                    "You can use <https://time.is/> to see if your device's time is accurate."
                ),
            }
        }

        # Selfhosting
        self.SELFHOSTING_KEYWORDS = [
            "selfhost",
            "self-host",
            "self hosting",
            "self-hosting",
            "host myself",
            "docker",
        ]
        self.SELFHOSTING_MESSAGE = (
            "If you have a question about selfhosting Ente, please use <#{}>"
        ).format(self.SELFHOSTING_CHANNEL_ID)

    def is_community_server(self, guild_id: int) -> bool:
        return guild_id == self.COMMUNITY_GUILD_ID

    def is_in_exempt_channel(self, message: discord.Message) -> bool:
        channel = message.channel
        if channel.id in self.SELFHOSTING_EXEMPT_CHANNELS:
            return True
        if isinstance(channel, discord.Thread):
            if channel.parent_id in self.SELFHOSTING_EXEMPT_CHANNELS:
                return True
        return False

    def has_blacklisted_role(self, member: discord.Member) -> bool:
        if not self.ROLE_BLACKLIST:
            return False
        try:
            member_role_ids = {role.id for role in member.roles}
        except Exception:
            return False
        return any(role_id in member_role_ids for role_id in self.ROLE_BLACKLIST)

    def is_on_cooldown(self, user_id: int) -> bool:
        """Check if a user is still within their cooldown window."""
        last_time = self.user_cooldowns.get(user_id, 0)
        return (time.time() - last_time) < self.COOLDOWN_SECONDS

    def update_cooldown(self, user_id: int) -> None:
        """Update the cooldown timestamp for a user."""
        self.user_cooldowns[user_id] = time.time()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if not self.is_community_server(message.guild.id):
            return
        if isinstance(message.author, discord.Member) and self.has_blacklisted_role(
            message.author
        ):
            return
        if self.is_on_cooldown(message.author.id):
            return

        content = message.content.lower()
        channel_id = message.channel.id

        # Channel specific auto replies
        if channel_id in self.CHANNEL_AUTO_REPLIES:
            cfg = self.CHANNEL_AUTO_REPLIES[channel_id]
            if any(k in content for k in cfg["trigger_keywords"]) and any(
                p in content for p in cfg["problem_keywords"]
            ):
                await message.reply(cfg["response"], mention_author=False)
                self.update_cooldown(message.author.id)
                return

        # Selfhosting redirect
        if any(
            word in content for word in self.SELFHOSTING_KEYWORDS
        ) and not self.is_in_exempt_channel(message):
            await message.reply(self.SELFHOSTING_MESSAGE, mention_author=False)
            self.update_cooldown(message.author.id)
            return

    @app_commands.command(
        name="docsearch",
        description="Search Ente documentation for a query and get an answer with a link if available.",
    )
    @app_commands.describe(query="Enter your documentation question.")
    @app_commands.autocomplete(query=autocomplete_doc_query)
    async def docsearch(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None or not self.is_community_server(
            interaction.guild.id
        ):
            await interaction.response.send_message(
                "This command is only available in the Ente community server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        if not API_KEY:
            await interaction.followup.send(
                "Docs search is not configured. Missing API key.",
                ephemeral=True,
            )
            return

        payload = {"query": query, "key": API_KEY}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    "https://api.poggers.win/api/ente/docs-search",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            f"API error: {resp.status}",
                            ephemeral=True,
                        )
                        return
                    data = await resp.json()
            except aiohttp.ClientError as e:
                await interaction.followup.send(f"Network error: {e}", ephemeral=True)
                return
            except Exception as e:
                await interaction.followup.send(
                    f"Unexpected error: {e}", ephemeral=True
                )
                return

        if data.get("success"):
            answer = data.get("answer", "No answer returned.")
            urls = re.findall(r"https?://\S+", answer)
            if urls:
                button = discord.ui.Button(label="Open Link", url=urls[0])
                view = discord.ui.View()
                view.add_item(button)
                await interaction.followup.send(answer, ephemeral=True, view=view)
            else:
                await interaction.followup.send(answer, ephemeral=True)
        else:
            await interaction.followup.send(
                "API returned success: false or an unknown format.", ephemeral=True
            )


async def setup(bot: commands.Bot | commands.AutoShardedBot) -> None:
    await bot.add_cog(DocSearch(bot))
