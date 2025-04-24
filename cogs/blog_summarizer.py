import discord
from discord.ext import commands
import re
import asyncio
from newspaper import Article
from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

TARGET_FORUM_CHANNEL_ID = 1121470028223623229

# Tag map with IDs and descriptions
TAG_MAP = {
    "Photos": {
        "id": 1241820570077757460,
        "description": "Content related to Ente Photos, including photography, editing, and sharing.",
    },
    "Auth": {
        "id": 1241820547613200578,
        "description": "Content related to Ente Auth.",
    },
    "Self Hosting": {
        "id": 1241820521071513642,
        "description": "Content about running services on personal servers, Raspberry Pi, or home infrastructure.",
    },
    "Other": {
        "id": 1364974316747231302,
        "description": "Anything that doesn't clearly fit into the above categories.",
    },
}


class Summarizer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def extract_url(self, text):
        match = re.search(r"https?://\S+", text)
        if match:
            return match.group(0).rstrip(").,!?")
        return None

    async def summarize_url(self, url):
        try:
            article = Article(url)
            article.download()
            article.parse()
            content = article.text[:4000]

            if not content.strip():
                return "Couldn't extract content from the link."

            prompt = (
                "Summarize the following blog article in a concise, clear paragraph. "
                "Focus on the key message and remove fluff or repetition. "
                "Use plain language and avoid technical jargon unless necessary.\n\n"
                f"{content}"
            )

            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You're a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=300,
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            return f"Failed to summarize: {str(e)}"

    async def suggest_tags(self, summary):
        try:
            tag_descriptions = "\n".join(
                f"- **{name}**: {data['description']}" for name, data in TAG_MAP.items()
            )

            prompt = (
                "You are an assistant that categorizes blog articles based on their summary. "
                "Your task is to choose the most relevant tags from the list below based on the provided summary. "
                "Each tag includes a description to help you decide.\n\n"
                "Only include tags that are clearly relevant. Choose **up to 3 tags**, but only if they strongly match the content. "
                "Return a plain comma-separated list of tag names — no extra text.\n\n"
                f"{tag_descriptions}\n\n"
                "If none of the tags clearly apply, return only: Other"
                "Here is the article summary:\n"
                f"{summary}\n\n"
                "Which tags best apply?"
            )

            response = client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {
                        "role": "system",
                        "content": "You're a helpful assistant that classifies blog posts.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=50,
            )

            raw_tags = response.choices[0].message.content.strip()
            return [
                tag.strip() for tag in raw_tags.split(",") if tag.strip() in TAG_MAP
            ]
        except Exception as e:
            print(f"Tag suggestion failed: {e}")
            return []

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        if thread.parent_id != TARGET_FORUM_CHANNEL_ID:
            return

        await asyncio.sleep(3)  # Let RSS bot post the message

        try:
            async for message in thread.history(limit=10):
                if message.author.bot and "http" in message.content:
                    url = self.extract_url(message.content)
                    if url:
                        status_msg = await thread.send("Summarizing article...")
                        summary = await self.summarize_url(url)
                        await status_msg.delete()
                        await thread.send(f"**Summary:**\n{summary}")

                        # Tagging
                        tag_names = await self.suggest_tags(summary)
                        tag_ids = [TAG_MAP[name]["id"] for name in tag_names]

                        available_tags = {
                            tag.id: tag for tag in thread.parent.available_tags
                        }
                        tags_to_apply = [
                            available_tags[tid]
                            for tid in tag_ids
                            if tid in available_tags
                        ]

                        if tags_to_apply:
                            await thread.edit(applied_tags=tags_to_apply)
                    break
        except Exception as e:
            print(f"Error in thread '{thread.name}': {e}")


async def setup(bot):
    await bot.add_cog(Summarizer(bot))
