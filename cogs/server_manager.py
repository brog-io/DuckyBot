import discord
from discord.ext import commands
from discord.ui import Button, View, Select
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

MESSAGE_FLAG_IS_COMPONENTS_V2 = 1 << 15

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

ROLES_EMOJI_ID = 1439927556202823712
CHANNELS_EMOJI_ID = 1439927871698239578

# --------- Self-assignable roles ---------
SELF_ASSIGNABLE_ROLES = {
    "Ente Photos": 1312807194487685231,
    "Ente Auth": 1099362028147183759,
    "Ente Locker": 1439921934409400351,
    "Blog Posts": 1050340002028077106,
    "Mastodon": 1214608287597723739,
    "Bluesky": 1400571735904092230,
    "Reddit": 1400571795848958052,
    "GitHub Discussions": 1403399186023579688,
}


def is_image_attachment(attachment: discord.Attachment) -> bool:
    if getattr(attachment, "content_type", None):
        return attachment.content_type.startswith("image/")
    return attachment.url.lower().endswith(IMAGE_EXTENSIONS)


def _safe_text(s: Optional[str], max_len: int = 1800) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


class MessageLinkButton(Button):
    def __init__(self, message_url: str):
        super().__init__(
            style=discord.ButtonStyle.link,
            label="Open Message",
            url=message_url,
        )


class RoleSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=str(role_id))
            for name, role_id in SELF_ASSIGNABLE_ROLES.items()
        ]

        super().__init__(
            placeholder="Select the roles you want",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id="self_roles_select",
        )

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "This can only be used inside the server.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "Guild not found.",
                ephemeral=True,
            )
            return

        selected_ids = {int(v) for v in self.values}
        managed_ids = set(SELF_ASSIGNABLE_ROLES.values())

        roles_to_add = []
        roles_to_remove = []

        for role_id in managed_ids:
            role = guild.get_role(role_id)
            if not role:
                continue

            if role_id in selected_ids and role not in member.roles:
                roles_to_add.append(role)
            elif role_id not in selected_ids and role in member.roles:
                roles_to_remove.append(role)

        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Self-assigned roles")

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Self-unassigned roles")

        await interaction.response.send_message(
            "Your roles have been updated.",
            ephemeral=True,
        )


class RolesView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoleSelect())


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
        banner_url = (
            "https://images-ext-1.discordapp.net/external/"
            "AuEowaRQWXAAS80ilWitGttrcF_u1MetYh2ArvjkXuE/"
            "https/i.imgur.com/wlZ8Kw4.png?format=webp&quality=lossless&width=1307&height=642"
        )

        container = {
            "type": 17,
            "accent_color": 0xFFCD3F,
            "spoiler": False,
            "components": [
                {
                    "type": 12,
                    "items": [{"media": {"url": banner_url}}],
                },
                {"type": 14, "spacing": 2, "divider": True},
                {
                    "type": 10,
                    "content": (
                        "# Welcome to the Ente Community!\n"
                        "Explore our privacy-first services:\n"
                        "**Ente Photos**: Secure, private photo storage.\n"
                        "**Ente Auth**: Easy, privacy-focused authentication.\n\n"
                        "We’re glad to have you here! 🔐"
                    ),
                },
                {"type": 14, "spacing": 1, "divider": True},
                {
                    "type": 10,
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

        action_row = {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "Roles",
                    "custom_id": "Roles",
                    "emoji": {"id": str(ROLES_EMOJI_ID), "name": "roles"},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Channels",
                    "custom_id": "Channels",
                    "emoji": {"id": str(CHANNELS_EMOJI_ID), "name": "channels"},
                },
                {
                    "type": 2,
                    "style": 5,
                    "label": "Website",
                    "url": "https://ente.io",
                },
            ],
        }

        return [container, action_row]

    @app_commands.command(name="welcome")
    @app_commands.default_permissions(administrator=True)
    async def send_welcome(self, interaction: discord.Interaction):
        await self._send_components_v2_message(
            WELCOME_CHANNEL_ID,
            components=self._welcome_components_v2(),
            allowed_mentions={"parse": []},
        )
        await interaction.response.send_message(
            "Welcome message sent.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = (interaction.data or {}).get("custom_id")

        if custom_id == "Roles":
            roles_embed = discord.Embed(
                description=(
                    "# Community Roles\n"
                    "- <@&950276268593659925>: Ente Employee.\n"
                    "- <@&950275266045960254>: Keeping things smooth and respectful.\n"
                    "- <@&1452983028476547286>: Driving innovation with ideas and feedback.\n"
                    "- <@&1452990146197590163>: Starred the GitHub repo.\n\n"
                    "# Service Roles\n"
                    "- <@&1312807194487685231>: Focused on all things Ente Photos.\n"
                    "- <@&1099362028147183759>: Focused on all things Ente Auth.\n"
                    "- <@&1439921934409400351>: Focused on all things Ente Locker.\n\n"
                    "# Notification Roles\n"
                    "- <@&1050340002028077106>: Blog post notifications.\n"
                    "- <@&1214608287597723739>: Mastodon updates.\n"
                    "- <@&1400571735904092230>: Bluesky updates.\n"
                    "- <@&1400571795848958052>: Reddit updates.\n"
                    "- <@&1403399186023579688>: GitHub discussion updates.\n\n"
                    "Use the dropdown below to choose which roles you want."
                ),
                color=0xFFCD3F,
            )

            await interaction.response.send_message(
                embed=roles_embed,
                view=RolesView(),
                ephemeral=True,
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
                    "  - **<#1335538661185421386>**: Our wall of love, where all our nice reviews are shown.\n"
                    "  - **<#948956465635397684>**: Share fun and lighthearted content."
                ),
                color=0xFFCD3F,
            )

            await interaction.response.send_message(
                embed=channels_embed,
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.is_news()
        ):
            try:
                await message.publish()
            except Exception:
                pass

        for match in self.message_link_pattern.finditer(message.content):
            try:
                channel = self.bot.get_channel(int(match.group(2)))
                if not channel:
                    continue

                ref = await channel.fetch_message(int(match.group(3)))

                embed = discord.Embed(
                    description=ref.content or "*[No content]*",
                    timestamp=ref.created_at,
                    color=0xFFCD3F,
                )
                embed.set_author(
                    name=ref.author.display_name,
                    icon_url=ref.author.display_avatar.url,
                )

                image = next(
                    (a for a in ref.attachments if is_image_attachment(a)),
                    None,
                )
                if image:
                    embed.set_image(url=image.url)

                view = View()
                view.add_item(MessageLinkButton(match.group(0)))

                await message.reply(embed=embed, view=view, mention_author=False)
            except Exception as e:
                logger.error(f"Error processing message link: {e}")

        if message.channel.id == self.target_channel_id:
            if any(is_image_attachment(a) for a in message.attachments):
                for r in self.reactions:
                    await message.add_reaction(r)

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
            else:
                try:
                    await message.delete()
                except Exception:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerManager(bot))
