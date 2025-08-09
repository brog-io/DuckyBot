import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
from dateutil import parser as dateparser
import aiohttp
import logging
from datetime import datetime, timedelta, timezone
import asyncio

logger = logging.getLogger(__name__)

FEEDS = {
    "blog": {
        "url": "https://ente.io/rss.xml",
        "role_mention": "<@&1050340002028077106>",
        "channel_id": 1121470028223623229,
        "channel_type": "forum",  # "forum" or "text"
        "tag_id": 1403037438519017553,  # Only used for forum channels
        "button_text": "Read Blog",
        "emoji": "📰",
        "name": "Blog",
        "type": "blog",
    },
    "joy": {
        "url": "https://joy.ente.io/rss.xml",
        "role_mention": "<@&1050340002028077106>",
        "channel_id": 1121470028223623229,
        "channel_type": "forum",
        "tag_id": 1403037470219829390,
        "button_text": "Read Blog",
        "emoji": "<:joy_mug:1402991225547657326>",
        "name": "Joy",
        "type": "blog",
    },
    "mastodon": {
        "url": "https://fosstodon.org/@ente.rss",
        "button_text": "View Post",
        "role_mention": "<@&1214608287597723739>",
        "channel_id": 1400567228314943529,
        "channel_type": "forum",
        "tag_id": 1400569634746269918,
        "emoji": "<:Mastodon_Logo:1312884790210461756>",
        "name": "Mastodon",
        "type": "social",
    },
    "bluesky": {
        "url": "https://bsky.app/profile/did:plc:uah5jix7ykdrae7a2ezp3rye/rss",
        "button_text": "View Post",
        "role_mention": "<@&1400571735904092230>",
        "channel_id": 1400567228314943529,
        "channel_type": "forum",
        "tag_id": 1400569656971886803,
        "emoji": "<:Bluesky_Logo:1400570292740296894>",
        "name": "Bluesky",
        "type": "social",
    },
    "reddit": {
        "url": "https://www.reddit.com/r/enteio/new/.rss",
        "button_text": "View Post",
        "role_mention": "<@&1400571795848958052>",
        "channel_id": 1403446960014360616,
        "channel_type": "text",
        "emoji": "<:Reddit_Logo:1400570705073934397>",
        "name": "Reddit",
        "type": "community",
        "headers": {"User-Agent": "Ducky/1.0 (https://ente.io; brogio@ente.io)"},
    },
    "github": {
        "url": "https://github.com/ente-io/ente/discussions/categories/general.atom",
        "button_text": "View Post",
        "role_mention": "<@&1403399186023579688>",
        "channel_id": 1403676221233037352,
        "channel_type": "text",
        "emoji": "<:GitHub_Logo:1403399690753675315>",
        "name": "GitHub",
        "type": "community",
    },
}

STATE_FILE = "ente_rss_state.json"
RECENT_POSTS_LIMIT = 5
MAX_AGE_HOURS = 72
FEED_TIMEOUT = 60


def get_entry_date(entry):
    """Extract publication date from entry"""
    for date_field in ["published", "updated", "created"]:
        date_str = getattr(entry, date_field, None)
        if date_str:
            try:
                parsed_date = dateparser.parse(date_str)
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                return parsed_date
            except Exception:
                continue
    return None


def get_post_identifier(entry):
    """Get unique identifier for a post"""
    return getattr(entry, "link", None) or getattr(entry, "id", None)


def get_first_str(val):
    """Extract string value from various feedparser structures"""
    if isinstance(val, list) and val:
        item = val[0]
        if isinstance(item, dict) and "value" in item:
            return item["value"]
        return str(item)
    return val


def create_clean_state():
    """Create a clean state structure"""
    now = datetime.now(timezone.utc)
    start_time = (now - timedelta(hours=24)).isoformat()

    state = {"last_updated": now.isoformat(), "feeds": {}, "recent_posts": {}}

    for feed_key, feed_cfg in FEEDS.items():
        url = feed_cfg["url"]
        state["feeds"][url] = {"name": feed_key, "last_check": start_time}
        state["recent_posts"][url] = []

    return state


def load_state():
    """Load and migrate state file"""
    if not os.path.exists(STATE_FILE):
        logger.info("Creating new state file")
        return create_clean_state()

    try:
        with open(STATE_FILE, "r") as f:
            old_state = json.load(f)

        # Check if we need to migrate from old format
        if "feeds" not in old_state:
            logger.info("Migrating state to new format")
            state = create_clean_state()

            # Migrate existing data
            for feed_key, feed_cfg in FEEDS.items():
                url = feed_cfg["url"]
                if url in old_state:
                    # Only migrate if it's a valid timestamp
                    old_value = old_state[url]
                    if isinstance(old_value, str) and (
                        "T" in old_value or "Z" in old_value
                    ):
                        try:
                            # Validate it's a proper timestamp
                            dateparser.parse(old_value)
                            state["feeds"][url]["last_check"] = old_value
                            logger.info(f"Migrated {feed_key} timestamp: {old_value}")
                        except Exception:
                            logger.warning(
                                f"Invalid timestamp for {feed_key}, using default"
                            )

                # Migrate recent posts
                if "recent_posts" in old_state and url in old_state["recent_posts"]:
                    state["recent_posts"][url] = old_state["recent_posts"][url][
                        :RECENT_POSTS_LIMIT
                    ]
                    logger.info(
                        f"Migrated {len(state['recent_posts'][url])} recent posts for {feed_key}"
                    )

            # Clean up old state file
            backup_file = f"{STATE_FILE}.old_backup"
            with open(backup_file, "w") as f:
                json.dump(old_state, f, indent=2)
            logger.info(f"Backed up old state to {backup_file}")

            save_state(state)
            return state
        else:
            # New format, just validate and clean
            state = old_state
            state["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Ensure all current feeds exist
            for feed_key, feed_cfg in FEEDS.items():
                url = feed_cfg["url"]
                if url not in state["feeds"]:
                    state["feeds"][url] = {
                        "name": feed_key,
                        "last_check": (
                            datetime.now(timezone.utc) - timedelta(hours=24)
                        ).isoformat(),
                    }
                if url not in state["recent_posts"]:
                    state["recent_posts"][url] = []

            return state

    except Exception as e:
        logger.error(f"Error loading state: {e}")
        logger.info("Creating new state due to error")
        return create_clean_state()


def save_state(state):
    """Save state with backup"""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    try:
        # Create backup
        if os.path.exists(STATE_FILE):
            backup_file = f"{STATE_FILE}.backup"
            import shutil

            shutil.copy2(STATE_FILE, backup_file)

        # Write new state
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        logger.debug("State saved successfully")

    except Exception as e:
        logger.error(f"Failed to save state: {e}")
        # Restore backup if save failed
        backup_file = f"{STATE_FILE}.backup"
        if os.path.exists(backup_file):
            import shutil

            shutil.copy2(backup_file, STATE_FILE)
            logger.info("Restored from backup")


def is_post_recent(state, feed_url, post_id):
    """Check if post was recently posted"""
    if not post_id:
        return False
    return post_id in state["recent_posts"].get(feed_url, [])


def add_recent_post(state, feed_url, post_id):
    """Add post to recent posts list"""
    if not post_id:
        return

    recent = state["recent_posts"].setdefault(feed_url, [])

    if post_id in recent:
        recent.remove(post_id)

    recent.insert(0, post_id)

    if len(recent) > RECENT_POSTS_LIMIT:
        recent[:] = recent[:RECENT_POSTS_LIMIT]


def is_entry_too_old(entry):
    """Check if entry is too old to post"""
    entry_date = get_entry_date(entry)
    if not entry_date:
        return False

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
        if entry_date.tzinfo is None:
            entry_date = entry_date.replace(tzinfo=timezone.utc)
        elif entry_date.tzinfo != timezone.utc:
            entry_date = entry_date.astimezone(timezone.utc)

        return entry_date < cutoff
    except Exception:
        return False


class LinkButton(discord.ui.View):
    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link)
        )


async def fetch_feed_content(url: str, headers: dict = None):
    """Fetch RSS feed content"""
    try:
        timeout = aiohttp.ClientTimeout(total=FEED_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logger.error(f"HTTP {response.status} for {url}")
                    return None
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching {url}")
        return None
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


async def parse_feed(url: str, headers: dict = None):
    """Parse RSS feed"""
    try:
        if headers:
            content = await fetch_feed_content(url, headers)
            if content:
                return feedparser.parse(content)
            return None
        else:
            return await asyncio.wait_for(
                asyncio.to_thread(feedparser.parse, url), timeout=FEED_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error(f"Timeout parsing {url}")
        return None
    except Exception as e:
        logger.error(f"Error parsing {url}: {e}")
        return None


def create_content(entry, feed_cfg, is_forum=True):
    """Create content for forum thread or text message"""
    url = entry.link
    title = get_first_str(getattr(entry, "title", ""))

    if is_forum:
        # Forum thread content
        if feed_cfg["type"] == "blog":
            thread_title = title or "New Blog Post"
            thread_content = (
                f"📰 [**{thread_title}**]({url}) **|** {feed_cfg['role_mention']}"
            )
        elif title:
            thread_title = title.strip()
            thread_content = f"{feed_cfg['emoji']} [**{thread_title}**]({url}) **|** {feed_cfg['role_mention']}"
        else:
            # Fallback for feeds without titles
            thread_title = f"New {feed_cfg['name']} Post"
            thread_content = f"{feed_cfg['emoji']} [**{thread_title}**]({url}) **|** {feed_cfg['role_mention']}"

        # Ensure title fits Discord limits
        if len(thread_title) > 95:
            thread_title = thread_title[:92] + "..."

        return thread_title, thread_content
    else:
        # Text channel message content - now using same hyperlink format as forum
        if title:
            message_title = title.strip()
            message_content = f"{feed_cfg['emoji']} [**{message_title}**]({url}) **|** {feed_cfg['role_mention']}"
        else:
            # Fallback for feeds without titles
            message_title = f"New {feed_cfg['name']} Post"
            message_content = f"{feed_cfg['emoji']} [**{message_title}**]({url}) **|** {feed_cfg['role_mention']}"

        return None, message_content


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

        logger.info("Starting feed check")

        for feed_key, feed_cfg in FEEDS.items():
            try:
                url = feed_cfg["url"]
                logger.debug(f"Checking {feed_key}")

                # Parse feed
                feed_data = await parse_feed(url, feed_cfg.get("headers"))
                if not feed_data or not feed_data.entries:
                    if feed_data is None:
                        logger.error(f"Failed to parse {feed_key} feed")
                    else:
                        logger.debug(f"No entries in {feed_key} feed")
                    continue

                # Get last check time
                feed_info = self.state["feeds"].get(url, {})
                last_check_str = feed_info.get("last_check")

                try:
                    last_check = dateparser.parse(last_check_str)
                    if last_check.tzinfo is None:
                        last_check = last_check.replace(tzinfo=timezone.utc)
                except Exception:
                    last_check = datetime.now(timezone.utc) - timedelta(hours=1)
                    logger.warning(
                        f"{feed_key}: Invalid last check time, using 1 hour ago"
                    )

                # Process entries
                new_entries = []
                latest_date = None

                for i, entry in enumerate(feed_data.entries):
                    entry_date = get_entry_date(entry)
                    post_id = get_post_identifier(entry)

                    if not entry_date or not post_id:
                        logger.debug(
                            f"{feed_key} entry {i}: Missing date or ID, skipping"
                        )
                        continue

                    # Track latest date
                    if latest_date is None or entry_date > latest_date:
                        latest_date = entry_date

                    # Skip if already posted
                    if is_post_recent(self.state, url, post_id):
                        logger.debug(f"{feed_key} entry {i}: Already posted")
                        continue

                    # Skip if older than last check
                    if entry_date.tzinfo is None:
                        entry_date = entry_date.replace(tzinfo=timezone.utc)

                    if entry_date <= last_check:
                        continue

                    # Skip if too old
                    if is_entry_too_old(entry):
                        logger.debug(f"{feed_key} entry {i}: Too old")
                        continue

                    new_entries.append(entry)

                    # Limit to prevent spam
                    if len(new_entries) >= 10:
                        logger.warning(f"Limiting {feed_key} to 10 entries")
                        break

                # Post new entries
                if new_entries:
                    channel = self.bot.get_channel(feed_cfg["channel_id"])
                    if not channel:
                        logger.error(f"{feed_key}: Channel not found")
                        continue

                    logger.info(f"Posting {len(new_entries)} new {feed_key} entries")

                    # Sort by date (oldest first)
                    new_entries.sort(
                        key=lambda x: get_entry_date(x)
                        or datetime.min.replace(tzinfo=timezone.utc)
                    )

                    for entry in new_entries:
                        if await self.post_to_channel(
                            channel, entry, feed_cfg, feed_key
                        ):
                            post_id = get_post_identifier(entry)
                            add_recent_post(self.state, url, post_id)
                            changed = True
                            await asyncio.sleep(1)  # Rate limit protection

                # Update last check time
                if latest_date:
                    new_check_time = latest_date.isoformat()
                    if new_check_time != self.state["feeds"][url]["last_check"]:
                        self.state["feeds"][url]["last_check"] = new_check_time
                        changed = True
                        logger.debug(
                            f"{feed_key}: Updated last check to {new_check_time}"
                        )

            except Exception as e:
                logger.error(f"Error processing {feed_key}: {e}")

        if changed:
            save_state(self.state)

        logger.info("Feed check completed")

    async def post_to_channel(self, channel, entry, feed_cfg, feed_name):
        """Post entry to Discord channel (forum or text)"""
        url = entry.link
        channel_type = feed_cfg.get("channel_type", "forum")

        try:
            if channel_type == "forum" and isinstance(channel, discord.ForumChannel):
                # Forum channel posting
                thread_title, thread_content = create_content(
                    entry, feed_cfg, is_forum=True
                )

                thread_args = {
                    "name": thread_title,
                    "content": thread_content,
                    "view": LinkButton(url, feed_cfg["button_text"]),
                }

                # Add tags if configured
                if "tag_id" in feed_cfg:
                    tag = discord.utils.get(
                        channel.available_tags, id=feed_cfg["tag_id"]
                    )
                    if tag:
                        thread_args["applied_tags"] = [tag]

                await channel.create_thread(**thread_args)
                logger.info(f"Posted {feed_name} forum thread: {thread_title}")
                return True

            elif channel_type == "text" and isinstance(channel, discord.TextChannel):
                # Text channel posting
                _, message_content = create_content(entry, feed_cfg, is_forum=False)

                await channel.send(
                    content=message_content,
                    view=LinkButton(url, feed_cfg["button_text"]),
                )
                logger.info(f"Posted {feed_name} text message")
                return True

            else:
                logger.error(
                    f"Channel type mismatch for {feed_name}: expected {channel_type}, got {type(channel).__name__}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to post {feed_name}: {e}")
            return False


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSFeedCog(bot))
