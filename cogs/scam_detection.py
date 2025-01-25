import discord
from discord.ext import commands
from discord.ui import Button, View
from mistralai import Mistral
import json
import os
from dotenv import load_dotenv
import logging

load_dotenv()
mistral_api_key = os.getenv("MISTRAL_API_KEY")
discord_token = os.getenv("DISCORD_BOT_TOKEN")

config_file = "config.json"

with open(config_file) as f:
    config = json.load(f)

log_channel_id = config["log_channel_id"]
whitelisted_role_ids = config["role_whitelist"]

mistral_client = Mistral(api_key=mistral_api_key)

logger = logging.getLogger(__name__)


async def check_scam_with_mistral(message: str) -> tuple[bool, dict]:
    try:
        response = mistral_client.classifiers.moderate(
            model="mistral-moderation-latest",
            inputs=[message],
        )
        moderation_result = response.results[0]

        scam_categories = ["fraud", "financial"]
        category_scores = {
            category: moderation_result.category_scores.get(category, 0)
            for category in scam_categories
        }

        is_scam = any(score > 0.70 for score in category_scores.values())
        return is_scam, category_scores
    except Exception as e:
        logger.error(f"Error with Mistral Moderation API: {e}")
        return False, {}


class ModerationButtons(View):
    def __init__(self, message, author):
        super().__init__(timeout=None)
        self.message = message
        self.author = author
        self.is_deleted = False

        # Add "View Message" button
        view_button = Button(
            label="View",
            style=discord.ButtonStyle.link,
            url=message.jump_url,  # Assign the jump URL directly
        )
        self.add_item(view_button)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: Button):
        if self.is_deleted:
            await interaction.response.send_message(
                "This message has already been deleted.", ephemeral=True
            )
            return

        if interaction.user.guild_permissions.manage_messages:
            await self.message.delete()
            self.is_deleted = True
            await interaction.response.send_message(
                "Message deleted successfully.", ephemeral=True
            )
            button.disabled = True
            await interaction.message.edit(view=self)
        else:
            await interaction.response.send_message(
                "You do not have permission to delete messages.", ephemeral=True
            )

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary)
    async def kick_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.kick_members:
            await self.author.kick(reason="Scam message detected by PhishHook.")
            await interaction.response.send_message(
                f"{self.author.name} has been kicked.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You do not have permission to kick members.", ephemeral=True
            )

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: Button):
        if interaction.user.guild_permissions.ban_members:
            await self.author.ban(reason="Scam message detected by PhishHook.")
            await interaction.response.send_message(
                f"{self.author.name} has been banned.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You do not have permission to ban members.", ephemeral=True
            )


class ScamDetection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = commands.CooldownMapping.from_cooldown(
            3, 60, commands.BucketType.user
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user or message.author.bot:
            return

        if not message.guild:
            return

        # Add cooldown check
        bucket = self.cooldowns.get_bucket(message)
        if bucket.update_rate_limit():
            return

        # Check whitelisted roles
        author_roles = [role.id for role in message.author.roles]
        if any(role_id in whitelisted_role_ids for role_id in author_roles):
            return

        is_scam, scores = await check_scam_with_mistral(message.content)

        if is_scam:
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="🚨 Potential Scam Detected",
                    description=f"Message from {message.author.mention} in {message.channel.mention}:\n\n{message.content}",
                    color=discord.Color.red(),
                )
                embed.add_field(
                    name="Scam Confidence Scores",
                    value="\n".join(f"• {k}: {v:.1%}" for k, v in scores.items()),
                )
                embed.set_footer(
                    text=f"User ID: {message.author.id} | Message ID: {message.id}"
                )

                view = ModerationButtons(message, message.author)
                await log_channel.send(embed=embed, view=view)
            else:
                logger.error("Log channel not found. Check the log_channel_id.")

    @commands.command()
    async def start(self, ctx):
        await ctx.send("PhishHook Scam Detection Bot is running!")


def setup(bot):
    bot.add_cog(ScamDetection(bot))
