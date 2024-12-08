import discord
from discord.ext import commands


class AutoPublish(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages sent by bots, including the bot itself
        if message.author.bot:
            return

        # Check if the channel is an announcement channel
        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.is_news()
        ):
            try:
                # Attempt to publish the message
                await message.publish()
                print(
                    f"Published message in {message.channel.name} (ID: {message.channel.id})"
                )
            except discord.Forbidden:
                print(
                    f"Missing permissions to publish messages in {message.channel.name}"
                )
            except discord.HTTPException as e:
                print(f"Failed to publish message in {message.channel.name}: {e}")


async def setup(bot):
    await bot.add_cog(AutoPublish(bot))
