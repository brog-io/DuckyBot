import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import List, Optional
import openai
import os


class Summarizer(commands.Cog):
    """Cog for summarizing Discord channel conversations."""

    def __init__(self, bot):
        self.bot = bot
        # Configure which channels to monitor (add your channel IDs here)
        self.monitored_channels = [
            948937919027105865,
            1051153671985045514,
            953968250553765908,
        ]

        # Initialize OpenAI (or use another AI service)
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def fetch_messages_from_channels(
        self, guild: discord.Guild, hours: int
    ) -> dict[str, List[discord.Message]]:
        """Fetch messages from monitored channels within the specified timeframe."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        messages_by_channel = {}

        for channel_id in self.monitored_channels:
            channel = guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Check bot permissions
            permissions = channel.permissions_for(guild.me)
            if not permissions.read_message_history:
                continue

            messages = []
            async for message in channel.history(
                limit=None, after=cutoff_time, oldest_first=True
            ):
                # Skip bot messages if desired
                if not message.author.bot:
                    messages.append(message)

            if messages:
                messages_by_channel[channel.name] = messages

        return messages_by_channel

    def format_messages_for_summary(
        self, messages_by_channel: dict[str, List[discord.Message]]
    ) -> tuple[str, dict[str, str]]:
        """Format messages into a readable text block for summarization.

        Returns:
            tuple: (formatted_text, dict mapping channel names to first message URLs)
        """
        formatted = []
        channel_links = {}

        for channel_name, messages in messages_by_channel.items():
            formatted.append(f"\n## Channel: #{channel_name}\n")

            # Store the first message link for this channel
            if messages:
                channel_links[channel_name] = messages[0].jump_url

            for msg in messages:
                timestamp = msg.created_at.strftime("%H:%M")
                author = msg.author.display_name
                content = msg.clean_content

                # Include attachment info
                if msg.attachments:
                    content += f" [Attachments: {len(msg.attachments)}]"

                formatted.append(f"[{timestamp}] {author}: {content}")

        return "\n".join(formatted), channel_links

    async def generate_summary(self, messages_text: str, hours: int) -> str:
        """Generate a summary using AI."""
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant that summarizes Discord conversations. "
                            "Provide a concise but comprehensive summary organized by topic. "
                            "Highlight key discussions, decisions, questions, and action items. "
                            "Use Discord markdown formatting to make the summary visually appealing:\n"
                            "- Use **bold** for emphasis on key points\n"
                            "- Use # for titles and ## for subtitles\n"
                            "- Use `code` for technical terms, features, or product names\n"
                            "- Use bullet points (- or •) for lists\n"
                            "- Use > for quotes if relevant\n"
                            "- Keep the summary well-structured and easy to scan\n"
                            "- Must be 4096 or fewer in length.\n"
                            "When organizing topics, format them as:\n"
                            "**[TOPIC_NUMBER]) Topic Name**\n"
                            "For example: **1) Storage & plans** or **2) Feature requests & roadmap**"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Please summarize the following Discord conversations from the last {hours} hour(s):\n\n"
                            f"{messages_text}"
                        ),
                    },
                ],
                max_completion_tokens=2000,
            )

            return response.choices[0].message.content

        except Exception as e:
            return f"Error generating summary: {str(e)}"

    @app_commands.command(
        name="summarise", description="Summarize conversations from monitored channels"
    )
    @app_commands.describe(hours="Number of hours to look back (default: 24)")
    async def summarise(
        self, interaction: discord.Interaction, hours: Optional[int] = 24
    ):
        """Summarize conversations from the last X hours."""
        # Validate input
        if hours < 1 or hours > 168:  # Max 1 week
            await interaction.response.send_message(
                "⚠️ Please specify between 1 and 168 hours (1 week).", ephemeral=True
            )
            return

        # Defer response as this might take a while
        await interaction.response.defer()

        try:
            # Fetch messages
            messages_by_channel = await self.fetch_messages_from_channels(
                interaction.guild, hours
            )

            if not messages_by_channel:
                await interaction.followup.send(
                    f"📭 No messages found in monitored channels from the last {hours} hour(s)."
                )
                return

            # Count total messages
            total_messages = sum(len(msgs) for msgs in messages_by_channel.values())

            # Format messages and get links
            messages_text, channel_links = self.format_messages_for_summary(
                messages_by_channel
            )

            # Check if there's too much content
            if len(messages_text) > 50000:
                await interaction.followup.send(
                    f"⚠️ Too many messages to summarize ({total_messages} messages). "
                    f"Try reducing the time window."
                )
                return

            # Generate summary
            summary = await self.generate_summary(messages_text, hours)

            # Create embed
            embed = discord.Embed(
                title=f"📊 Server Summary - Last {hours} Hour(s)",
                description=summary,
                color=discord.Color.blue(),
                timestamp=datetime.utcnow(),
            )

            embed.add_field(
                name="Channels Analyzed",
                value=", ".join([f"#{name}" for name in messages_by_channel.keys()]),
                inline=False,
            )

            embed.add_field(
                name="Total Messages", value=str(total_messages), inline=True
            )

            # Add clickable links to jump to conversations
            if channel_links:
                links_text = " • ".join(
                    [f"[#{name}]({url})" for name, url in channel_links.items()]
                )
                embed.add_field(
                    name="🔗 Jump to Conversations", value=links_text, inline=False
                )

            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            # Send the summary
            await interaction.followup.send(embed=embed)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to read message history in some channels."
            )
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")


async def setup(bot):
    """Load the cog."""
    await bot.add_cog(Summarizer(bot))
