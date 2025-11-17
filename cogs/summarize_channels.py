import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import os
import io

import openai


# ---------- Constants for Discord limits ----------
EMBED_DESC_MAX = 4096
FIELD_VALUE_MAX = 1024
MAX_FIELDS_PER_EMBED = 25
MAX_EMBEDS_PER_MESSAGE = 10
# Discord total-per-embed soft cap is ~6000, but the hard limits above are the primary constraints.

# Hard cap the model summary to something that comfortably fits inside multiple embeds if needed.
# This is a safety net. We also split across multiple embeds.
MODEL_SUMMARY_HARD_CAP = 32000  # characters

# Reasonable cutoff to stop collecting excessive messages for one call
RAW_MESSAGES_TEXT_CAP = 120_000  # characters, upstream cutoff


class Summarizer(commands.Cog):
    """Cog for summarizing Discord channel conversations.

    This version fetches messages from specific channels in the main server,
    even if you run the command from your dev server. It does so by resolving
    channels globally via bot.get_channel(channel_id) and checking permissions
    in the channel's own guild.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Optional, purely for nicer headers. If not set, we infer from the first channel we can resolve.
        self.main_guild_id: Optional[int] = None
        self.main_guild_id = 948937918347608085

        # Configure which channels to monitor (IDs from the MAIN server)
        # You can keep these in code or fetch from env, config file, database, etc.
        self.monitored_channels: List[int] = [
            948937919027105865,
            1051153671985045514,
            953968250553765908,
            1439920913096380537,
        ]

        # Initialize OpenAI client
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # ---------- Utility: safe chunking helpers ----------

    @staticmethod
    def _chunk_text(text: str, chunk_size: int) -> List[str]:
        """Split text into chunks no larger than chunk_size, preferring to break on line boundaries."""
        if len(text) <= chunk_size:
            return [text]

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            # Try to break at the last newline within the window
            window = text[start:end]
            if end < len(text):
                last_nl = window.rfind("\n")
                if last_nl != -1 and (start + last_nl) > start:
                    end = start + last_nl + 1
            chunks.append(text[start:end])
            start = end
        return chunks

    @staticmethod
    def _safe_add_chunked_field(
        embed: discord.Embed, name: str, value: str, inline: bool = False
    ) -> List[discord.Embed]:
        """
        Add a field to an embed, splitting into multiple fields if value exceeds FIELD_VALUE_MAX.
        Returns a list with the possibly modified embed, or additional embeds if field count overflows.
        """
        embeds_out = [embed]
        parts = Summarizer._chunk_text(value, FIELD_VALUE_MAX)

        for idx, part in enumerate(parts):
            field_name = name if idx == 0 else f"{name} (cont. {idx})"
            # If current embed is out of field slots, start a new embed to continue fields.
            if len(embeds_out[-1].fields) >= MAX_FIELDS_PER_EMBED:
                cont_embed = discord.Embed(
                    title=embeds_out[-1].title or "Continuation",
                    description="",
                    color=embeds_out[-1].colour,
                    timestamp=embeds_out[-1].timestamp,
                    url=embeds_out[-1].url,
                )
                embeds_out.append(cont_embed)
            embeds_out[-1].add_field(
                name=field_name, value=part or "\u200b", inline=inline
            )

        return embeds_out

    @staticmethod
    def _build_summary_embeds(
        base_title: str,
        summary_text: str,
        color: discord.Color,
        timestamp_dt: datetime,
        header_fields: Dict[str, str],
        base_url: Optional[str] = None,
    ) -> List[discord.Embed]:
        """
        Build one or more embeds for the summary. The first embed carries header fields.
        Long description is split across multiple embeds, respecting limits and caps.

        base_url, if provided, will make the embed title clickable.
        """
        desc_chunks = Summarizer._chunk_text(summary_text, EMBED_DESC_MAX)

        embeds: List[discord.Embed] = []

        # First embed with title, first chunk, and header fields
        first = discord.Embed(
            title=base_title,
            description=desc_chunks[0] if desc_chunks else "",
            color=color,
            timestamp=timestamp_dt,
            url=base_url if base_url else discord.Embed.Empty,
        )
        # Add header fields safely, these may themselves need chunking
        for fname, fvalue in header_fields.items():
            temp_embeds = Summarizer._safe_add_chunked_field(
                first, fname, fvalue, inline=False
            )
            if len(temp_embeds) == 1:
                first = temp_embeds[0]
            else:
                first = temp_embeds[0]
                for emb in temp_embeds[1:]:
                    if base_url and not emb.url:
                        emb.url = base_url
                embeds.extend(temp_embeds[1:])

        embeds.insert(0, first)

        # Remaining description chunks, each in its own embed
        for idx, chunk in enumerate(desc_chunks[1:], start=2):
            if len(embeds) >= MAX_EMBEDS_PER_MESSAGE:
                break
            emb = discord.Embed(
                title=f"{base_title} (continued {idx - 1})",
                description=chunk,
                color=color,
                timestamp=timestamp_dt,
                url=base_url if base_url else discord.Embed.Empty,
            )
            embeds.append(emb)

        return embeds

    # ---------- Message collection and formatting ----------

    async def fetch_messages_from_main_channels(
        self, hours: int
    ) -> Tuple[Dict[str, List[discord.Message]], Optional[discord.Guild]]:
        """Fetch messages from the configured main-server channels across guilds.

        This does not use interaction.guild. It resolves channels globally and
        checks read permissions in each channel's own guild.

        Returns:
            messages_by_channel: map from channel.name to chronological list of user messages
            source_guild: the first resolved guild that matches our channels, used for display
        """
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        messages_by_channel: Dict[str, List[discord.Message]] = {}

        source_guild: Optional[discord.Guild] = None

        for channel_id in self.monitored_channels:
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Track the guild we are pulling from for nicer headers
            if source_guild is None:
                source_guild = channel.guild

            # Get the bot's Member object inside that guild for permissions
            me_member = channel.guild.me or channel.guild.get_member(self.bot.user.id)
            if me_member is None:
                try:
                    me_member = await channel.guild.fetch_member(self.bot.user.id)
                except Exception:
                    me_member = None
            if not me_member:
                continue

            permissions = channel.permissions_for(me_member)
            if not permissions.read_message_history:
                # Skip channels where the bot cannot read history
                continue

            messages: List[discord.Message] = []
            # Pull messages oldest first for stable formatting
            async for message in channel.history(
                limit=None, after=cutoff_time, oldest_first=True
            ):
                if not message.author.bot:
                    messages.append(message)

            if messages:
                # Use the visible channel name for the header
                messages_by_channel[channel.name] = messages

        return messages_by_channel, source_guild

    def format_messages_for_summary(
        self, messages_by_channel: Dict[str, List[discord.Message]]
    ) -> Tuple[str, Dict[str, str]]:
        """
        Format messages into a readable text block for summarization.

        Returns:
            tuple: (formatted_text, dict mapping channel names to first message URLs)

        Each channel section starts with a markdown link header that jumps to the first message.
        """
        formatted: List[str] = []
        channel_links: Dict[str, str] = {}

        for channel_name, messages in messages_by_channel.items():
            first_jump_url = messages[0].jump_url if messages else None
            if messages:
                channel_links[channel_name] = first_jump_url

            # Channel header with a clickable link to the first message
            if first_jump_url:
                formatted.append(f"\n## [#{channel_name}]({first_jump_url})\n")
            else:
                formatted.append(f"\n## #{channel_name}\n")

            # Append messages in chronological order with compact formatting
            for msg in messages:
                timestamp = msg.created_at.strftime("%H:%M")
                author = msg.author.display_name
                content = msg.clean_content
                if msg.attachments:
                    content += f" [Attachments: {len(msg.attachments)}]"
                formatted.append(f"[{timestamp}] {author}: {content}")

        combined = "\n".join(formatted)

        if len(combined) > RAW_MESSAGES_TEXT_CAP:
            combined = (
                combined[:RAW_MESSAGES_TEXT_CAP]
                + "\n\n[Truncated input to fit processing limits]"
            )
        return combined, channel_links

    # ---------- Model call and length control ----------

    async def generate_summary(self, messages_text: str, hours: int) -> str:
        """Generate a summary using the model, then apply a hard character cap."""
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You summarize Discord conversations by topic with high signal density. "
                            "Be concise and structured, suitable for posting inside Discord embeds. "
                            "Always keep total output under 3,000 words, ideally under 2,000 words. "
                            "Organize by topic with short lists, avoid unnecessary prose. "
                            "Use Discord markdown sparingly and clearly:\n"
                            "- Use **bold** for key points\n"
                            "- Use # for title and ## for subtitles only when helpful\n"
                            "- Use `code` for feature or technical terms\n"
                            "- Use bullet lists (-) for items\n"
                            "- Prefer short sections over long paragraphs\n"
                            "Format topics like **1) Topic Name**, **2) Topic Name**, etc."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Summarize the following Discord conversations from the last {hours} hour(s). "
                            f"Keep it compact and scannable, avoid redundancy.\n\n{messages_text}"
                        ),
                    },
                ],
                max_completion_tokens=2000,
            )
            text = response.choices[0].message.content or ""
        except Exception as e:
            return f"Error generating summary: {str(e)}"

        if len(text) > MODEL_SUMMARY_HARD_CAP:
            text = (
                text[:MODEL_SUMMARY_HARD_CAP]
                + "\n\n[Truncated summary to fit message limits]"
            )
        return text

    # ---------- Slash command ----------

    @app_commands.command(
        name="summarise",
        description="Summarize conversations from monitored main-server channels",
    )
    @app_commands.describe(hours="Number of hours to look back (default: 24)")
    async def summarise(
        self, interaction: discord.Interaction, hours: Optional[int] = 24
    ):
        """Summarize conversations from the main server channels, regardless of where you run the command."""
        if hours is None:
            hours = 24
        if hours < 1 or hours > 168:
            await interaction.response.send_message(
                "Please specify between 1 and 168 hours (1 week).",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            # Fetch messages from main-server channels, not from interaction.guild
            messages_by_channel, source_guild = (
                await self.fetch_messages_from_main_channels(hours)
            )

            if not messages_by_channel:
                await interaction.followup.send(
                    f"No messages found in monitored channels from the last {hours} hour(s)."
                )
                return

            total_messages = sum(len(msgs) for msgs in messages_by_channel.values())

            # Prepare formatted input and per-channel jump links
            messages_text, channel_links = self.format_messages_for_summary(
                messages_by_channel
            )

            # Ask model for the summary
            summary = await self.generate_summary(messages_text, hours)

            channels_value = (
                ", ".join([f"#{name}" for name in messages_by_channel.keys()]) or "None"
            )
            links_text = (
                " • ".join([f"[#{name}]({url})" for name, url in channel_links.items()])
                or "None"
            )

            # Identify source server label for the header
            source_server_label = "Unknown"
            if self.main_guild_id:
                g = self.bot.get_guild(self.main_guild_id)
                if g:
                    source_server_label = g.name
            elif source_guild:
                source_server_label = source_guild.name

            base_title = (
                f"Server Summary, last {hours} hour(s) [Source: {source_server_label}]"
            )
            color = discord.Color(0xFFCD3F)
            ts = datetime.utcnow()

            primary_link = next(iter(channel_links.values()), None)

            header_fields = {
                "Channels Analyzed": channels_value,
                "Total Messages": str(total_messages),
                "Jump to Conversations": links_text,
            }

            if summary.startswith("Error generating summary:"):
                await interaction.followup.send(summary)
                return

            embeds = self._build_summary_embeds(
                base_title=base_title,
                summary_text=summary,
                color=color,
                timestamp_dt=ts,
                header_fields=header_fields,
                base_url=primary_link,
            )

            attachments = []
            if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
                embeds = embeds[:MAX_EMBEDS_PER_MESSAGE]
                attachments.append(
                    discord.File(
                        io.BytesIO(summary.encode("utf-8")), filename="full_summary.txt"
                    )
                )

            if len(summary) > EMBED_DESC_MAX:
                if not any(att.filename == "full_summary.txt" for att in attachments):
                    attachments.append(
                        discord.File(
                            io.BytesIO(summary.encode("utf-8")),
                            filename="full_summary.txt",
                        )
                    )

            if embeds:
                embeds[-1].set_footer(
                    text=f"Requested by {interaction.user.display_name}"
                )

            if attachments:
                await interaction.followup.send(embeds=embeds, files=attachments)
            else:
                await interaction.followup.send(embeds=embeds)

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to read message history in one or more channels."
            )
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}")


async def setup(bot: commands.Bot):
    """Load the cog."""
    await bot.add_cog(Summarizer(bot))
