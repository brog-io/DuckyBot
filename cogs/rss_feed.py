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
from datetime import datetime, timedelta, timezone
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
        "url": "https://rss.app/feeds/6KKkSyJY69IyUDD8.xml",
        "button_text": "View Tweet",
        "role_mention": "<@&1400571684867543233>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569666996535419,
        "emoji": "<:X_Logo:1400570906644058143>",
        "name": "Twitter",
        "type": "social",
    },
    "reddit": {
        "url": "https://www.reddit.com/r/enteio/new/.rss",
        "button_text": "View Post",
        "role_mention": "<@&1400571795848958052>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400569681387061299,
        "emoji": "<:Reddit_Logo:1400570705073934397>",
        "name": "Reddit",
        "type": "social",
        "headers": {"User-Agent": "Ducky/1.0 (https://ente.io; brogio@ente.io)"},
    },
    "instagram": {
        "url": "https://rss.app/feeds/kSh7fh1j85tCyFEx.xml",
        "button_text": "View Post",
        "role_mention": "<@&1400779976222965962>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400780883698651136,
        "emoji": "<:Instagram_Logo:1400780663614869504>",
        "name": "Instagram",
        "type": "social",
    },
    "theads": {
        "url": "https://rss.app/feeds/KLQWdv7w7ukehTax.xml",
        "button_text": "View Post",
        "role_mention": "<@&1400779976222965962>",
        "forum_channel_id": 1400567228314943529,
        "tag_id": 1400780883698651136,
        "emoji": "<:Instagram_Logo:1400780663614869504>",
        "name": "Threads",
        "type": "social",
    },
}

STATE_FILE = "ente_rss_state.json"
RECENT_POSTS_LIMIT = 5  # Number of recent posts to track per feed

# SAFETY LIMITS
MAX_AGE_HOURS = 3  # Don't post items older than 3 hours
FEED_TIMEOUT = 60  # Timeout for feed parsing in seconds


def get_entry_date(entry):
    """Extract publication date from entry, trying multiple fields"""
    for date_field in ["published", "updated", "created"]:
        date_str = getattr(entry, date_field, None)
        if date_str:
            try:
                parsed_date = dateparser.parse(date_str)
                # Ensure the datetime is timezone-aware
                if parsed_date.tzinfo is None:
                    # If naive, assume UTC
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                return parsed_date
            except Exception:
                continue
    return None


def get_post_identifier(entry):
    """Get a unique identifier for a post (using URL)"""
    return getattr(entry, "link", None) or getattr(entry, "id", None)


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
            # Ensure we have a last_check timestamp
            if "last_check" not in state:
                state["last_check"] = datetime.now(timezone.utc).isoformat()

            # Ensure we have a recent_posts structure
            if "recent_posts" not in state:
                state["recent_posts"] = {}

            # Migrate from old ID-based system to date-based system
            for feed_key, feed_cfg in FEEDS.items():
                feed_url = feed_cfg["url"]
                if (
                    feed_url not in state
                    or not isinstance(state[feed_url], str)
                    or not state[feed_url].endswith(("Z", "+00:00"))
                ):
                    # Initialize with current time to avoid re-posting old content
                    state[feed_url] = datetime.now(timezone.utc).isoformat()
                    logger.info(f"Initialized {feed_key} last check time")

                # Initialize recent_posts for this feed if not exists
                if feed_url not in state["recent_posts"]:
                    state["recent_posts"][feed_url] = []
                    logger.info(f"Initialized {feed_key} recent posts tracking")

            return state

    # Initialize state with current time for all feeds
    state = {"last_check": datetime.now(timezone.utc).isoformat(), "recent_posts": {}}
    current_time = datetime.now(timezone.utc).isoformat()

    for feed_key, feed_cfg in FEEDS.items():
        state[feed_cfg["url"]] = current_time
        state["recent_posts"][feed_cfg["url"]] = []
        logger.info(f"Initialized {feed_key} with current time")

    save_state(state)
    return state


def save_state(state):
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    try:
        # Create backup of existing state
        if os.path.exists(STATE_FILE):
            backup_file = f"{STATE_FILE}.backup"
            import shutil

            shutil.copy2(STATE_FILE, backup_file)

        # Write new state
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        logger.debug(f"State saved successfully at {state['last_check']}")
    except Exception as e:
        logger.error(f"Failed to save state: {e}")
        # Try to restore backup if save failed
        backup_file = f"{STATE_FILE}.backup"
        if os.path.exists(backup_file):
            import shutil

            shutil.copy2(backup_file, STATE_FILE)
            logger.info("Restored state from backup")


def is_post_recently_posted(state, feed_url, post_identifier):
    """Check if a post was recently posted"""
    if not post_identifier:
        return False

    recent_posts = state.get("recent_posts", {}).get(feed_url, [])
    return post_identifier in recent_posts


def add_post_to_recent(state, feed_url, post_identifier):
    """Add a post identifier to the recent posts list, maintaining the limit"""
    if not post_identifier:
        return

    if "recent_posts" not in state:
        state["recent_posts"] = {}

    if feed_url not in state["recent_posts"]:
        state["recent_posts"][feed_url] = []

    recent_posts = state["recent_posts"][feed_url]

    # Remove if already exists (to avoid duplicates)
    if post_identifier in recent_posts:
        recent_posts.remove(post_identifier)

    # Add to the beginning of the list
    recent_posts.insert(0, post_identifier)

    # Keep only the last N posts
    if len(recent_posts) > RECENT_POSTS_LIMIT:
        recent_posts[:] = recent_posts[:RECENT_POSTS_LIMIT]

    logger.debug(f"Added post to recent list for {feed_url}: {post_identifier}")


def is_entry_too_old(entry, max_hours=MAX_AGE_HOURS):
    """Check if entry is older than max_hours"""
    entry_date = get_entry_date(entry)
    if not entry_date:
        return False

    try:
        # Ensure we're comparing timezone-aware datetimes
        now = datetime.now(timezone.utc)
        if entry_date.tzinfo is None:
            entry_date = entry_date.replace(tzinfo=timezone.utc)
        elif entry_date.tzinfo != timezone.utc:
            # Convert to UTC for comparison
            entry_date = entry_date.astimezone(timezone.utc)

        cutoff = now - timedelta(hours=max_hours)
        return entry_date < cutoff
    except Exception:
        pass

    return False


class LinkButton(discord.ui.View):
    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link)
        )


async def fetch_rss_with_headers(
    url: str, headers: dict = None, timeout: int = FEED_TIMEOUT
):
    """Fetch RSS content with custom headers using aiohttp"""
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    return content
                else:
                    logger.error(f"HTTP {response.status} for {url}")
                    return None
    except asyncio.TimeoutError:
        logger.error(f"Timeout ({timeout}s) fetching RSS: {url}")
        return None
    except Exception as e:
        logger.error(f"Error fetching RSS {url}: {e}")
        return None


async def parse_feed_with_headers(
    url: str, headers: dict = None, timeout: int = FEED_TIMEOUT
):
    """Parse RSS feed with optional custom headers"""
    try:
        if headers:
            # Use aiohttp to fetch with headers, then parse with feedparser
            content = await fetch_rss_with_headers(url, headers, timeout)
            if content:
                # Parse the content directly without threading for simplicity
                return feedparser.parse(content)
            return None
        else:
            # Use the original method for feeds without custom headers
            return await parse_feed_with_timeout(url, timeout)
    except Exception as e:
        logger.error(f"Error parsing feed {url}: {e}")
        return None


async def parse_feed_with_timeout(url: str, timeout: int = FEED_TIMEOUT):
    """Parse RSS feed with timeout to prevent blocking the event loop"""
    try:
        # Use asyncio.to_thread for better async compatibility (Python 3.9+)
        return await asyncio.wait_for(
            asyncio.to_thread(feedparser.parse, url), timeout=timeout
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

        thread_title = f"{title}"
        thread_content = f"📰 [**{title}**]({url}) **|** {feed_cfg['role_mention']}"
    else:
        # For social posts, check if we have a meaningful title (especially for Reddit)
        if feed_cfg["name"] == "Reddit":
            # Reddit posts have meaningful titles, use them
            title = get_first_str(getattr(entry, "title", None))
            if title and title.strip():
                # Clean up the title (remove extra whitespace)
                title = title.strip()
                thread_title = title
                thread_content = f"{feed_cfg['emoji']} [**{title}**]({url}) **|** {feed_cfg['role_mention']}"
            else:
                # Fallback to generic title if no title found
                thread_title = f"New {feed_cfg['name']} Post"
                thread_content = f"{feed_cfg['emoji']} [**New {feed_cfg['name']} Post**]({url}) **|** {feed_cfg['role_mention']}"
        else:
            # For other social media, use the generic format
            thread_title = f"New {feed_cfg['name']} Post"
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
                d = await parse_feed_with_headers(
                    feed_cfg["url"], feed_cfg.get("headers")
                )
                if d and d.entries:
                    # Get the last check time for this feed
                    last_check_str = self.state.get(feed_cfg["url"])
                    if last_check_str:
                        try:
                            last_check = dateparser.parse(last_check_str)
                            # Ensure timezone-aware
                            if last_check.tzinfo is None:
                                last_check = last_check.replace(tzinfo=timezone.utc)
                        except Exception:
                            # If we can't parse the stored time, use an hour ago as fallback
                            last_check = datetime.now(timezone.utc) - timedelta(hours=1)
                    else:
                        # If no last check time, use an hour ago to avoid spam
                        last_check = datetime.now(timezone.utc) - timedelta(hours=1)

                    new_entries = []
                    latest_entry_date = None

                    # Debug logging for Twitter specifically
                    if feed_key == "twitter":
                        logger.info(
                            f"Twitter: last_check={last_check}, feed has {len(d.entries)} entries"
                        )

                    for i, entry in enumerate(d.entries):
                        try:
                            entry_date = get_entry_date(entry)
                            post_identifier = get_post_identifier(entry)

                            if not entry_date:
                                logger.warning(
                                    f"Skipping {feed_key} entry {i} - no valid date"
                                )
                                continue

                            if not post_identifier:
                                logger.warning(
                                    f"Skipping {feed_key} entry {i} - no valid identifier"
                                )
                                continue

                            # Check if this post was recently posted (duplicate prevention)
                            if is_post_recently_posted(
                                self.state, feed_cfg["url"], post_identifier
                            ):
                                logger.debug(
                                    f"Skipping {feed_key} entry {i} - recently posted duplicate"
                                )
                                continue

                            # Debug logging for Twitter
                            if feed_key == "twitter" and i < 5:  # Log first 5 entries
                                logger.info(f"Twitter entry {i}: date={entry_date}")

                            # Keep track of the latest entry date
                            if (
                                latest_entry_date is None
                                or entry_date > latest_entry_date
                            ):
                                latest_entry_date = entry_date

                            # Skip entries older than our last check
                            # Ensure both datetimes are timezone-aware for comparison
                            if entry_date.tzinfo is None:
                                entry_date_aware = entry_date.replace(
                                    tzinfo=timezone.utc
                                )
                            else:
                                entry_date_aware = entry_date

                            if last_check.tzinfo is None:
                                last_check_aware = last_check.replace(
                                    tzinfo=timezone.utc
                                )
                            else:
                                last_check_aware = last_check

                            if entry_date_aware <= last_check_aware:
                                continue

                            # Safety: Don't post old entries (older than MAX_AGE_HOURS)
                            if is_entry_too_old(entry):
                                logger.debug(f"Skipping old {feed_key} entry")
                                continue

                            new_entries.append(entry)

                            # Safety: Limit number of new entries to post at once
                            if len(new_entries) >= 10:
                                logger.warning(
                                    f"Limiting {feed_key} to 10 new entries to prevent spam"
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
                            # Sort by date (oldest first) to post in chronological order
                            new_entries.sort(
                                key=lambda x: get_entry_date(x)
                                or datetime.min.replace(tzinfo=None)
                            )

                            for entry in new_entries:
                                success = await self.send_forum_post(
                                    forum_channel, entry, feed_cfg, feed_key
                                )
                                # Only add to recent posts if successfully posted
                                if success:
                                    post_identifier = get_post_identifier(entry)
                                    add_post_to_recent(
                                        self.state, feed_cfg["url"], post_identifier
                                    )
                                    changed = True
                        else:
                            logger.error(
                                f"{feed_key} forum channel not found: {feed_cfg['forum_channel_id']}"
                            )

                    # Update the last check time to the latest entry date or current time
                    if latest_entry_date:
                        # Use the latest entry date we saw, ensure it's timezone-aware
                        if latest_entry_date.tzinfo is None:
                            latest_entry_date = latest_entry_date.replace(
                                tzinfo=timezone.utc
                            )
                        new_last_check = latest_entry_date.isoformat()
                    else:
                        # If no entries had dates, just use current time
                        new_last_check = datetime.now(timezone.utc).isoformat()

                    if new_last_check != self.state.get(feed_cfg["url"]):
                        self.state[feed_cfg["url"]] = new_last_check
                        changed = True
                        logger.info(
                            f"{feed_key}: Updated last check time to {new_last_check}"
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
            return False

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
            return True

        except Exception as e:
            logger.error(f"Failed to post {feed_name} thread: {e}")
            return False


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
