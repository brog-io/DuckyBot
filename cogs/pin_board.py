import json
import discord
from discord.ext import commands
import asyncio
import time

CONFIG_FILE = "config.json"
STARRED_MESSAGES_FILE = "starred_messages.json"

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)


# Load starred messages from file
def load_starred_messages():
    try:
        with open(STARRED_MESSAGES_FILE, "r") as f:
            content = f.read().strip()
            if not content:  # Check if file is empty
                return {}
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        # Handle both file not found and JSON decode errors
        return {}


# Save starred messages to file
def save_starred_messages(starred_messages):
    with open(STARRED_MESSAGES_FILE, "w") as f:
        json.dump(starred_messages, f)


class RateLimiter:
    def __init__(self, rate_limit_seconds=5):
        # Store the last update time for each message
        self.last_update = {}
        self.rate_limit_seconds = rate_limit_seconds
        self.backoff_multiplier = {}  # Track backoff for each message

    def can_update(self, message_id):
        """Check if we can update this message based on rate limit"""
        current_time = time.time()
        last_time = self.last_update.get(message_id, 0)
        multiplier = self.backoff_multiplier.get(message_id, 1)

        # If enough time has passed, allow the update
        if current_time - last_time >= (self.rate_limit_seconds * multiplier):
            self.last_update[message_id] = current_time
            # Reset backoff on successful update
            self.backoff_multiplier[message_id] = 1
            return True
        return False

    def apply_backoff(self, message_id):
        """Apply exponential backoff when rate limited"""
        current = self.backoff_multiplier.get(message_id, 1)
        # Increase backoff up to a maximum of 8x
        self.backoff_multiplier[message_id] = min(current * 2, 8)
        print(
            f"Applying backoff for message {message_id}: {self.backoff_multiplier[message_id]}x"
        )


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.star_threshold = 2
        self.star_emoji = "💚"
        self.starboard_channel_id = config.get("starboard_channel_id")
        if not self.starboard_channel_id:
            raise ValueError("starboard_channel_id must be set in config.json")
        self.starred_messages = load_starred_messages()
        # Add a flag to prevent concurrent updates
        self.updating = set()
        # Add rate limiter
        self.rate_limiter = RateLimiter(rate_limit_seconds=5)
        # For messages that need to be updated but are rate limited
        self.pending_updates = set()
        # Task for processing pending updates
        self.update_task = None

    async def process_pending_updates(self):
        """Process any pending updates that were rate limited"""
        try:
            while True:
                await asyncio.sleep(1.0)  # Check every second (reduced frequency)

                # Copy the set to avoid modification during iteration
                current_pending = self.pending_updates.copy()
                for message_id in current_pending:
                    if message_id in self.updating:
                        continue  # Skip if actively updating

                    if self.rate_limiter.can_update(message_id):
                        self.pending_updates.remove(message_id)
                        # Get the guild and channel
                        for guild in self.bot.guilds:
                            message_found = False
                            for channel in guild.text_channels:
                                try:
                                    message = await channel.fetch_message(
                                        int(message_id)
                                    )
                                    await self.update_starboard(message)
                                    message_found = True
                                    break
                                except discord.HTTPException as e:
                                    if e.status == 429:  # Rate limited
                                        self.rate_limiter.apply_backoff(message_id)
                                        self.pending_updates.add(
                                            message_id
                                        )  # Re-add to pending
                                        message_found = True  # Break out of the loop
                                        break
                                    continue
                                except (discord.NotFound, discord.Forbidden):
                                    continue
                            if message_found:
                                break
        except asyncio.CancelledError:
            print("Update task cancelled")
        except Exception as e:
            print(f"Error in process_pending_updates: {e}")
            # Restart the task if it fails
            self.update_task = self.bot.loop.create_task(self.process_pending_updates())

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the background task when the bot is ready"""
        self.update_task = self.bot.loop.create_task(self.process_pending_updates())

    async def update_starboard(self, message):
        message_id_str = str(message.id)

        # If already updating, skip
        if message_id_str in self.updating:
            print(f"Already updating message {message.id}, skipping")
            return

        # Check rate limit
        if not self.rate_limiter.can_update(message_id_str):
            print(f"Rate limited for message {message.id}, adding to pending updates")
            self.pending_updates.add(message_id_str)
            return

        # Add to updating set
        self.updating.add(message_id_str)

        # Add a temporary flag to prevent other instances from creating new messages
        # while this instance is processing
        creating_key = f"creating_{message_id_str}"
        if hasattr(self, creating_key) and getattr(self, creating_key):
            print(
                f"Another instance is already creating a starboard message for {message.id}"
            )
            self.updating.remove(message_id_str)
            return

        setattr(self, creating_key, True)

        try:
            guild = message.guild
            starboard_channel = guild.get_channel(self.starboard_channel_id)
            if not starboard_channel:
                print(
                    f"Error: Starboard channel with ID {self.starboard_channel_id} not found."
                )
                return

            # Always fetch the most recent version of the message
            try:
                message = await message.channel.fetch_message(message.id)
            except discord.NotFound:
                print(f"Error: Message {message.id} not found.")
                # If message was deleted, also delete from starboard if it exists
                if message_id_str in self.starred_messages:
                    try:
                        starred_message_id = self.starred_messages[message_id_str]
                        starred_message = await starboard_channel.fetch_message(
                            int(starred_message_id)
                        )
                        await starred_message.delete()
                        del self.starred_messages[message_id_str]
                        save_starred_messages(self.starred_messages)
                    except Exception as e:
                        print(f"Error cleaning up deleted message: {e}")
                return
            except discord.Forbidden:
                print(
                    f"Error: Bot doesn't have permission to access message {message.id}"
                )
                return
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    print(f"Rate limited when fetching message {message.id}")
                    self.rate_limiter.apply_backoff(message_id_str)
                    self.pending_updates.add(message_id_str)
                    return
                raise  # Re-raise other HTTP exceptions

            # Count star reactions
            star_count = 0
            for reaction in message.reactions:
                if str(reaction.emoji) == self.star_emoji:
                    star_count = reaction.count
                    break

            # Get existing starred message if it exists
            starred_message = None
            if message_id_str in self.starred_messages:
                starred_message_id = self.starred_messages[message_id_str]
                try:
                    starred_message = await starboard_channel.fetch_message(
                        int(starred_message_id)
                    )
                    print(f"Found existing starboard message: {starred_message.id}")
                except discord.NotFound:
                    print(
                        f"Starboard message {starred_message_id} not found. Deleting entry from starred_messages."
                    )
                    # Remove from tracking since we couldn't find it
                    del self.starred_messages[message_id_str]
                    save_starred_messages(self.starred_messages)
                    starred_message = None
                except discord.Forbidden:
                    print(
                        f"Error: Bot doesn't have permission to fetch message in starboard channel"
                    )
                    return
                except discord.HTTPException as e:
                    if e.status == 429:  # Rate limited
                        print(
                            f"Rate limited when fetching starboard message {starred_message_id}"
                        )
                        self.rate_limiter.apply_backoff(message_id_str)
                        self.pending_updates.add(message_id_str)
                        return
                    raise  # Re-raise other HTTP exceptions

            # Decision logic for what to do with the message
            if star_count < self.star_threshold:
                # Delete starboard message if it exists
                if starred_message:
                    print(
                        f"Star count below threshold, deleting starboard message: {starred_message.id}"
                    )
                    try:
                        await starred_message.delete()
                        del self.starred_messages[message_id_str]
                        save_starred_messages(self.starred_messages)
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limited
                            print(
                                f"Rate limited when deleting starboard message {starred_message.id}"
                            )
                            self.rate_limiter.apply_backoff(message_id_str)
                            self.pending_updates.add(message_id_str)
                            return
                        print(f"Error deleting starboard message: {e}")
            else:
                # Create/update starboard message
                embed = self.create_embed(message)
                view = self.create_view(message, star_count)

                if starred_message:
                    # Update existing message
                    try:
                        await starred_message.edit(embed=embed, view=view)
                        print(
                            f"Edited existing starboard message: {starred_message.id}"
                        )
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limited
                            print(
                                f"Rate limited when editing starboard message {starred_message.id}"
                            )
                            self.rate_limiter.apply_backoff(message_id_str)
                            self.pending_updates.add(message_id_str)
                            return
                        print(f"Error editing starboard message: {e}")
                else:
                    # Create new message
                    print(f"Creating new starboard message for {message.id}")
                    try:
                        starred_message = await starboard_channel.send(
                            embed=embed, view=view
                        )
                        self.starred_messages[message_id_str] = str(starred_message.id)
                        save_starred_messages(self.starred_messages)
                        print(
                            f"Created and saved new starboard message: {starred_message.id}"
                        )
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limited
                            print(
                                f"Rate limited when creating starboard message for {message.id}"
                            )
                            self.rate_limiter.apply_backoff(message_id_str)
                            self.pending_updates.add(message_id_str)
                            return
                        print(f"Error creating starboard message: {e}")
        finally:
            # Always clean up our flags
            if message_id_str in self.updating:
                self.updating.remove(message_id_str)
            if hasattr(self, creating_key):
                setattr(self, creating_key, False)

    def create_embed(self, message):
        embed = discord.Embed(
            description=message.content or "*[No content]*", color=0xFFCD3F
        )

        # Fix for potential avatar issues
        author_name = message.author.display_name
        author_icon_url = None
        if message.author.avatar:
            author_icon_url = message.author.avatar.url

        embed.set_author(name=author_name, icon_url=author_icon_url)

        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        return embed

    def create_view(self, message, star_count):
        view = discord.ui.View()

        jump_button = discord.ui.Button(
            label=f"Jump to Message | {self.star_emoji} {star_count}",
            url=message.jump_url,
            style=discord.ButtonStyle.link,
        )
        view.add_item(jump_button)

        return view

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return

            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return

            try:
                message = await channel.fetch_message(payload.message_id)
                await self.update_starboard(message)
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    message_id_str = str(payload.message_id)
                    self.rate_limiter.apply_backoff(message_id_str)
                    self.pending_updates.add(message_id_str)
                else:
                    print(f"Error in on_raw_reaction_add: {e}")
            except (discord.NotFound, discord.Forbidden):
                print(
                    f"Could not access message {payload.message_id} in channel {payload.channel_id}"
                )
                return
        except Exception as e:
            print(f"Error in on_raw_reaction_add: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return

            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return

            try:
                message = await channel.fetch_message(payload.message_id)
                await self.update_starboard(message)
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    message_id_str = str(payload.message_id)
                    self.rate_limiter.apply_backoff(message_id_str)
                    self.pending_updates.add(message_id_str)
                else:
                    print(f"Error in on_raw_reaction_remove: {e}")
            except (discord.NotFound, discord.Forbidden):
                print(
                    f"Could not access message {payload.message_id} in channel {payload.channel_id}"
                )
                return
        except Exception as e:
            print(f"Error in on_raw_reaction_remove: {e}")


def setup(bot):
    bot.add_cog(Starboard(bot))
