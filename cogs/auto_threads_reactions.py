import discord
from discord.ext import commands


class AutoThreadReactionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_channel_id = 1025978742318833684  # Replace with your channel ID
        self.reactions = ["💚"]

    @commands.Cog.listener()
    async def on_message(self, message):
        # Check if the message is in the target channel and not from a bot
        if message.channel.id == self.target_channel_id and not message.author.bot:
            # Check if the message contains an image attachment
            if any(
                attachment.content_type and attachment.content_type.startswith("image/")
                for attachment in message.attachments
            ):
                # Add reactions to the message
                for reaction in self.reactions:
                    await message.add_reaction(reaction)

                # Create a thread for the message
                try:
                    thread = await message.create_thread(
                        name=f"Discussion: {message.content[:30]}..."
                    )
                    await thread.send(
                        f"Thread created for discussing this picture by {message.author.mention}."
                    )
                except discord.Forbidden:
                    print("Bot lacks permissions to create threads.")
                except discord.HTTPException as e:
                    print(f"Failed to create thread: {e}")
            else:
                # Delete the message
                await message.delete()


async def setup(bot):
    await bot.add_cog(AutoThreadReactionsCog(bot))
