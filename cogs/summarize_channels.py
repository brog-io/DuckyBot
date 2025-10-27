import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import os
import io
import re  # Used for pattern-based hyperlinking

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
    """Cog for summarizing Discord channel conversations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Configure which channels to monitor (add your channel IDs here)
        self.monitored_channels: List[int] = [
            948937919027105865,
            1051153671985045514,
            953968250553765908,
        ]

        # Initialize OpenAI client
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Section keyword, URL resolution rules for auto-linking numbered sections.
        # Order matters, first match wins. Patterns are case-insensitive substring checks.
        # Adjust or extend this map to cover more destinations as needed.
        self.SECTION_LINK_MAP: List[Tuple[str, str]] = [
            # Storage and files
            ("locker", "https://ente.io/drive"),
            ("drive", "https://ente.io/drive"),
            ("files", "https://ente.io/drive"),
            ("file backup", "https://help.ente.io/backups"),
            ("file backups", "https://help.ente.io/backups"),
            ("backup", "https://help.ente.io/backups"),
            ("backups", "https://help.ente.io/backups"),
            # Products
            ("photos", "https://ente.io/photos"),
            ("auth", "https://ente.io/auth"),
            ("2fa", "https://ente.io/auth"),
            ("mfa", "https://ente.io/auth"),
            # Docs and help
            ("faq", "https://help.ente.io"),
            ("help", "https://help.ente.io"),
            ("support", "https://help.ente.io"),
            # Privacy and security
            ("privacy", "https://ente.io"),
            ("security", "https://ente.io"),
        ]

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
        # Split summary into description-sized chunks
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
            # _safe_add_chunked_field may have created continuation embeds for field overflow
            if len(temp_embeds) == 1:
                first = temp_embeds[0]
            else:
                # first updated and then additional embeds were appended, collect them
                first = temp_embeds[0]
                # Ensure continuation embeds inherit the clickable URL if present
                for emb in temp_embeds[1:]:
                    if base_url and not emb.url:
                        emb.url = base_url
                embeds.extend(temp_embeds[1:])

        embeds.insert(0, first)

        # Remaining description chunks, each in its own embed
        for idx, chunk in enumerate(desc_chunks[1:], start=2):
            if len(embeds) >= MAX_EMBEDS_PER_MESSAGE:
                break  # stop at 10 embeds to satisfy Discord
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

    async def fetch_messages_from_channels(
        self, guild: discord.Guild, hours: int
    ) -> Dict[str, List[discord.Message]]:
        """Fetch messages from monitored channels within the specified timeframe."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        messages_by_channel: Dict[str, List[discord.Message]] = {}

        for channel_id in self.monitored_channels:
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Check bot permissions
            me = guild.me or guild.get_member(self.bot.user.id)  # fallback
            if not me:
                continue
            permissions = channel.permissions_for(me)
            if not permissions.read_message_history:
                continue

            messages: List[discord.Message] = []
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
        self, messages_by_channel: Dict[str, List[discord.Message]]
    ) -> Tuple[str, Dict[str, str]]:
        """
        Format messages into a readable text block for summarization.

        Returns:
            tuple: (formatted_text, dict mapping channel names to first message URLs)

        Also creates markdown-linked channel headers per section when possible.
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

            for msg in messages:
                timestamp = msg.created_at.strftime("%H:%M")
                author = msg.author.display_name
                content = msg.clean_content

                if msg.attachments:
                    content += f" [Attachments: {len(msg.attachments)}]"

                formatted.append(f"[{timestamp}] {author}: {content}")

        combined = "\n".join(formatted)

        # Upstream safety cap to avoid sending enormous prompts to the model
        if len(combined) > RAW_MESSAGES_TEXT_CAP:
            combined = (
                combined[:RAW_MESSAGES_TEXT_CAP]
                + "\n\n[Truncated input to fit processing limits]"
            )
        return combined, channel_links

    # ---------- Linkification helpers for model output ----------

    def _resolve_section_url(self, section_title: str) -> Optional[str]:
        """
        Given a section title like '4) Locker / Drive / file backups', return the best URL to link to.
        Matching is case-insensitive and uses the SECTION_LINK_MAP in order.
        If multiple keywords appear, the first matching rule wins.
        """
        lower = section_title.lower()
        # Remove the leading 'N) ' prefix if present to improve matching
        cleaned = re.sub(r"^\s*\d+\)\s*", "", lower)
        for needle, url in self.SECTION_LINK_MAP:
            if needle in cleaned:
                return url
        return None

    def _linkify_numbered_sections(self, text: str) -> str:
        """
        Convert numbered section lines like '4) Locker / Drive / file backups'
        into hyperlinks: '[4) Locker / Drive / file backups](https://ente.io/drive)'.

        This only links the full line when a URL can be resolved from keywords.
        It preserves the original text otherwise.
        """
        # Pattern matches the entire line starting with 'number)' and captures the section text.
        # We keep trailing text to the end of the line so wrapped lines are not half-linked.
        pattern = re.compile(r"^(\s*\d+\)\s+)(.+)$", flags=re.MULTILINE)

        def repl(match: re.Match) -> str:
            prefix = match.group(1)  # e.g., "4) "
            title = match.group(2)  # e.g., "Locker / Drive / file backups"
            url = self._resolve_section_url(title)
            if url:
                # Linkify the entire visible line content (including the numeric prefix)
                visible = f"{prefix}{title}".rstrip()
                return f"[{visible}]({url})"
            return match.group(0)

        return pattern.sub(repl, text)

    # Optional: also replace specific keywords anywhere in the text with links
    def _linkify_keywords_inline(self, text: str) -> str:
        """
        Additionally, replace common keywords inside the summary body with inline links.
        This is conservative to avoid over-linking. Edit as needed.
        """
        replacements = {
            "Ente Photos": "https://ente.io/photos",
            "Ente Auth": "https://ente.io/auth",
            "Locker": "https://ente.io/drive",
            "Drive": "https://ente.io/drive",
            "file backups": "https://help.ente.io/backups",
        }
        for word, url in replacements.items():
            # Replace standalone words or exact phrases, case-insensitive, without nesting links
            text = re.sub(
                rf"(?<!\])(?<!\w){re.escape(word)}(?!\w)(?!\()",
                f"[{word}]({url})",
                text,
                flags=re.IGNORECASE,
            )
        return text

    # ---------- Model call and length control ----------

    async def generate_summary(self, messages_text: str, hours: int) -> str:
        """
        Generate a summary using the model, asking for concision, then post-process:
        1) Convert numbered section headers into hyperlinks when resolvable.
        2) Optionally linkify common keywords inline.
        3) Apply a hard character cap for Discord safety.
        """
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
                # Use max_tokens to bound the model output size
                max_completion_tokens=2000,
            )
            text = response.choices[0].message.content or ""
        except Exception as e:
            return f"Error generating summary: {str(e)}"

        # First, turn numbered section headings into hyperlinks when resolvable
        text = self._linkify_numbered_sections(text)

        # Optionally, also linkify common keywords inline elsewhere in the text
        text = self._linkify_keywords_inline(text)

        # Apply a hard cap to guarantee we can split into embeds safely later
        if len(text) > MODEL_SUMMARY_HARD_CAP:
            text = (
                text[:MODEL_SUMMARY_HARD_CAP]
                + "\n\n[Truncated summary to fit message limits]"
            )
        return text

    # ---------- Slash command ----------

    @app_commands.command(
        name="summarise",
        description="Summarize conversations from monitored channels",
    )
    @app_commands.describe(hours="Number of hours to look back (default: 24)")
    async def summarise(
        self, interaction: discord.Interaction, hours: Optional[int] = 24
    ):
        """Summarize conversations from the last X hours."""
        # Validate input
        if hours is None:
            hours = 24
        if hours < 1 or hours > 168:  # Max 1 week
            await interaction.response.send_message(
                "Please specify between 1 and 168 hours (1 week).",
                ephemeral=True,
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
                    f"No messages found in monitored channels from the last {hours} hour(s)."
                )
                return

            # Count total messages
            total_messages = sum(len(msgs) for msgs in messages_by_channel.values())

            # Format messages and get links
            messages_text, channel_links = self.format_messages_for_summary(
                messages_by_channel
            )

            # Generate summary
            summary = await self.generate_summary(messages_text, hours)

            # Build header field values
            channels_value = (
                ", ".join([f"#{name}" for name in messages_by_channel.keys()]) or "None"
            )

            # Build links text and chunk later if needed
            links_text = (
                " • ".join([f"[#{name}]({url})" for name, url in channel_links.items()])
                or "None"
            )

            base_title = f"Server Summary, last {hours} hour(s)"
            color = discord.Color(0xFFCD3F)  # Ente yellow
            ts = datetime.utcnow()

            # Choose a primary link to attach to the embed title itself (first channel link if available)
            primary_link = next(iter(channel_links.values()), None)

            # Prepare header fields for the first embed
            header_fields = {
                "Channels Analyzed": channels_value,
                "Total Messages": str(total_messages),
                "Jump to Conversations": links_text,
            }

            # If summary begins with an error marker, short-circuit
            if summary.startswith("Error generating summary:"):
                await interaction.followup.send(summary)
                return

            # Construct embeds with smart chunking and a clickable title URL when available
            embeds = self._build_summary_embeds(
                base_title=base_title,
                summary_text=summary,
                color=color,
                timestamp_dt=ts,
                header_fields=header_fields,
                base_url=primary_link,
            )

            # If we somehow exceeded 10 embeds, attach the rest as a file
            attachments = []
            if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
                embeds = embeds[:MAX_EMBEDS_PER_MESSAGE]
                attachments.append(
                    discord.File(
                        io.BytesIO(summary.encode("utf-8")), filename="full_summary.txt"
                    )
                )

            # Always consider attaching the complete summary for convenience if it was long
            if len(summary) > EMBED_DESC_MAX:
                # Add attachment with the full text for easy offline reading
                # Avoid duplicate attachment if already added above
                if not any(att.filename == "full_summary.txt" for att in attachments):
                    attachments.append(
                        discord.File(
                            io.BytesIO(summary.encode("utf-8")),
                            filename="full_summary.txt",
                        )
                    )

            # Footer on the last embed
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
