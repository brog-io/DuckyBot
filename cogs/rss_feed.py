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

FEEDS = {
    "blog": {
        "url": "https://ente.io/rss.xml",
        "role_mention": "<@&1050340002028077106>",
        "forum_channel_id": 1121470028223623229,
        "button_text": "Read Blog",
        "emoji": "📰",
        "name": "Blog",
        "type": "blog",
    },
    "mastodon": {
        "url": "https://fosstodon.org/@ente.rss",
        "button_text": "View Post",
        "role_mention": "<@&1214608287597723739>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569634746269918,
        "emoji": "<:Mastodon_Logo:1312884790210461756>",
        "name": "Mastodon",
        "type": "social",
    },
    "bluesky": {
        "url": "https://bsky.app/profile/did:plc:uah5jix7ykdrae7a2ezp3rye/rss",
        "button_text": "View Post",
        "role_mention": "<@&1400571735904092230>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569656971886803,
        "emoji": "<:Bluesky_Logo:1400570292740296894>",
        "name": "Bluesky",
        "type": "social",
    },
    "twitter": {
        "url": "https://nitter.net/enteio/rss",
        "button_text": "View Tweet",
        "role_mention": "<@&1400571684867543233>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569666996535419,
        "emoji": "<:X_Logo:1400570906644058143>",
        "name": "Twitter",
        "type": "social",
    },
    "reddit": {
        "url": "https://www.reddit.com/r/enteio/.rss",
        "button_text": "View Post",
        "role_mention": "<@&1400571795848958052>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569681387061299,
        "emoji": "<:Reddit_Logo:1400570705073934397>",
        "name": "Reddit",
        "type": "social",
    },
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
    for feed_key, feed_cfg in FEEDS.items():
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


def get_entry_title(entry, feed_cfg: dict) -> str:
    """Extract and format entry title based on feed type"""
    title = get_first_str(entry.title) or "Untitled"

    # For social media feeds, truncate long titles and add context
    if feed_cfg["type"] == "social":
        # Remove HTML tags and clean up
        clean_title = strip_tags(title)
        # Truncate if too long
        if len(clean_title) > 100:
            clean_title = clean_title[:97] + "..."
        return clean_title

    return title


def get_entry_content(entry, feed_cfg: dict) -> str:
    """Extract and format entry content based on feed type"""
    if feed_cfg["type"] == "blog":
        # For blog posts, just return empty as we're creating a simple thread
        return ""

    # For social media, get the content/summary
    content = ""
    for field in ["summary", "description", "content"]:
        content = get_first_str(entry.get(field, ""))
        if content:
            break

    if content:
        clean_content = html_to_discord_md(content)
        # Truncate if too long for Discord
        if len(clean_content) > 1900:  # Leave room for other content
            clean_content = clean_content[:1897] + "..."
        return clean_content

    return ""


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

        for feed_key, feed_cfg in FEEDS.items():
            try:
                d = await parse_feed_with_timeout(feed_cfg["url"])
                if d and d.entries:
                    latest = d.entries[0]
                    entry_id = getattr(latest, "id", latest.link)
                    stored_id = self.state.get(feed_cfg["url"])

                    if stored_id != entry_id:
                        new_entries = []
                        found_stored = False

                        for i, entry in enumerate(d.entries):
                            eid = getattr(entry, "id", entry.link)

                            # If we find the stored entry, stop here
                            if eid == stored_id:
                                found_stored = True
                                break

                            # Safety: Don't post old entries
                            if is_entry_too_old(entry):
                                logger.info(
                                    f"Skipping old {feed_key} entry: {entry.get('title', 'Unknown')}"
                                )
                                continue

                            new_entries.append(entry)

                            # Safety: If we've gone through too many entries without finding stored ID, stop
                            if i >= 20:  # Arbitrary limit
                                logger.warning(
                                    f"Stopped searching after 20 entries for {feed_key}, stored ID not found"
                                )
                                break

                        if new_entries:
                            forum_channel = self.bot.get_channel(
                                feed_cfg["forum_channel_id"]
                            )
                            if forum_channel:
                                logger.info(
                                    f"Posting {len(new_entries)} new {feed_key} entries"
                                )
                                for entry in reversed(new_entries):
                                    await self.send_forum_post(
                                        forum_channel, entry, feed_cfg, feed_key
                                    )
                            else:
                                logger.error(
                                    f"{feed_key} forum channel not found: {feed_cfg['forum_channel_id']}"
                                )

                            # Only update state if we found the stored ID or it's the first run
                            if found_stored or stored_id is None:
                                self.state[feed_cfg["url"]] = entry_id
                                changed = True
                            else:
                                logger.warning(
                                    f"Stored {feed_key} entry ID not found in feed, not updating state"
                                )
                elif d is None:
                    logger.error(f"Failed to parse {feed_key} feed: {feed_cfg['url']}")

            except Exception as e:
                logger.error(f"RSS error for {feed_key}: {e}")

        if changed:
            save_state(self.state)

    async def send_forum_post(
        self, forum_channel, entry, feed_cfg: dict, feed_name: str
    ):
        """Send a post to a forum channel - unified method for all feeds"""
        title = get_entry_title(entry, feed_cfg)
        url = entry.link
        content = get_entry_content(entry, feed_cfg)

        if forum_channel and isinstance(forum_channel, discord.ForumChannel):
            # Create the thread title (max 100 characters for Discord)
            thread_title = f"{feed_cfg['emoji']} {title}"
            if len(thread_title) > 95:
                thread_title = thread_title[:92] + "..."

            # Create the initial post content
            if feed_cfg["type"] == "blog":
                # For blog posts, simple format
                thread_content = (
                    f"📰 [**{title}**]({url}) **|** {feed_cfg['role_mention']}"
                )
            else:
                # For social media posts, include content and formatting
                thread_content = f"{feed_cfg['emoji']} **{feed_cfg['name']} Post** **|** {feed_cfg['role_mention']}\n\n"
                if content:
                    thread_content += f"{content}\n\n"
                thread_content += f"[View Original Post]({url})"

            try:
                # Prepare thread creation arguments
                thread_args = {
                    "name": thread_title,
                    "content": thread_content,
                    "view": LinkButton(url, feed_cfg["button_text"]),
                }

                # Add tags for social feeds
                if feed_cfg["type"] == "social" and "tag_id" in feed_cfg:
                    try:
                        # Get the tag object from the forum - tags are stored as forum channel tags
                        available_tags = forum_channel.available_tags
                        tag = None
                        for available_tag in available_tags:
                            if available_tag.id == feed_cfg["tag_id"]:
                                tag = available_tag
                                break

                        if tag:
                            thread_args["applied_tags"] = [tag]
                        else:
                            logger.warning(
                                f"Tag ID {feed_cfg['tag_id']} not found for {feed_name}"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to apply tag for {feed_name}: {e}")

                # Try to get image for social media posts
                image_url = None
                if feed_cfg["type"] == "social":
                    image_url = await extract_image_url(entry, url)

                # Create the thread
                thread = await forum_channel.create_thread(**thread_args)

                # If there's an image and it's a social post, send it as a follow-up
                if image_url and feed_cfg["type"] == "social":
                    try:
                        embed = discord.Embed(color=0x1DB954)
                        embed.set_image(url=image_url)

                        # Add timestamp if available
                        published = entry.get("published")
                        if published:
                            try:
                                timestamp = dateparser.parse(published)
                                if timestamp:
                                    embed.timestamp = timestamp
                            except Exception:
                                pass

                        await thread.thread.send(embed=embed)
                    except Exception as e:
                        logger.warning(
                            f"Failed to send image for {feed_name} post: {e}"
                        )

                logger.info(f"Posted {feed_name}: {title}")
            except Exception as e:
                logger.error(f"Failed to post {feed_name} thread: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
