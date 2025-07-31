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
}

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
                entry_id = getattr(entry, "id", getattr(entry, "link", None))
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


def get_thread_title_and_content(entry, feed_cfg: dict):
    """Get thread title and content for the post"""
    url = entry.link

    if feed_cfg["type"] == "blog":
        # For blog posts, try to get the actual title
        title = get_first_str(getattr(entry, "title", None))
        if not title:
            title = "New Blog Post"

        thread_title = f"{feed_cfg['emoji']} {title}"
        thread_content = f"📰 [**{title}**]({url}) **|** {feed_cfg['role_mention']}"
    else:
        # For social posts, use a simple format
        thread_title = f"{feed_cfg['emoji']} New {feed_cfg['name']} Post"
        thread_content = f"{feed_cfg['emoji']} [**New {feed_cfg['name']} Post**]({url}) **|** {feed_cfg['role_mention']}"

    # Ensure thread title fits Discord's limits
    if len(thread_title) > 95:
        thread_title = thread_title[:92] + "..."

    return thread_title, thread_content


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
                    entry_id = getattr(latest, "id", getattr(latest, "link", None))
                    stored_id = self.state.get(feed_cfg["url"])

                    # Skip processing if we don't have a valid entry ID
                    if not entry_id:
                        logger.warning(
                            f"Skipping {feed_key} - latest entry has no valid ID or link"
                        )
                        continue

                    if stored_id != entry_id:
                        new_entries = []
                        found_stored = False

                        for i, entry in enumerate(d.entries):
                            try:
                                eid = getattr(entry, "id", getattr(entry, "link", None))

                                # Skip entries without valid IDs
                                if not eid:
                                    logger.warning(
                                        f"Skipping {feed_key} entry {i} - no valid ID or link"
                                    )
                                    continue

                                # If we find the stored entry, stop here
                                if eid == stored_id:
                                    found_stored = True
                                    break

                                # Safety: Don't post old entries
                                if is_entry_too_old(entry):
                                    logger.info(f"Skipping old {feed_key} entry")
                                    continue

                                new_entries.append(entry)

                                # Safety: If we've gone through too many entries without finding stored ID, stop
                                if i >= 20:  # Arbitrary limit
                                    logger.warning(
                                        f"Stopped searching after 20 entries for {feed_key}, stored ID not found"
                                    )
                                    break
                            except Exception as entry_error:
                                logger.warning(
                                    f"Error processing entry {i} for {feed_key}: {entry_error}"
                                )
                                continue

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
                                if entry_id:  # Only update if we have a valid entry_id
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
        """Send a post to a forum channel - simplified unified method"""
        if not forum_channel or not isinstance(forum_channel, discord.ForumChannel):
            return

        thread_title, thread_content = get_thread_title_and_content(entry, feed_cfg)
        url = entry.link

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
                    # Get the tag object from the forum
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

            # Create the thread
            thread = await forum_channel.create_thread(**thread_args)
            logger.info(f"Posted {feed_name}: {thread_title}")

        except Exception as e:
            logger.error(f"Failed to post {feed_name} thread: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
