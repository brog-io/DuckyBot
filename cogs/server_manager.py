import discord
from discord.ext import commands
from discord.ui import Button, View
from discord import app_commands
import re
import logging

logger = logging.getLogger(__name__)

TARGET_CHANNEL_ID = 1025978742318833684
AUTO_THREAD_REACTIONS = ["⭐"]

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def is_image_attachment(attachment: discord.Attachment) -> bool:
    """
    Determines if an attachment is an image.

    Args:
        attachment (discord.Attachment): The attachment to check.

    Returns:
        bool: True if the attachment is an image, False otherwise.
    """
    if hasattr(attachment, "content_type") and attachment.content_type:
        return attachment.content_type.startswith("image/")
    return attachment.url.lower().endswith(IMAGE_EXTENSIONS)


class MessageLinkButton(Button):
    """
    A button that links to the referenced Discord message.
    """

    def __init__(self, url: str):
        """
        Initialize the message link button.

        Args:
            url (str): The URL of the Discord message.
        """
        super().__init__(label="Go to Message", url=url, style=discord.ButtonStyle.link)


class ServerManager(commands.Cog):
    """
    Manages server onboarding, information messages, message link previews,
    automatic publishing of announcement messages, and auto-thread creation.
    """

    def __init__(self, bot):
        """
        Initialize the ServerManager cog.

        Args:
            bot (commands.Bot): The Discord bot instance.
        """
        self.bot = bot
        self.message_link_pattern = re.compile(
            r"https?:\/\/(?:.*\.)?discord\.com\/channels\/(\d+)\/(\d+)\/(\d+)"
        )
        self.target_channel_id = TARGET_CHANNEL_ID
        self.reactions = AUTO_THREAD_REACTIONS

    @app_commands.command(name="welcome")
    @app_commands.default_permissions(administrator=True)
    async def send_welcome(self, interaction: discord.Interaction):
        """
        Sends a welcome message in the configured welcome channel with community info and navigation buttons.

        Args:
            interaction (discord.Interaction): The interaction for the command.
        """
        channel = self.bot.get_channel(953697188544925756)
        if channel:
            embed = discord.Embed(
                title="Welcome to the Ente Community!",
                description=(
                    "## Explore our privacy-first services:\n"
                    "**Ente Photos**: Secure, private photo storage.\n"
                    "**Ente Auth**: Easy, privacy-focused authentication.\n\n"
                    "We’re glad to have you here! 🔐\n"
                    "## Community Guidelines:\n"
                    "• **Respect Privacy**: No sharing of personal information.\n"
                    "• **Be Kind**: Keep interactions respectful and constructive.\n"
                    "• **Stay On Topic**: Use the right channels for your discussions.\n"
                    "• **No Spam**: Avoid posting irrelevant or repetitive content.\n"
                    "• **Follow Guidelines**: Abide by the community and platform rules.\n"
                ),
                color=0xFFCD3F,
            )
            embed.set_image(
                url="https://images-ext-1.discordapp.net/external/AuEowaRQWXAAS80ilWitGttrcF_u1MetYh2ArvjkXuE/https/i.imgur.com/wlZ8Kw4.png?format=webp&quality=lossless&width=1307&height=642"
            )
            view = View()
            view.add_item(
                Button(
                    label="Roles",
                    emoji=":roles:1439927556202823712",
                    custom_id="Roles",
                )
            )
            view.add_item(
                Button(
                    label="Channels",
                    emoji=":channels:1439927871698239578",
                    custom_id="Channels",
                )
            )
            view.add_item(Button(label="Website", url="https://ente.io"))
            await channel.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Handles button interactions for information messages.

        Args:
            interaction (discord.Interaction): The interaction event triggered by a button click.
        """
        if interaction.type != discord.InteractionType.component:
            return
        if interaction.data.get("custom_id") == "Roles":
            roles_embed = discord.Embed(
                description=(
                    "# Community Roles\n"
                    "- <@&950276268593659925>: Ente Employee.\n"
                    "- <@&950275266045960254>: Keeping things smooth and respectful.\n"
                    "- <@&1307599568824700969>: Driving innovation with ideas and feedback. Use `/role contributor` to claim\n"
                    "- <@&1376169325064355940>: Starred the GitHub repo. Use `/role stargazer` to claim\n"
                    "# Service Roles\n"
                    "- <@&1312807194487685231>: Focused on all things Ente Photos.\n"
                    "- <@&1099362028147183759>: Focused on all things Ente Auth.\n"
                    "# Notification Roles\n"
                    "- <@&1050340002028077106>: Get notified when a blog post is posted.\n"
                    "- <@&1214608287597723739>: Get notified when Ente posts on Mastodon."
                ),
                color=0xFFCD3F,
            )
            roles_view = View()
            roles_view.add_item(
                Button(
                    label="Edit Roles",
                    emoji=":roles:1439927556202823712",
                    url="https://discord.com/channels/948937918347608085/customize-community",
                )
            )
            await interaction.response.send_message(
                embed=roles_embed, view=roles_view, ephemeral=True
            )
        elif interaction.data.get("custom_id") == "Channels":
            channels_embed = discord.Embed(
                description=(
                    "- 👋 **WELCOME**\n"
                    "  - **<#953697188544925756>**: Key details about the Ente Community, rules, and guidelines.\n"
                    "  - **<#948956829982031912>**: Updates and news from the Ente team.\n"
                    "  - **<#1121470028223623229>**: Links to blog posts and articles.\n"
                    "  - **<#973177352446173194>**: Updates about Ente’s presence on Mastodon.\n\n"
                    "- 🐣 **ENTE**\n"
                    "  - **<#948937919027105865>**: Discussions and support related to Ente Photos.\n"
                    "  - **<#1051153671985045514>**: Focused on Ente Auth and authentication-related queries.\n"
                    "  - **<#1383504546361380995>**: Discussions for those interested in hosting Ente services themselves.\n"
                    "  - **<#1121126215995113552>**: Share suggestions, report bugs, or provide input on Ente products and community.\n"
                    "  - **<#1364139133794123807>**: Ask for help, Ducky will try to help you out!.\n\n"
                    "- 💬 **COMMUNITY**\n"
                    "  - **<#1380262760994177135>**: A place to see who joined the community.\n"
                    "  - **<#953968250553765908>**: Casual conversations unrelated to Ente products.\n"
                    "  - **<#1025978742318833684>**: A place for sharing your favorite memories.\n"
                    "  - **<#1335538661185421386>**: Our wall of love, a place where all our nice reviews get shown\n"
                    "  - **<#948956465635397684>**: Share fun and lighthearted content."
                ),
                color=0xFFCD3F,
            )
            await interaction.response.send_message(
                embed=channels_embed, ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Processes new messages for auto-publishing, message link previews, and auto-threading for images.

        Args:
            message (discord.Message): The received message object.
        """
        if message.author.bot:
            return

        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.is_news()
        ):
            try:
                await message.publish()
                logger.info(
                    f"Published message in {message.channel.name} (ID: {message.channel.id})"
                )
            except discord.Forbidden:
                logger.warning(
                    f"Missing permissions to publish messages in {message.channel.name} (ID: {message.channel.id})"
                )
            except discord.HTTPException as e:
                logger.error(
                    f"Failed to publish message in {message.channel.name} (ID: {message.channel.id}): {str(e)}"
                )

        for match in self.message_link_pattern.finditer(message.content):
            guild_id, channel_id, message_id = match.groups()
            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    continue
                referenced_message = await channel.fetch_message(int(message_id))
                if not referenced_message:
                    continue
                embed = discord.Embed(
                    description=referenced_message.content or "*[No content]*",
                    timestamp=referenced_message.created_at,
                    color=0xFFCD3F,
                )
                embed.set_author(
                    name=referenced_message.author.display_name,
                    icon_url=referenced_message.author.display_avatar.url,
                )
                img = next(
                    (
                        a
                        for a in referenced_message.attachments
                        if is_image_attachment(a)
                    ),
                    None,
                )
                if img:
                    embed.set_image(url=img.url)
                view = View()
                view.add_item(MessageLinkButton(match.group(0)))
                await message.reply(embed=embed, view=view, mention_author=False)
            except Exception as e:
                logger.error(f"Error processing message link: {e}")

        if message.channel.id == self.target_channel_id and not message.author.bot:
            has_image = any(
                is_image_attachment(attachment) for attachment in message.attachments
            )
            if has_image:
                for reaction in self.reactions:
                    await message.add_reaction(reaction)
                try:
                    thread = await message.create_thread(
                        name=(
                            f"Discussion: {message.content[:30]}..."
                            if message.content
                            else "Discussion"
                        )
                    )
                    await thread.send(
                        f"Thread created for discussing this picture by {message.author.mention}."
                    )
                except discord.Forbidden:
                    logger.error("Bot lacks permissions to create threads.")
                except discord.HTTPException as e:
                    logger.error(f"Failed to create thread: {e}")
            else:
                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.error("Bot lacks permissions to delete messages.")
                except discord.NotFound:
                    logger.warning("Message was already deleted.")
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete message: {e}")


async def setup(bot):
    """
    Adds the ServerManager cog to the bot.

    Args:
        bot (commands.Bot): The Discord bot instance.
    """
    await bot.add_cog(ServerManager(bot))
