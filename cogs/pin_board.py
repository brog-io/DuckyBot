import discord
from discord import app_commands
from discord.ext import commands
import json

CONFIG_FILE = "config.json"

# Load config
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.star_threshold = 1  # Minimum reactions required
        self.star_emoji = "💚"
        self.starboard_channel_id = config[
            "starboard_channel_id"
        ]  # Get from config.json
        self.starred_messages = {}  # Stores starred messages

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.emoji.name != self.star_emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        # If the message was already starred
        if message.id in self.starred_messages:
            starred_message_id = self.starred_messages[message.id]
            starboard_channel = guild.get_channel(self.starboard_channel_id)

            try:
                starred_message = await starboard_channel.fetch_message(
                    starred_message_id
                )
            except discord.NotFound:
                # If the starred message was deleted, remove it from starred messages
                del self.starred_messages[message.id]
                return

            # Calculate the number of specific star emoji reactions
            star_count = sum(1 for r in message.reactions if r.emoji == self.star_emoji)

            # If star count is zero, delete the starboard message
            if star_count == 0:
                await starred_message.delete()
                del self.starred_messages[message.id]
                return

            # Update the embed if the star count changed
            embed = discord.Embed(description=message.content, color=0xFFCD3F)
            embed.set_author(
                name=message.author.display_name, icon_url=message.author.avatar.url
            )
            if message.attachments:
                embed.set_image(url=message.attachments[0].url)

            # Add the reaction count to the embed
            embed.add_field(name="Hearts", value=f"💚 {star_count}", inline=False)

            # Edit the existing starboard message with the updated embed
            await starred_message.edit(embed=embed)

            return

        # If the message hasn't been starred yet, create a new starboard entry
        star_count = sum(1 for r in message.reactions if r.emoji == self.star_emoji)

        if star_count >= self.star_threshold:
            starboard_channel = guild.get_channel(self.starboard_channel_id)
            if not starboard_channel:
                return

            embed = discord.Embed(description=message.content, color=0xFFCD3F)
            embed.set_author(
                name=message.author.display_name, icon_url=message.author.avatar.url
            )
            if message.attachments:
                embed.set_image(url=message.attachments[0].url)

            # Add the reaction count to the embed
            embed.add_field(name="Hearts", value=f"💚 {star_count}", inline=False)

            view = discord.ui.View()
            jump_button = discord.ui.Button(
                label="Jump to Message",
                url=message.jump_url,
                style=discord.ButtonStyle.link,
            )
            view.add_item(jump_button)

            starred_message = await starboard_channel.send(embed=embed, view=view)
            self.starred_messages[message.id] = starred_message.id

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if payload.emoji.name != self.star_emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        # If the message was already starred
        if message.id in self.starred_messages:
            starred_message_id = self.starred_messages[message.id]
            starboard_channel = guild.get_channel(self.starboard_channel_id)

            try:
                starred_message = await starboard_channel.fetch_message(
                    starred_message_id
                )
            except discord.NotFound:
                # If the starred message was deleted, remove it from starred messages
                del self.starred_messages[message.id]
                return

            # Calculate the number of specific star emoji reactions
            star_count = sum(1 for r in message.reactions if r.emoji == self.star_emoji)

            # If star count is zero, delete the starboard message
            if star_count == 0:
                await starred_message.delete()
                del self.starred_messages[message.id]
                return

            # Update the embed if the star count changed
            embed = discord.Embed(description=message.content, color=0xFFCD3F)
            embed.set_author(
                name=message.author.display_name, icon_url=message.author.avatar.url
            )
            if message.attachments:
                embed.set_image(url=message.attachments[0].url)

            # Add the reaction count to the embed
            embed.add_field(name="Hearts", value=f"💚 {star_count}", inline=False)

            # Edit the existing starboard message with the updated embed
            await starred_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(Starboard(bot))
