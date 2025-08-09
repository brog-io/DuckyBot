import re
import asyncio
import discord
from discord.ext import commands
from newspaper import Article
from openai import OpenAI
import logging
from dotenv import load_dotenv

load_dotenv()

# Initialize logger
logger = logging.getLogger(__name__)

# Ensure channel ID is an int
TARGET_FORUM_CHANNEL_ID = 1403678102340763699
client = os.getenv("OPENAI_API_KEY")

# Tag map with IDs and descriptions
TAG_MAP = {
    "Privacy": {
        "id": 1403678640029306900,
        "description": "Content related to privacy, security, and data protection. Be strict with this tag.",
    },
    "Software": {
        "id": 1403678703657156619,
        "description": "Content related to software development, programming, and coding.",
    },
    "Self Hosting": {
        "id": 1403678757503369279,
        "description": "Content about running services on personal servers, Raspberry Pi, or home infrastructure.",
    },
    "Hardware": {
        "id": 1403678786708308099,
        "description": "Content about hardware, including reviews, builds, and recommendations.",
    },
    "Other": {
        "id": 1403678813019308052,
        "description": "Anything that doesn't clearly fit into the above categories.",
    },
}


class Summarizer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_parent_id = TARGET_FORUM_CHANNEL_ID

    def extract_url(self, text: str) -> str | None:
        match = re.search(r"https?://\S+", text or "")
        return match.group(0).rstrip(").,!?") if match else None

    async def summarize_url(self, url: str) -> str:
        try:
            article = Article(url)
            article.download()
            article.parse()
            content = article.text[:4000]
            if not content.strip():
                return "Couldn't extract any text from that link."

            prompt = (
                "Summarize the following blog article in a concise paragraph, "
                "focusing on the key message and stripping out fluff or repetition:\n\n"
                f"{content}"
            )
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=300,
            )
            return resp.choices[0].message.content.strip()

        except Exception as e:
            logger.error("Summarize error for URL %s: %s", url, e)
            return f"Failed to summarize: {e}"

    async def suggest_tags(self, summary: str) -> list[str]:
        try:
            descs = "\n".join(
                f"- **{n}**: {d['description']}" for n, d in TAG_MAP.items()
            )
            prompt = (
                "Given this summary, pick up to 3 tags from the list below (or 'Other'), "
                "return a comma-separated list of tag names.\n\n"
                f"{descs}\n\nSummary:\n{summary}"
            )
            resp = client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": "You classify blog posts."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=50,
            )
            raw = resp.choices[0].message.content.strip()
            tags = [t.strip() for t in raw.split(",") if t.strip() in TAG_MAP]
            # drop "Other" if there are other valid tags
            if "Other" in tags and len(tags) > 1:
                tags = [t for t in tags if t != "Other"]
            return tags or ["Other"]

        except Exception as e:
            logger.error("Tag suggestion error: %s", e)
            return ["Other"]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # only care about threads under our target forum
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != self.target_parent_id:
            return

        # ignore bots
        if message.author.bot:
            return

        # extract URL from text or embed
        url = self.extract_url(message.content)
        if not url and message.embeds:
            url = message.embeds[0].url
        if not url:
            return

        logger.info(
            "User %s shared URL %s in thread %s",
            message.author,
            url,
            message.channel.name,
        )

        # summarize
        status = await message.channel.send("🔍 Summarizing your article…")
        summary = await self.summarize_url(url)
        await status.delete()

        await message.channel.send(f"# 📑 Summary:\n{summary}")

        # apply tags
        tag_names = await self.suggest_tags(summary)
        tag_ids = [TAG_MAP[name]["id"] for name in tag_names]
        available = {t.id: t for t in message.channel.parent.available_tags}
        to_apply = [available[i] for i in tag_ids if i in available]
        if to_apply:
            await message.channel.edit(applied_tags=to_apply)
            logger.info("Applied tags %s to thread %s", tag_names, message.channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Summarizer(bot))
