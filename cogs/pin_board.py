import discord
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
        self.starboard_channel_id = config.get("starboard_channel_id")
        if not self.starboard_channel_id:
            raise ValueError("starboard_channel_id must be set in config.json")
        self.starred_messages = {}  # Stores starred messages

    async def update_starboard(self, message):
        guild = message.guild
        starboard_channel = guild.get_channel(self.starboard_channel_id)
        if not starboard_channel:
            return

        # Fetch the latest reactions
        try:
            message = await message.channel.fetch_message(message.id)
        except discord.NotFound:
            return

        # Calculate the number of specific star emoji reactions
        star_count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == self.star_emoji:
                star_count = reaction.count
                break

        # If the message is already in the starboard
        if message.id in self.starred_messages:
            starred_message_id = self.starred_messages[message.id]
            try:
                starred_message = await starboard_channel.fetch_message(
                    starred_message_id
                )
            except discord.NotFound:
                # If the starred message was deleted, remove it from starred messages
                del self.starred_messages[message.id]
                return

            # If star count is zero, delete the starboard message
            if star_count < self.star_threshold:
                await starred_message.delete()
                del self.starred_messages[message.id]
                return

            # Update the embed
            embed = self.create_embed(message, star_count)
            await starred_message.edit(embed=embed)
        else:
            # If the message hasn't been starred yet, create a new starboard entry
            if star_count >= self.star_threshold:
                embed = self.create_embed(message, star_count)
                view = self.create_view(message)
                starred_message = await starboard_channel.send(embed=embed, view=view)
                self.starred_messages[message.id] = starred_message.id

    def create_embed(self, message, star_count):
        embed = discord.Embed(
            description=message.content or "*[No content]*", color=0xFFCD3F
        )
        embed.set_author(
            name=message.author.display_name, icon_url=message.author.avatar.url
        )
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        embed.add_field(name="Hearts", value=f"💚 {star_count}", inline=False)
        return embed

    def create_view(self, message):
        view = discord.ui.View()
        jump_button = discord.ui.Button(
            label="Jump to Message",
            url=message.jump_url,
            style=discord.ButtonStyle.link,
        )
        view.add_item(jump_button)
        return view

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        await self.update_starboard(message)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if str(payload.emoji) != self.star_emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        await self.update_starboard(message)


async def setup(bot):
    await bot.add_cog(Starboard(bot))
