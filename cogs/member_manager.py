from discord.ext import commands
from discord import Embed
import discord
import re
import logging

logger = logging.getLogger(__name__)


class MemberManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flag_pattern = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return

        if self.flag_pattern.search(str(payload.emoji)):
            try:
                channel = self.bot.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                user = self.bot.get_user(payload.user_id)

                for reaction in message.reactions:
                    if str(reaction.emoji) == str(payload.emoji):
                        await reaction.remove(user)
                        break
            except (discord.Forbidden, discord.NotFound, AttributeError):
                pass

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.display_name != after.display_name:
            sanitized_name = self.remove_flags_from_name(after.display_name)
            if sanitized_name != after.display_name:
                try:
                    await after.edit(nick=sanitized_name)
                    logger.info(
                        f"Removed flags from {after.display_name}, updated to: {sanitized_name}"
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"Could not change nickname for {after.name}: Missing permissions"
                    )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        welcome_channel_id = int(self.bot.config.get("welcome_channel_id", 0))
        if welcome_channel_id:
            channel = self.bot.get_channel(welcome_channel_id)
            if channel:
                try:
                    # Send the initial welcome message
                    message = await channel.send(
                        f"Welcome to the server, {member.name}! 🎉 Feel free to introduce yourself here!"
                    )

                    # Create a thread tied to the welcome message
                    thread = await message.create_thread(
                        name=f"Meet {member.name}",
                        auto_archive_duration=1440,  # Thread auto-archives after 24 hours
                    )

                    # Create an embed for the introduction
                    embed = Embed(
                        title=f"Welcome, {member.name}!",
                        description=(
                            f"Hi {member.mention}! 🎊 We're excited to have you here. "
                            "Here are a few tips for making a great introduction:\n\n"
                            "**- Share a little about yourself** Tell us where you're from, what you do, or what interests you. Are you a developer, a privacy enthusiast, or just someone curious about secure photo storage?\n\n"
                            "**- What brings you to Ente?** We'd love to hear about your journey. Are you looking for a more private way to store your photos? Interested in open-source solutions? Concerned about data privacy?\n\n"
                            "**- Be friendly and positive!** This is a welcoming space, "
                            "so let's make sure everyone feels comfortable."
                        ),
                        color=0xFFCD3F,
                    )

                    # Send the embed in the thread
                    await thread.send(embed=embed)
                    logger.info(f"Welcome thread created for {member.name}")

                except Exception as e:
                    logger.error(f"Error creating thread for {member.name}: {e}")
            else:
                logger.error(
                    f"Could not find welcome channel with ID {welcome_channel_id}"
                )
        else:
            logger.error("No welcome_channel_id configured in bot settings")


async def setup(bot):
    await bot.add_cog(MemberManager(bot))
