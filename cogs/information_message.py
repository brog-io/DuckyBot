import discord
from discord.ext import commands
from discord.ui import Button, View
from discord import app_commands


class InformationMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="welcome")
    @app_commands.default_permissions(administrator=True)
    async def send_welcome(self, interaction: discord.Interaction):
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

            roles_button = Button(
                label="Roles", emoji=":roles:1316036079379288094", custom_id="Roles"
            )
            view.add_item(roles_button)

            channels_button = Button(
                label="Channels",
                emoji=":channels:1316036119481290802",
                custom_id="Channels",
            )
            view.add_item(channels_button)

            website_button = Button(label="Website", url="https://ente.io")
            view.add_item(website_button)

            # Send the embed and buttons to the specified channel
            await channel.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # Ensure the interaction is a button click
        if interaction.type == discord.InteractionType.component:
            # Check if the interaction is from a button and if the custom_id matches
            if interaction.data.get("custom_id") == "Roles":
                roles_embed = discord.Embed(
                    description=(
                        "# Community Roles\n"
                        "- <@&950276268593659925>: Ente Employee.\n"
                        "- <@&950275266045960254>: Keeping things smooth and respectful.\n"
                        "- <@&1307599568824700969>: Driving innovation with ideas and feedback.\n"
                        "- <@&1312804146428252235>: Deep knowledge of Ente’s products.\n"
                        "# Service Roles\n"
                        "- <@&1312807194487685231>: Focused on all things Ente Photos.\n"
                        "- <@&1099362028147183759>: Focused on all things Ente Auth.\n"
                        "# Notification Roles\n"
                        "- <@&1050340002028077106>: Get notified when a blog post is posted.\n"
                        "- <@&1214608287597723739>: Get notified when Ente posts on Mastodon."
                    ),
                    color=0xFFCD3F,
                )

                # Add a button to edit roles
                edit_button = Button(
                    label="Edit Roles",
                    emoji=":roles:1316036079379288094",
                    url="https://discord.com/channels/948937918347608085/customize-community",
                )
                roles_view = View()
                roles_view.add_item(edit_button)

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
                        "  - **<#973177352446173194>**: Updates about Ente’s presence on Mastodon.\n"
                        "  - **<#1128352882874400888>**: A place to see who joined the community.\n\n"
                        "- 🐣 **ENTE**\n"
                        "  - **<#948937919027105865>**: Discussions and support related to Ente Photos.\n"
                        "  - **<#1051153671985045514>**: Focused on Ente Auth and authentication-related queries.\n"
                        "  - **<#1215252276911018014>**: Discussions for those interested in hosting Ente services themselves.\n"
                        "  - **<#1121126215995113552>**: Share suggestions, report bugs, or provide input on Ente products and community.\n\n"
                        "- 💬 **COMMUNITY**\n"
                        "  - **<#953968250553765908>**: Casual conversations unrelated to Ente products.\n"
                        "  - **<#1025978742318833684>**: A place for sharing your favorite memories.\n"
                        "  - **<#948956465635397684>**: Share fun and lighthearted content."
                    ),
                    color=0xFFCD3F,
                )

                await interaction.response.send_message(
                    embed=channels_embed, ephemeral=True
                )


async def setup(bot):
    await bot.add_cog(InformationMessage(bot))
