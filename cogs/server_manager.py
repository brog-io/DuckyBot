import discord
from discord.ext import commands
from discord.ui import Button, View
from discord import app_commands
from discord.http import Route
import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# --------- Config ---------
WELCOME_CHANNEL_ID = 953697188544925756
TARGET_CHANNEL_ID = 1025978742318833684
AUTO_THREAD_REACTIONS = ["⭐"]

# Message flag: IS_COMPONENTS_V2 (1 << 15)
MESSAGE_FLAG_IS_COMPONENTS_V2 = 1 << 15

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# Custom emoji IDs used in your server
ROLES_EMOJI_ID = 1439927556202823712
CHANNELS_EMOJI_ID = 1439927871698239578


def is_image_attachment(attachment: discord.Attachment) -> bool:
    """Return True if an attachment is an image (via content_type or file extension fallback)."""
    if getattr(attachment, "content_type", None):
        return attachment.content_type.startswith("image/")
    return attachment.url.lower().endswith(IMAGE_EXTENSIONS)


def _safe_text(s: Optional[str], max_len: int = 1800) -> str:
    """Trim text to a safe size for display; keeps markdown but avoids huge payloads."""
    if not s:
        return ""
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


class MessageLinkButton(Button):
    """A simple link button that takes the user to the referenced Discord message."""

    def __init__(self, message_url: str):
        super().__init__(
            style=discord.ButtonStyle.link,
            label="Open Message",
            url=message_url,
        )


class ServerManager(commands.Cog):
    """
    Manages server onboarding, components-v2 welcome message, message link previews,
    automatic publishing of announcement messages, and auto-thread creation.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_link_pattern = re.compile(
            r"https?:\/\/(?:.*\.)?discord\.com\/channels\/(\d+)\/(\d+)\/(\d+)"
        )
        self.target_channel_id = TARGET_CHANNEL_ID
        self.reactions = AUTO_THREAD_REACTIONS

    async def _send_components_v2_message(
        self,
        channel_id: int,
        *,
        components: list,
        allowed_mentions: Optional[dict] = None,
        message_reference: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Send a Components V2 message by calling Discord's REST API directly.
        discord.py does not (currently) provide first-class builders for these component types.

        This intentionally does not send `content` or `embeds`, because IS_COMPONENTS_V2
        disables them.
        """
        payload: Dict[str, Any] = {
            "flags": MESSAGE_FLAG_IS_COMPONENTS_V2,
            "components": components,
        }

        if allowed_mentions is not None:
            payload["allowed_mentions"] = allowed_mentions

        if message_reference is not None:
            payload["message_reference"] = message_reference

        route = Route("POST", "/channels/{channel_id}/messages", channel_id=channel_id)
        return await self.bot.http.request(route, json=payload)

    def _welcome_components_v2(self) -> list:
        """Build the Components V2 payload (top-level components array) for the welcome message."""
        banner_url = (
            "https://images-ext-1.discordapp.net/external/"
            "AuEowaRQWXAAS80ilWitGttrcF_u1MetYh2ArvjkXuE/"
            "https/i.imgur.com/wlZ8Kw4.png?format=webp&quality=lossless&width=1307&height=642"
        )

        container = {
            "type": 17,  # Container
            "accent_color": 0xFFCD3F,
            "spoiler": False,
            "components": [
                {
                    "type": 12,  # Media Gallery
                    "items": [
                        {
                            "media": {"url": banner_url},
                            "description": None,
                            "spoiler": False,
                        }
                    ],
                },
                {
                    "type": 14,  # Separator
                    "spacing": 2,
                    "divider": True,
                },
                {
                    "type": 10,  # Text Display (markdown)
                    "content": (
                        "# Welcome to the Ente Community!\n"
                        "Explore our privacy-first services:\n"
                        "**Ente Photos**: Secure, private photo storage.\n"
                        "**Ente Auth**: Easy, privacy-focused authentication.\n\n"
                        "We’re glad to have you here! 🔐"
                    ),
                },
                {
                    "type": 14,  # Separator
                    "spacing": 1,
                    "divider": True,
                },
                {
                    "type": 10,  # Text Display (markdown)
                    "content": (
                        "## Community Guidelines\n"
                        "• **Respect Privacy**: No sharing of personal information.\n"
                        "• **Be Kind**: Keep interactions respectful and constructive.\n"
                        "• **Stay On Topic**: Use the right channels for your discussions.\n"
                        "• **No Spam**: Avoid posting irrelevant or repetitive content.\n"
                        "• **Follow Guidelines**: Abide by the community and platform rules."
                    ),
                },
            ],
        }

        # Action row with your buttons (still valid in Components V2 messages)
        action_row = {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 2,  # Secondary
                    "label": "Roles",
                    "custom_id": "Roles",
                    "emoji": {"id": str(ROLES_EMOJI_ID), "name": "roles"},
                },
                {
                    "type": 2,  # Button
                    "style": 2,  # Secondary
                    "label": "Channels",
                    "custom_id": "Channels",
                    "emoji": {"id": str(CHANNELS_EMOJI_ID), "name": "channels"},
                },
                {
                    "type": 2,  # Button
                    "style": 5,  # Link
                    "label": "Website",
                    "url": "https://ente.io",
                },
            ],
        }

        return [container, action_row]

    def _message_link_preview_components_v2(
        self,
        *,
        author_name: str,
        author_avatar_url: str,
        message_content: str,
        created_at_iso: str,
        jump_url: str,
        image_url: Optional[str],
    ) -> list:
        """
        Build a Components V2 preview for a referenced Discord message, plus a link button.
        """
        header = f"**{author_name}**\n-# {created_at_iso}\n"
        body = message_content if message_content else "*[No content]*"
        body = _safe_text(body, max_len=1800)

        container_components = [
            {
                "type": 10,  # Text Display
                "content": header + body,
            }
        ]

        if image_url:
            container_components.append(
                {
                    "type": 14,  # Separator
                    "spacing": 1,
                    "divider": True,
                }
            )
            container_components.append(
                {
                    "type": 12,  # Media Gallery
                    "items": [
                        {
                            "media": {"url": image_url},
                            "description": None,
                            "spoiler": False,
                        }
                    ],
                }
            )

        container = {
            "type": 17,  # Container
            "accent_color": 0xFFCD3F,
            "spoiler": False,
            "components": container_components[:10],  # container limit safety
        }

        action_row = {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,  # Link
                    "label": "Go to Message",
                    "url": jump_url,
                }
            ],
        }

        return [container, action_row]

    @app_commands.command(name="welcome")
    @app_commands.default_permissions(administrator=True)
    async def send_welcome(self, interaction: discord.Interaction):
        """Sends a Components V2 welcome message in the configured welcome channel."""
        try:
            await self._send_components_v2_message(
                WELCOME_CHANNEL_ID,
                components=self._welcome_components_v2(),
                allowed_mentions={"parse": []},
            )
            await interaction.response.send_message(
                "Welcome message sent.", ephemeral=True
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send Components V2 welcome: {e}")
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Failed to send welcome message.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Failed to send welcome message.", ephemeral=True
                )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handles button interactions for information messages."""
        if interaction.type != discord.InteractionType.component:
            return

        data = interaction.data or {}
        custom_id = data.get("custom_id")
        if custom_id == "Roles":
            roles_embed = discord.Embed(
                description=(
                    "# Community Roles\n"
                    "- <@&950276268593659925>: Ente Employee.\n"
                    "- <@&950275266045960254>: Keeping things smooth and respectful.\n"
                    "- <@&1452983028476547286>: Driving innovation with ideas and feedback. [How to get this role](https://support.discord.com/hc/en-us/articles/8063233404823-Connections-Linked-Roles-Community-Members#:~:text=Connection%20details.%20To%20do%20so%2C%20you%20can%20go%20to%20the%20Server%20menu%20dropdown%20%3E%20Linked%20Roles.)\n"
                    "- <@&1452990146197590163>: Starred the GitHub repo. [How to get this role](https://support.discord.com/hc/en-us/articles/8063233404823-Connections-Linked-Roles-Community-Members#:~:text=Connection%20details.%20To%20do%20so%2C%20you%20can%20go%20to%20the%20Server%20menu%20dropdown%20%3E%20Linked%20Roles.)\n"
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
                    emoji=discord.PartialEmoji(name="roles", id=ROLES_EMOJI_ID),
                    url="https://discord.com/channels/948937918347608085/customize-community",
                )
            )
            await interaction.response.send_message(
                embed=roles_embed, view=roles_view, ephemeral=True
            )

        elif custom_id == "Channels":
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


async def setup(bot: commands.Bot):
    """Adds the ServerManager cog to the bot."""
    await bot.add_cog(ServerManager(bot))
