import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import os
import re
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
    "Can I search for photos using the descriptions I've added? ",
    "Does Ente Auth require an account? ",
]


async def autocomplete_doc_query(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=q, value=q)
        for q in EXAMPLE_QUERIES
        if current.lower() in q.lower()
    ]


class DocSearch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.SELFHOSTING_CHANNEL_ID = 1383504546361380995
        self.INTROS_CHANNEL_ID = 1380262760994177135

        # Channels where selfhosting redirect should NOT appear
        self.SELFHOSTING_EXEMPT_CHANNELS = [
            self.SELFHOSTING_CHANNEL_ID,
            self.INTROS_CHANNEL_ID,
        ]

        self.CHANNEL_AUTO_REPLIES = {
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
                    "make sure the time is correct on both devices. The codes are time-based, "
                    "so even a small time drift can cause invalid codes."
                ),
            }
        }

        self.SELFHOSTING_KEYWORDS = [
            "selfhost",
            "self-host",
            "self hosting",
            "self-hosting",
            "host myself",
            "docker",
        ]
        self.SELFHOSTING_MESSAGE = (
            "If you have a question about selfhosting Ente, please check out <#{}>"
        ).format(self.SELFHOSTING_CHANNEL_ID)

    def is_in_exempt_channel(self, message: discord.Message) -> bool:
        """Check if message is in a channel where selfhosting redirects should not appear."""
        channel_id = message.channel.id

        # Direct message in any exempt channel
        if channel_id in self.SELFHOSTING_EXEMPT_CHANNELS:
            return True

        # Message in a thread within an exempt forum channel
        if isinstance(message.channel, discord.Thread):
            if message.channel.parent_id in self.SELFHOSTING_EXEMPT_CHANNELS:
                return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content.lower()
        channel_id = message.channel.id

        # Channel-specific auto replies
        if channel_id in self.CHANNEL_AUTO_REPLIES:
            config = self.CHANNEL_AUTO_REPLIES[channel_id]
            if any(k in content for k in config["trigger_keywords"]) and any(
                p in content for p in config["problem_keywords"]
            ):
                await message.reply(config["response"], mention_author=False)
                return

        # Selfhosting: redirect if NOT in exempt channels (selfhosting, intros, etc.)
        if any(
            word in content for word in self.SELFHOSTING_KEYWORDS
        ) and not self.is_in_exempt_channel(message):
            await message.reply(self.SELFHOSTING_MESSAGE, mention_author=False)
            return

    @app_commands.command(
        name="docsearch", description="Search Ente's documentation for a query."
    )
    @app_commands.describe(query="Enter your documentation question.")
    @app_commands.autocomplete(query=autocomplete_doc_query)
    async def docsearch(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True, ephemeral=True)

        payload = {"query": query, "key": API_KEY}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.poggers.win/api/ente/docs-search", json=payload
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"API error: {resp.status}", ephemeral=True
                    )
                    return

                data = await resp.json()

                if data.get("success"):
                    answer = data.get("answer", "No answer returned.")
                    urls = re.findall(r"https?://\S+", answer)

                    if urls:
                        button = discord.ui.Button(label="Open Link", url=urls[0])
                        view = discord.ui.View()
                        view.add_item(button)
                        await interaction.followup.send(
                            f"{answer}", ephemeral=True, view=view
                        )
                    else:
                        await interaction.followup.send(f"{answer}", ephemeral=True)
                else:
                    await interaction.followup.send(
                        "API returned success: false or unknown format.", ephemeral=True
                    )


async def setup(bot):
    await bot.add_cog(DocSearch(bot))
