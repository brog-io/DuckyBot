import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
import re
from dateutil import parser as dateparser
import aiohttp
import logging
from html import unescape

logger = logging.getLogger(__name__)

BLOG_FEED = {
    "url": "https://ente.io/rss.xml",
    "role_mention": "<@&1050340002028077106>",
    "forum_channel_id": 1121470028223623229,
}

MASTODON_FEED = {
    "url": "https://fosstodon.org/@ente.rss",
    "button_text": "Link",
    "role_mention": "<@&1214608287597723739>",
    "text_channel_id": 973177352446173194,
}

ENTE_ICON_URL = "https://cdn.fosstodon.org/accounts/avatars/112/972/617/472/440/727/original/1bf22f4a9a82e4fc.png"
STATE_FILE = "ente_rss_state.json"


def get_first_str(val):
    if isinstance(val, list) and val:
        item = val[0]
        if isinstance(item, dict) and "value" in item:
            return item["value"]
        return str(item)
    return val


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    state = {}
    for feed_cfg in [BLOG_FEED, MASTODON_FEED]:
        d = feedparser.parse(feed_cfg["url"])
        if d.entries:
            entry = d.entries[0]
            entry_id = getattr(entry, "id", entry.link)
            state[feed_cfg["url"]] = entry_id
        else:
            state[feed_cfg["url"]] = None
    save_state(state)
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class LinkButton(discord.ui.View):
    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link)
        )


async def extract_image_url(entry, fallback_url=None):
    img_url = entry.get("media_content__@__url")
    img_type = entry.get("media_content__@__type", "")
    if img_url and img_type.startswith("image/"):
        return img_url

    if "media_content" in entry:
        media_list = entry["media_content"]
        if isinstance(media_list, list) and media_list:
            for media in media_list:
                url = media.get("url")
                typ = media.get("type", "")
                if url and typ.startswith("image/"):
                    return url

    enclosures = entry.get("enclosures")
    if enclosures and isinstance(enclosures, list):
        for enc in enclosures:
            if "image" in enc.get("type", "") and enc.get("url"):
                return enc["url"]

    if entry.get("image"):
        if isinstance(entry["image"], dict):
            image = entry["image"].get("href")
            return image
        if isinstance(entry["image"], str):
            return entry["image"]

    for field in ["summary", "description", "content"]:
        html = get_first_str(entry.get(field))
        if html and isinstance(html, str):
            m = re.search(r'<img[^>]+src="([^"]+)"', html)
            if m:
                return m.group(1)

    if fallback_url:
        og_image = await fetch_og_image(fallback_url)
        if og_image:
            return og_image

    return None


async def fetch_og_image(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                html = await resp.text()
                m = re.search(
                    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                    html,
                    re.IGNORECASE,
                )
                if m:
                    return m.group(1)
    except Exception as e:
        logger.warning(f"Error fetching og:image for {url}: {e}")
    return None


# HTML to Discord Markdown


def html_to_discord_md(html: str) -> str:
    if not html:
        return ""
    # Replace <br> and <br/> with newlines
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Replace </p> and </div> with double newlines
    html = re.sub(r"</?(p|div)>", "\n\n", html, flags=re.IGNORECASE)
    # Lists: turn <li>…</li> into • …
    html = re.sub(r"<li>(.*?)</li>", r"• \1\n", html, flags=re.IGNORECASE)
    # Links: <a href="url">text</a> to [text](url)
    html = re.sub(
        r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"[{strip_tags(m.group(2))}]({m.group(1)})",
        html,
        flags=re.IGNORECASE,
    )
    # Remove all remaining tags
    html = re.sub(r"<.*?>", "", html)
    # Unescape HTML entities
    return unescape(html).strip()


def strip_tags(html: str) -> str:
    return re.sub(r"<.*?>", "", html)


class RSSFeedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = load_state()
        self.check_feeds.start()

    def cog_unload(self):
        self.check_feeds.cancel()

    @tasks.loop(minutes=5)
    async def check_feeds(self):
        await self.bot.wait_until_ready()
        changed = False

        # --- BLOG FEED ---
        try:
            d = feedparser.parse(BLOG_FEED["url"])
            if d.entries:
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                if self.state.get(BLOG_FEED["url"]) != entry_id:
                    new_entries = []
                    for entry in d.entries:
                        eid = getattr(entry, "id", entry.link)
                        if eid == self.state.get(BLOG_FEED["url"]):
                            break
                        new_entries.append(entry)
                    forum_channel = self.bot.get_channel(BLOG_FEED["forum_channel_id"])
                    for entry in reversed(new_entries):
                        await self.send_blog_post(
                            forum_channel, entry, BLOG_FEED["role_mention"]
                        )
                    self.state[BLOG_FEED["url"]] = entry_id
                    changed = True
        except Exception as e:
            logger.error(f"RSS error for blog: {e}")

        # --- MASTODON FEED ---
        try:
            d = feedparser.parse(MASTODON_FEED["url"])
            author_icon = self._get_feed_icon(d)
            if d.entries:
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                if self.state.get(MASTODON_FEED["url"]) != entry_id:
                    new_entries = []
                    for entry in d.entries:
                        eid = getattr(entry, "id", entry.link)
                        if eid == self.state.get(MASTODON_FEED["url"]):
                            break
                        new_entries.append(entry)
                    channel = self.bot.get_channel(MASTODON_FEED["text_channel_id"])
                    for entry in reversed(new_entries):
                        await self.send_mastodon_embed(
                            channel,
                            entry,
                            MASTODON_FEED["role_mention"],
                            MASTODON_FEED["button_text"],
                            author_icon,
                        )
                    self.state[MASTODON_FEED["url"]] = entry_id
                    changed = True
        except Exception as e:
            logger.error(f"RSS error for Mastodon: {e}")

        if changed:
            save_state(self.state)

    @staticmethod
    def _get_feed_icon(d):
        if hasattr(d.feed, "image") and hasattr(d.feed.image, "href"):
            return d.feed.image.href
        return ENTE_ICON_URL

    async def send_blog_post(self, forum_channel, entry, role_mention: str):
        title = get_first_str(entry.title)
        url = entry.link
        if forum_channel and isinstance(forum_channel, discord.ForumChannel):
            await forum_channel.create_thread(
                name=title[:95], content=f"📰 [**{title}**]({url}) **|** {role_mention}"
            )

    async def send_mastodon_embed(
        self,
        channel: discord.TextChannel,
        entry,
        role_mention: str,
        button_text: str,
        author_icon: str,
    ):
        author = get_first_str(entry.get("author", "Ente(@fosstodon.org)"))
        summary = get_first_str(entry.get("summary", ""))
        link = entry.link
        published = entry.get("published", None)

        # format HTML to Discord markdown
        clean_text = html_to_discord_md(summary)

        image_url = await extract_image_url(entry)
        timestamp = None
        if published:
            try:
                timestamp = dateparser.parse(published)
            except Exception:
                timestamp = None
        embed = discord.Embed(description=clean_text, color=0x1DB954)
        embed.set_author(name=author, url=link, icon_url=author_icon)
        if image_url:
            embed.set_image(url=image_url)
        if timestamp:
            embed.timestamp = timestamp

        await channel.send(
            content=role_mention, embed=embed, view=LinkButton(link, button_text)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
