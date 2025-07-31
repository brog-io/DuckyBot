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
from datetime import datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

BLOG_FEED = {
    "url": "https://ente.io/rss.xml",
    "role_mention": "<@&1050340002028077106>",
    "forum_channel_id": 1121470028223623229,
    "button_text": "Link",
}

MASTODON_FEED = {
    "url": "https://fosstodon.org/@ente.rss",
    "button_text": "Link",
    "role_mention": "<@&1214608287597723739>",
    "text_channel_id": 973177352446173194,
}

ENTE_ICON_URL = "https://cdn.fosstodon.org/accounts/avatars/112/972/617/472/440/727/original/1bf22f4a9a82e4fc.png"
STATE_FILE = "ente_rss_state.json"

# SAFETY LIMITS
MAX_AGE_HOURS = 24  # Don't post items older than 24 hours
FEED_TIMEOUT = 60  # Timeout for feed parsing in seconds


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
            state = json.load(f)
            # Add timestamp tracking if not present
            if "last_check" not in state:
                state["last_check"] = datetime.now().isoformat()
            return state

    # Initialize state with current entries
    state = {"last_check": datetime.now().isoformat()}
    for feed_cfg in [BLOG_FEED, MASTODON_FEED]:
        try:
            d = feedparser.parse(feed_cfg["url"])
            if d.entries:
                entry = d.entries[0]
                entry_id = getattr(entry, "id", entry.link)
                state[feed_cfg["url"]] = entry_id
            else:
                state[feed_cfg["url"]] = None
        except Exception as e:
            logger.error(f"Error initializing state for {feed_cfg['url']}: {e}")
            state[feed_cfg["url"]] = None

    save_state(state)
    return state


def save_state(state):
    state["last_check"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_entry_too_old(entry, max_hours=MAX_AGE_HOURS):
    """Check if entry is older than max_hours"""
    published = entry.get("published")
    if not published:
        return False

    try:
        entry_time = dateparser.parse(published)
        if entry_time:
            cutoff = datetime.now(entry_time.tzinfo) - timedelta(hours=max_hours)
            return entry_time < cutoff
    except Exception:
        pass

    return False


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


def html_to_discord_md(html: str) -> str:
    if not html:
        return ""
    # Replace <br> and <br/> with single newline
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Replace block-ending tags with newline
    html = re.sub(r"</(p|div)>", "\n", html, flags=re.IGNORECASE)
    # Remove block-opening tags
    html = re.sub(r"<(p|div)[^>]*>", "", html, flags=re.IGNORECASE)
    # Lists: <li>...</li> becomes bullet points
    html = re.sub(r"<li>(.*?)</li>", r"• \1\n", html, flags=re.IGNORECASE)
    # Strip <a href="...">...</a>, keep only the visible text (removes links)
    html = re.sub(
        r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: strip_tags(m.group(2)).strip(),
        html,
        flags=re.IGNORECASE,
    )
    # Remove all other tags
    html = re.sub(r"<.*?>", "", html)
    # Decode HTML entities
    text = unescape(html)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace on each line
    text = "\n".join(line.rstrip() for line in text.splitlines())
    # Remove leading/trailing blank lines
    text = text.strip("\n")
    # Ensure paragraphs have double newlines
    text = re.sub(r"([^\n])\n([^\n])", r"\1\n\n\2", text)
    # Final collapse: more than 2 newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def strip_tags(html: str) -> str:
    return re.sub(r"<.*?>", "", html)


async def parse_feed_with_timeout(url: str, timeout: int = FEED_TIMEOUT):
    """Parse RSS feed with timeout to prevent blocking the event loop"""
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, feedparser.parse, url), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.error(f"Timeout ({timeout}s) parsing feed: {url}")
        return None
    except Exception as e:
        logger.error(f"Error parsing feed {url}: {e}")
        return None


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
            d = await parse_feed_with_timeout(BLOG_FEED["url"])
            if d and d.entries:
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                stored_id = self.state.get(BLOG_FEED["url"])

                if stored_id != entry_id:
                    new_entries = []
                    found_stored = False

                    # IMPROVED: Add safety checks
                    for i, entry in enumerate(d.entries):

                        eid = getattr(entry, "id", entry.link)

                        # If we find the stored entry, stop here
                        if eid == stored_id:
                            found_stored = True
                            break

                        # Safety: Don't post old entries
                        if is_entry_too_old(entry):
                            logger.info(
                                f"Skipping old blog entry: {entry.get('title', 'Unknown')}"
                            )
                            continue

                        new_entries.append(entry)

                        # Safety: If we've gone through too many entries without finding stored ID, stop
                        if i >= 20:  # Arbitrary limit
                            logger.warning(
                                f"Stopped searching after 20 entries, stored ID not found"
                            )
                            break

                    if new_entries:
                        forum_channel = self.bot.get_channel(
                            BLOG_FEED["forum_channel_id"]
                        )
                        if forum_channel:
                            logger.info(f"Posting {len(new_entries)} new blog entries")
                            for entry in reversed(new_entries):
                                await self.send_blog_post(
                                    forum_channel,
                                    entry,
                                    BLOG_FEED["role_mention"],
                                    BLOG_FEED["button_text"],
                                )
                        else:
                            logger.error(
                                f"Blog forum channel not found: {BLOG_FEED['forum_channel_id']}"
                            )

                        # Only update state if we found the stored ID or it's the first run
                        if found_stored or stored_id is None:
                            self.state[BLOG_FEED["url"]] = entry_id
                            changed = True
                        else:
                            logger.warning(
                                "Stored blog entry ID not found in feed, not updating state"
                            )
            elif d is None:
                logger.error(f"Failed to parse blog feed: {BLOG_FEED['url']}")

        except Exception as e:
            logger.error(f"RSS error for blog: {e}")

        # --- MASTODON FEED ---
        try:
            d = await parse_feed_with_timeout(MASTODON_FEED["url"])
            if d and d.entries:
                author_icon = self._get_feed_icon(d)
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                stored_id = self.state.get(MASTODON_FEED["url"])

                if stored_id != entry_id:
                    new_entries = []
                    found_stored = False

                    # IMPROVED: Same safety checks for Mastodon
                    for i, entry in enumerate(d.entries):

                        eid = getattr(entry, "id", entry.link)

                        if eid == stored_id:
                            found_stored = True
                            break

                        if is_entry_too_old(entry):
                            logger.info(
                                f"Skipping old Mastodon entry: {entry.get('title', 'Unknown')}"
                            )
                            continue

                        new_entries.append(entry)

                        if i >= 20:
                            logger.warning(
                                f"Stopped searching after 20 entries, stored ID not found"
                            )
                            break

                    if new_entries:
                        channel = self.bot.get_channel(MASTODON_FEED["text_channel_id"])
                        if channel:
                            logger.info(
                                f"Posting {len(new_entries)} new Mastodon entries"
                            )
                            for entry in reversed(new_entries):
                                await self.send_mastodon_embed(
                                    channel,
                                    entry,
                                    MASTODON_FEED["role_mention"],
                                    MASTODON_FEED["button_text"],
                                    author_icon,
                                )
                        else:
                            logger.error(
                                f"Mastodon channel not found: {MASTODON_FEED['text_channel_id']}"
                            )

                        if found_stored or stored_id is None:
                            self.state[MASTODON_FEED["url"]] = entry_id
                            changed = True
                        else:
                            logger.warning(
                                "Stored Mastodon entry ID not found in feed, not updating state"
                            )
            elif d is None:
                logger.error(f"Failed to parse Mastodon feed: {MASTODON_FEED['url']}")

        except Exception as e:
            logger.error(f"RSS error for Mastodon: {e}")

        if changed:
            save_state(self.state)

    @staticmethod
    def _get_feed_icon(d):
        if hasattr(d.feed, "image") and hasattr(d.feed.image, "href"):
            return d.feed.image.href
        return ENTE_ICON_URL

    async def send_blog_post(
        self, forum_channel, entry, role_mention: str, button_text: str
    ):
        title = get_first_str(entry.title)
        url = entry.link
        if forum_channel and isinstance(forum_channel, discord.ForumChannel):
            content = f"📰 [**{title}**]({url}) **|** {role_mention}"
            try:
                thread = await forum_channel.create_thread(
                    name=title[:95], content=content, view=LinkButton(url, button_text)
                )
                logger.info(f"Posted blog: {title}")
            except Exception as e:
                logger.error(f"Failed to post blog thread: {e}")

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

        try:
            await channel.send(
                content=role_mention, embed=embed, view=LinkButton(link, button_text)
            )
            logger.info(f"Posted Mastodon: {author}")
        except Exception as e:
            logger.error(f"Failed to post Mastodon embed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
