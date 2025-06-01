import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
import re
from dateutil import parser as dateparser
import aiohttp
import logging

logger = logging.getLogger(__name__)


BLOG_FEED = {
    "url": "https://ente.io/rss.xml",
    "button_text": "Read Blog",
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
    """
    Return the first string value from a field that may be a string, list, or dict.
    Used for RSS fields like summary, description, or content.
    """
    if isinstance(val, list) and val:
        item = val[0]
        if isinstance(item, dict) and "value" in item:
            return item["value"]
        return str(item)
    return val


def load_state():
    """
    Load the state file tracking the last processed entry for each feed.
    On first run, initializes to the latest entry in each feed.
    """
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
    """Save the current state of the feeds to a JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class LinkButton(discord.ui.View):
    """
    Discord UI view containing a single link button.
    """

    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link)
        )


async def extract_image_url(entry, fallback_url=None):
    """
    Extract an image URL from an RSS feed entry.
    Tries all common RSS fields and as fallback fetches og:image from the entry's web page.
    """
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
    """
    Fetch the first <meta property="og:image"> from the HTML of a URL.
    Used as a fallback if no image is found in RSS.
    """
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


class RSSFeedCog(commands.Cog):
    """
    A cog that monitors multiple RSS feeds and posts updates to different Discord channels:
    - Blog posts are posted as new threads in a forum channel with rich embeds.
    - Mastodon posts are posted as rich embeds in a text channel.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = load_state()
        self.check_feeds.start()

    def cog_unload(self):
        """Cancel the background feed checking task when the cog is unloaded."""
        self.check_feeds.cancel()

    @tasks.loop(minutes=5)
    async def check_feeds(self):
        """
        Periodically check each configured RSS feed for new entries.
        For each new entry, post to the configured channel (forum for blog, text for Mastodon).
        """
        await self.bot.wait_until_ready()
        changed = False

        # --- BLOG FEED: post to forum channel as new thread ---
        try:
            d = feedparser.parse(BLOG_FEED["url"])
            author_icon = self._get_feed_icon(d)
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
                        await self.send_blog_embed(
                            forum_channel,
                            entry,
                            BLOG_FEED["role_mention"],
                            BLOG_FEED["button_text"],
                            author_icon,
                        )
                    self.state[BLOG_FEED["url"]] = entry_id
                    changed = True
        except Exception as e:
            logger.error(f"RSS error for blog: {e}")

        # --- MASTODON FEED: post as embed to text channel ---
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
        """
        Extract the <image><url> from the feed, or return the fallback.
        """
        if hasattr(d.feed, "image") and hasattr(d.feed.image, "href"):
            return d.feed.image.href
        return ENTE_ICON_URL

    async def send_blog_embed(
        self,
        forum_channel,
        entry,
        role_mention: str,
        button_text: str,
        author_icon: str,
    ):
        """
        Post a blog entry as a rich embed in a new forum thread.
        """
        title = get_first_str(entry.title)
        url = entry.link
        summary = get_first_str(entry.get("summary") or entry.get("description", ""))
        clean_summary = (
            re.sub(r"<.*?>", "", summary).strip() if isinstance(summary, str) else ""
        )
        image_url = await extract_image_url(entry, fallback_url=url)

        embed = discord.Embed(
            title=title, url=url, description=clean_summary, color=0x1DB954
        )
        embed.set_author(name="Ente", icon_url=author_icon)
        if image_url:
            embed.set_image(url=image_url)

        if forum_channel and isinstance(forum_channel, discord.ForumChannel):
            await forum_channel.create_thread(
                name=title[:95],
                content=role_mention,
                embed=embed,
                view=LinkButton(url, button_text),
            )

    async def send_mastodon_embed(
        self,
        channel: discord.TextChannel,
        entry,
        role_mention: str,
        button_text: str,
        author_icon: str,
    ):
        """
        Send a Mastodon post as a rich embed to the specified text channel.
        """
        author = get_first_str(entry.get("author", "Ente(@fosstodon.org)"))
        summary = get_first_str(entry.get("summary", ""))
        link = entry.link
        published = entry.get("published", None)
        clean_text = (
            re.sub(r"<.*?>", "", summary).strip() if isinstance(summary, str) else ""
        )
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
    """
    Setup function for adding the RSSFeedCog to the bot.
    """
    await bot.add_cog(RSSFeedCog(bot))
