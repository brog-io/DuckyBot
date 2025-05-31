# cogs/rss_feed.py
import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
import re
from dateutil import parser as dateparser

# ---- CONFIGURATION ----

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

STATE_FILE = "ente_rss_state.json"


def load_state():
    """
    Load the state file that keeps track of the last processed entry for each feed.
    If no state file exists, set each feed's last ID to the *latest* post, so old posts are not sent.
    """
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    # If missing, fetch the latest entry from each feed and use its ID
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
    """
    Save the current state of the feeds to a JSON file.

    Args:
        state (dict): The state dictionary to be saved.
    """
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class LinkButton(discord.ui.View):
    """
    A Discord UI view containing a single link button.
    """

    def __init__(self, url: str, label: str):
        """
        Initialize the view with a link button.

        Args:
            url (str): The URL to open when the button is clicked.
            label (str): The text to display on the button.
        """
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link)
        )


class RSSFeedCog(commands.Cog):
    """
    A cog that monitors multiple RSS feeds and posts updates to different Discord channels:
    - Blog posts are posted as new threads in a forum channel.
    - Mastodon posts are posted as rich embeds in a text channel.
    """

    def __init__(self, bot: commands.Bot):
        """
        Initialize the cog, loading state and starting the feed checking task.

        Args:
            bot (commands.Bot): The Discord bot instance.
        """
        self.bot = bot
        self.state = load_state()
        if BLOG_FEED["url"] not in self.state:
            self.state[BLOG_FEED["url"]] = None
        if MASTODON_FEED["url"] not in self.state:
            self.state[MASTODON_FEED["url"]] = None
        save_state(self.state)
        self.check_feeds.start()

    def cog_unload(self):
        """
        Cancel the background feed checking task when the cog is unloaded.
        """
        self.check_feeds.cancel()

    @tasks.loop(minutes=5)
    async def check_feeds(self):
        """
        Periodically check each configured RSS feed for new entries.
        For each new entry, posts to the configured channel (forum for blog, text for Mastodon).
        """
        await self.bot.wait_until_ready()
        changed = False

        # --- BLOG FEED: post to forum channel as new thread ---
        try:
            d = feedparser.parse(BLOG_FEED["url"])
            if d.entries:
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                if self.state.get(BLOG_FEED["url"]) != entry_id:
                    # Find all new entries
                    new_entries = []
                    for entry in d.entries:
                        eid = getattr(entry, "id", entry.link)
                        if eid == self.state.get(BLOG_FEED["url"]):
                            break
                        new_entries.append(entry)
                    forum_channel = self.bot.get_channel(BLOG_FEED["forum_channel_id"])
                    for entry in reversed(new_entries):
                        title = entry.title.replace("[", "").replace("]", "")
                        url = entry.link
                        msg = f"📰 **[{title}](url)** | {BLOG_FEED['role_mention']}"
                        view = LinkButton(url, BLOG_FEED["button_text"])
                        # Create a new thread in the forum channel for each new post
                        if forum_channel and isinstance(
                            forum_channel, discord.ForumChannel
                        ):
                            await forum_channel.create_thread(
                                name=title[:95], content=msg, view=view
                            )
                    self.state[BLOG_FEED["url"]] = entry_id
                    changed = True
        except Exception as e:
            print(f"RSS error for blog: {e}")

        # --- MASTODON FEED: post as embed to text channel ---
        try:
            d = feedparser.parse(MASTODON_FEED["url"])
            if d.entries:
                latest = d.entries[0]
                entry_id = getattr(latest, "id", latest.link)
                if self.state.get(MASTODON_FEED["url"]) != entry_id:
                    # Find all new entries
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
                        )
                    self.state[MASTODON_FEED["url"]] = entry_id
                    changed = True
        except Exception as e:
            print(f"RSS error for Mastodon: {e}")

        if changed:
            save_state(self.state)

    async def send_mastodon_embed(
        self, channel: discord.TextChannel, entry, role_mention: str, button_text: str
    ):
        """
        Send a Mastodon post as a rich embed to the specified text channel.

        Args:
            channel (discord.TextChannel): The Discord channel to send the embed to.
            entry (feedparser.FeedParserDict): The RSS feed entry to post.
            role_mention (str): The role or string to mention in the post.
            button_text (str): The text for the action button.
        """
        author = entry.get("author", "Ente(@fosstodon.org)")
        summary = entry.get("summary", "")
        link = entry.link
        published = entry.get("published", None)

        # Remove html tags from summary
        clean_text = re.sub(r"<.*?>", "", summary).strip()

        # Get icon and image from feed entry
        author_icon = entry.get("meta_image__@__url")
        img_url = entry.get("media_content__@__url")  # use None if not present

        # Format timestamp
        timestamp = None
        if published:
            try:
                timestamp = dateparser.parse(published)
            except Exception:
                timestamp = None

        embed = discord.Embed(description=clean_text, color=0x1DB954)
        embed.set_author(name=author, url=link, icon_url=author_icon)
        if img_url:
            embed.set_image(url=img_url)
        if timestamp:
            embed.timestamp = timestamp

        await channel.send(
            content=role_mention, embed=embed, view=LinkButton(link, button_text)
        )


async def setup(bot: commands.Bot):
    """
    Setup function for adding the RSSFeedCog to the bot.

    Args:
        bot (commands.Bot): The Discord bot instance.
    """
    await bot.add_cog(RSSFeedCog(bot))
