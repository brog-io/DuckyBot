import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import json
from datetime import datetime, timezone, timedelta

LEADERBOARD_CHANNEL_ID = 953689741432340540


class MessageTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = "messages.db"
        self.cache_path = "leaderboard_cache.json"
        self.pinned_message_ids = {}
        bot.loop.create_task(self._init_everything())

    async def _init_everything(self):
        await self.bot.wait_until_ready()
        await self._init_db()
        await self._ensure_pinned_messages()
        if not self.update_leaderboards.is_running():
            self.update_leaderboards.start()

    async def _init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL
                )
                """
            )
            await db.commit()

    async def _ensure_pinned_messages(self):
        try:
            with open(self.cache_path, "r") as f:
                self.pinned_message_ids = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.pinned_message_ids = {}

        channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            print(f"Channel {LEADERBOARD_CHANNEL_ID} not found.")
            return

        needed = {
            "forever": "🏆 Forever Leaderboard",
            "monthly": "📅 Monthly Leaderboard",
            "weekly": "📆 Weekly Leaderboard",
        }

        for key, title in needed.items():
            if key not in self.pinned_message_ids:
                msg = await channel.send(f"{title}\nLoading...")
                await msg.pin()
                self.pinned_message_ids[key] = msg.id

        with open(self.cache_path, "w") as f:
            json.dump(self.pinned_message_ids, f)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (user_id, timestamp) VALUES (?, ?)",
                (message.author.id, now),
            )
            await db.commit()

    async def _fetch_leaderboard(self, mode: str):
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            if mode == "forever":
                query = """
                    SELECT user_id, COUNT(*) as count
                    FROM messages
                    GROUP BY user_id
                    ORDER BY count DESC
                    LIMIT 10
                """
                cursor = await db.execute(query)
            elif mode == "monthly":
                start_month = now.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                query = """
                    SELECT user_id, COUNT(*) as count
                    FROM messages
                    WHERE timestamp >= ?
                    GROUP BY user_id
                    ORDER BY count DESC
                    LIMIT 10
                """
                cursor = await db.execute(query, (start_month,))
            elif mode == "weekly":
                start_week = now - timedelta(days=now.weekday())
                start_week = start_week.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                query = """
                    SELECT user_id, COUNT(*) as count
                    FROM messages
                    WHERE timestamp >= ?
                    GROUP BY user_id
                    ORDER BY count DESC
                    LIMIT 10
                """
                cursor = await db.execute(query, (start_week,))
            else:
                return []
            return await cursor.fetchall()

    async def _build_mode_embed(self, mode: str):
        leaderboard = await self._fetch_leaderboard(mode)
        if not leaderboard:
            return discord.Embed(title="No data yet.", color=discord.Color.red())

        titles = {
            "forever": "🏆 All-Time Leaderboard",
            "monthly": "📅 Monthly Leaderboard",
            "weekly": "📆 Weekly Leaderboard",
        }

        embed = discord.Embed(
            title=titles.get(mode, "Leaderboard"),
            color=0xFFCD3F,
            timestamp=datetime.utcnow(),
        )

        try:
            top_user_id = leaderboard[0][0]
            top_user = await self.bot.fetch_user(top_user_id)
            embed.set_thumbnail(url=top_user.display_avatar.url)
        except (IndexError, discord.NotFound):
            pass

        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, count) in enumerate(leaderboard, start=1):
            try:
                user = await self.bot.fetch_user(user_id)
                medal = medals[i - 1] if i <= 3 else f"#{i}"
                embed.add_field(
                    name=f"{medal} {user.display_name}",
                    value=f"{count} messages",
                    inline=False,
                )
            except discord.NotFound:
                continue

        embed.set_footer(text="Leaderboard based on real message counts.")
        return embed

    @app_commands.command(
        name="leaderboard", description="Show the top users by messages"
    )
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        embed = await self._build_mode_embed("forever")
        view = self.LeaderboardView(self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    class LeaderboardView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.select(
            placeholder="Select timeframe",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="All-Time", value="forever", emoji="🏆"),
                discord.SelectOption(label="Monthly", value="monthly", emoji="📅"),
                discord.SelectOption(label="Weekly", value="weekly", emoji="📆"),
            ],
            custom_id="leaderboard_timeframe_select",
        )
        async def select_timeframe(
            self, interaction: discord.Interaction, select: discord.ui.Select
        ):
            mode = select.values[0]
            embed = await self.cog._build_mode_embed(mode)
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(
            label="🔄 Refresh",
            style=discord.ButtonStyle.primary,
            custom_id="refresh_leaderboard_button",
        )
        async def refresh_button(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            mode = None
            title = interaction.message.embeds[0].title
            if "All-Time" in title:
                mode = "forever"
            elif "Monthly" in title:
                mode = "monthly"
            elif "Weekly" in title:
                mode = "weekly"
            embed = await self.cog._build_mode_embed(mode or "forever")
            await interaction.response.edit_message(embed=embed, view=self)

    @app_commands.command(
        name="rank", description="Show your rank and message count in the leaderboards."
    )
    @app_commands.describe(user="The user to check the rank for (optional)")
    async def rank(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
    ):
        user = user or interaction.user

        # Helper for ranking
        async def get_rank_and_count(mode: str):
            now = datetime.now(timezone.utc)
            async with aiosqlite.connect(self.db_path) as db:
                if mode == "forever":
                    query = """
                        SELECT user_id, COUNT(*) as count
                        FROM messages
                        GROUP BY user_id
                        ORDER BY count DESC
                    """
                    cursor = await db.execute(query)
                elif mode == "monthly":
                    start_month = now.replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    )
                    query = """
                        SELECT user_id, COUNT(*) as count
                        FROM messages
                        WHERE timestamp >= ?
                        GROUP BY user_id
                        ORDER BY count DESC
                    """
                    cursor = await db.execute(query, (start_month,))
                elif mode == "weekly":
                    start_week = now - timedelta(days=now.weekday())
                    start_week = start_week.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    query = """
                        SELECT user_id, COUNT(*) as count
                        FROM messages
                        WHERE timestamp >= ?
                        GROUP BY user_id
                        ORDER BY count DESC
                    """
                    cursor = await db.execute(query, (start_week,))
                else:
                    return None, 0

                leaderboard = await cursor.fetchall()
                for i, (user_id, count) in enumerate(leaderboard, start=1):
                    if user_id == user.id:
                        return i, count
                return None, 0

        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(
            title=f"📊 Rank for {user.display_name}",
            color=0xFFCD3F,
            timestamp=datetime.utcnow(),
        )

        # All leaderboard types
        modes = [
            ("forever", "🏆 All-Time"),
            ("monthly", "📅 Monthly"),
            ("weekly", "📆 Weekly"),
        ]

        for mode, mode_name in modes:
            rank, count = await get_rank_and_count(mode)
            if rank:
                embed.add_field(
                    name=mode_name,
                    value=f"Rank: **#{rank}**\nMessages: **{count}**",
                    inline=False,
                )
            else:
                embed.add_field(
                    name=mode_name, value="Not ranked (no messages).", inline=False
                )

        embed.set_footer(text="Rank is recalculated every 10 minutes.")

        try:
            embed.set_thumbnail(url=user.display_avatar.url)
        except Exception:
            pass

        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(minutes=10)
    async def update_leaderboards(self):
        try:
            await self.bot.wait_until_ready()
            channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
            if not channel:
                print(f"Leaderboard channel {LEADERBOARD_CHANNEL_ID} not found.")
                return
            for mode, msg_id in self.pinned_message_ids.items():
                try:
                    msg = await channel.fetch_message(msg_id)
                    embed = await self._build_mode_embed(mode)
                    await msg.edit(content=None, embed=embed)
                except discord.NotFound:
                    print(f"Leaderboard message ID {msg_id} not found. Skipping.")
                except Exception as e:
                    import traceback

                    print(f"Exception in update_leaderboards (mode {mode}): {e}")
                    traceback.print_exc()
        except Exception as e:
            import traceback

            print(f"Fatal exception in update_leaderboards: {e}")
            traceback.print_exc()

    @update_leaderboards.error
    async def update_leaderboards_error(self, error):
        print(f"Leaderboard update loop error: {error}")

    def cog_unload(self):
        if self.update_leaderboards.is_running():
            self.update_leaderboards.cancel()


async def setup(bot: commands.Bot):
    cog = MessageTracker(bot)
    await bot.add_cog(cog)
    bot.add_view(cog.LeaderboardView(cog))  # Register persistent view
