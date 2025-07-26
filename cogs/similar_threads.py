import discord
from discord.ext import commands, tasks
import json
import asyncio
import openai
import numpy as np
import os
import logging
from datetime import datetime, timedelta
import time
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ForumSimilarityBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.forum_channel_id = 1383504546361380995
        self.similarity_threshold = 0.55
        self.solved_posts_file = "solved_posts_index.json"
        self.solved_tag_name = "Solved"

        # Optimization settings
        self.embedding_model = (
            "text-embedding-3-small"  # 1536 dimensions instead of 3072
        )
        self.embedding_version = "v1"  # For versioning embeddings
        self.batch_size = 100  # Batch embedding requests
        self.max_retries = 3
        self.cache_duration_days = 60  # Refresh old embeddings

        # Performance tracking
        self.stats = {
            "embeddings_generated": 0,
            "cache_hits": 0,
            "similarity_checks": 0,
            "matches_found": 0,
        }

        # Initialize cache before loading data
        self.embedding_cache = {}  # In-memory cache for frequently accessed embeddings

        # Load existing data
        self.solved_posts = self.load_solved_posts()

        # Start background tasks
        self.check_new_solved_posts.start()
        self.refresh_old_embeddings.start()

    def load_solved_posts(self):
        try:
            with open(self.solved_posts_file, "r") as f:
                content = f.read().strip()
                if not content:  # Empty file
                    return {}
                data = json.loads(content)
                # Load embeddings into memory for faster access
                self.preload_embeddings(data)
                return data
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load solved posts file: {e}")
            logger.info("Starting with empty index...")
            return {}

    def preload_embeddings(self, posts_data):
        """Load frequently used embeddings into memory"""
        recent_cutoff = datetime.now() - timedelta(days=7)
        for post_id, post_data in posts_data.items():
            if (
                post_data.get("embedding")
                and datetime.fromisoformat(post_data.get("indexed_at", "2020-01-01"))
                > recent_cutoff
            ):
                self.embedding_cache[post_id] = np.array(post_data["embedding"])

    def save_solved_posts(self):
        # Convert numpy arrays back to lists for JSON serialization
        serializable_data = {}
        for post_id, post_data in self.solved_posts.items():
            serializable_data[post_id] = post_data.copy()
            if "embedding" in serializable_data[post_id] and isinstance(
                serializable_data[post_id]["embedding"], np.ndarray
            ):
                serializable_data[post_id]["embedding"] = serializable_data[post_id][
                    "embedding"
                ].tolist()

        # Use compact JSON for embeddings (no pretty printing)
        with open(self.solved_posts_file, "w") as f:
            json.dump(serializable_data, f, separators=(",", ":"))

    async def generate_embeddings_batch(
        self, texts: List[str]
    ) -> List[Optional[List[float]]]:
        """Generate embeddings in batches for efficiency"""
        if not texts:
            return []

        all_embeddings = []

        # Process in batches of 100 (OpenAI limit is 2048, but 100 is safer)
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]

            for attempt in range(self.max_retries):
                try:
                    response = await asyncio.to_thread(
                        self.openai_client.embeddings.create,
                        model=self.embedding_model,
                        input=batch,
                    )

                    batch_embeddings = [data.embedding for data in response.data]
                    all_embeddings.extend(batch_embeddings)
                    self.stats["embeddings_generated"] += len(batch_embeddings)
                    break

                except Exception as e:
                    if attempt == self.max_retries - 1:
                        print(
                            f"Failed to generate embeddings after {self.max_retries} attempts: {e}"
                        )
                        all_embeddings.extend([None] * len(batch))
                    else:
                        await asyncio.sleep(2**attempt)  # Exponential backoff

        return all_embeddings

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate single embedding with caching"""
        embeddings = await self.generate_embeddings_batch([text])
        return embeddings[0] if embeddings else None

    def get_embedding_from_cache(self, post_id: str) -> Optional[np.ndarray]:
        """Get embedding from memory cache or load from data"""
        if post_id in self.embedding_cache:
            self.stats["cache_hits"] += 1
            return self.embedding_cache[post_id]

        post_data = self.solved_posts.get(post_id)
        if post_data and "embedding" in post_data:
            embedding = np.array(post_data["embedding"])
            self.embedding_cache[post_id] = embedding
            return embedding

        return None

    def cosine_similarity_optimized(self, a: np.ndarray, b: np.ndarray) -> float:
        """Optimized cosine similarity calculation"""
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

    @tasks.loop(minutes=30)
    async def check_new_solved_posts(self):
        """Check for newly solved posts with batch processing"""
        await self.bot.wait_until_ready()

        forum_channel = self.bot.get_channel(self.forum_channel_id)
        if not forum_channel:
            return

        new_threads = []

        try:
            # Collect new solved threads
            for thread in forum_channel.threads:
                if (
                    self.is_thread_solved(thread)
                    and str(thread.id) not in self.solved_posts
                ):
                    new_threads.append(thread)

            # Check archived threads (limited batch)
            count = 0
            async for thread in forum_channel.archived_threads(
                limit=200
            ):  # Increased from 100
                if count >= 100:  # Increased from 50
                    break
                if (
                    self.is_thread_solved(thread)
                    and str(thread.id) not in self.solved_posts
                ):
                    new_threads.append(thread)
                    count += 1

            if new_threads:
                await self.batch_add_threads_to_index(new_threads)
                print(
                    f"✅ Added {len(new_threads)} new solved posts. Total: {len(self.solved_posts)}"
                )

        except Exception as e:
            print(f"Error checking for new solved posts: {e}")

    @tasks.loop(hours=24)
    async def refresh_old_embeddings(self):
        """Refresh embeddings older than cache duration"""
        await self.bot.wait_until_ready()

        cutoff_date = datetime.now() - timedelta(days=self.cache_duration_days)
        old_posts = []

        for post_id, post_data in self.solved_posts.items():
            indexed_date = datetime.fromisoformat(
                post_data.get("indexed_at", "2020-01-01")
            )
            if (
                indexed_date < cutoff_date
                and post_data.get("embedding_version") != self.embedding_version
            ):
                old_posts.append((post_id, post_data))

        if old_posts:
            logger.info(f"Refreshing {len(old_posts)} old embeddings...")
            await self.batch_update_embeddings(old_posts)

    async def batch_add_threads_to_index(self, threads: List[discord.Thread]):
        """Add multiple threads to index with batch embedding generation"""
        if not threads:
            return

        # Collect all texts for batch processing
        texts = []
        thread_data = []

        for thread in threads:
            try:
                starter_message = await thread.fetch_message(thread.id)
                combined_text = f"Title: {thread.name or 'Untitled'}\nBody: {starter_message.content or ''}"

                texts.append(combined_text)
                thread_data.append(
                    {
                        "thread": thread,
                        "starter_message": starter_message,
                        "text": combined_text,
                    }
                )
            except Exception as e:
                logger.error(f"Error preparing thread {thread.id}: {e}")
                continue

        if not texts:
            return

        # Generate embeddings in batch
        embeddings = await self.generate_embeddings_batch(texts)

        # Store results
        for i, data in enumerate(thread_data):
            if i < len(embeddings) and embeddings[i]:
                thread = data["thread"]
                starter_message = data["starter_message"]

                # Store in index
                self.solved_posts[str(thread.id)] = {
                    "title": thread.name or "Untitled",
                    "body": starter_message.content or "",
                    "author_id": starter_message.author.id,
                    "created_at": thread.created_at.isoformat(),
                    "indexed_at": datetime.now().isoformat(),
                    "url": thread.jump_url,
                    "embedding": embeddings[i],
                    "embedding_version": self.embedding_version,
                }

                # Add to memory cache
                self.embedding_cache[str(thread.id)] = np.array(embeddings[i])

        self.save_solved_posts()

    async def batch_update_embeddings(self, old_posts: List[Tuple[str, Dict]]):
        """Update embeddings for old posts in batches"""
        texts = []
        post_ids = []

        for post_id, post_data in old_posts:
            combined_text = f"Title: {post_data['title']}\nBody: {post_data['body']}"
            texts.append(combined_text)
            post_ids.append(post_id)

        embeddings = await self.generate_embeddings_batch(texts)

        for i, post_id in enumerate(post_ids):
            if i < len(embeddings) and embeddings[i]:
                self.solved_posts[post_id]["embedding"] = embeddings[i]
                self.solved_posts[post_id]["embedding_version"] = self.embedding_version
                self.solved_posts[post_id]["refreshed_at"] = datetime.now().isoformat()

                # Update cache
                self.embedding_cache[post_id] = np.array(embeddings[i])

        self.save_solved_posts()

    def is_thread_solved(self, thread):
        """Check if a thread has the solved tag"""
        if hasattr(thread, "applied_tags"):
            for tag in thread.applied_tags:
                if tag.name == self.solved_tag_name:
                    return True
        return False

    async def add_thread_to_index(self, thread):
        """Add single thread to index (fallback for immediate updates)"""
        await self.batch_add_threads_to_index([thread])

    async def find_similar_solved_posts_optimized(
        self, title: str, body: str
    ) -> List[Dict]:
        """Optimized similarity search with smart filtering"""
        print(
            f"🔍 Starting similarity search with {len(self.solved_posts)} solved posts"
        )

        if not self.solved_posts:
            print("❌ No solved posts in index")
            return []

        start_time = time.time()

        # Generate embedding for new post
        new_text = f"Title: {title}\nBody: {body}"
        print(f"📝 Generating embedding for: '{title[:50]}...'")
        new_embedding = await self.generate_embedding(new_text)
        if not new_embedding:
            print("❌ Failed to generate embedding")
            return []

        new_embedding_np = np.array(new_embedding)

        # Calculate similarities with vectorized operations where possible
        similarities = []
        embedding_batch = []
        post_ids_batch = []
        post_data_batch = []

        # Collect embeddings for vectorized comparison
        for post_id, post_data in self.solved_posts.items():
            embedding = self.get_embedding_from_cache(post_id)
            if embedding is not None:
                embedding_batch.append(embedding)
                post_ids_batch.append(post_id)
                post_data_batch.append(post_data)

        print(f"🎯 Comparing against {len(embedding_batch)} posts with embeddings")

        # Vectorized similarity calculation
        if embedding_batch:
            embedding_matrix = np.vstack(embedding_batch)
            similarities_batch = np.dot(embedding_matrix, new_embedding_np) / (
                np.linalg.norm(embedding_matrix, axis=1)
                * np.linalg.norm(new_embedding_np)
            )

            # Filter by threshold and prepare results
            above_threshold = 0
            for i, similarity in enumerate(similarities_batch):
                if similarity > self.similarity_threshold:
                    above_threshold += 1
                    post_data = post_data_batch[i]
                    similarities.append(
                        {
                            "id": int(post_ids_batch[i]),
                            "similarity": float(similarity),
                            "title": post_data["title"],
                            "body": post_data["body"][:200],
                            "url": post_data["url"],
                        }
                    )

            print(
                f"📊 {above_threshold} posts above threshold {self.similarity_threshold}"
            )

        # Sort by similarity and get top candidates
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        top_candidates = similarities[:8]

        # Update stats
        self.stats["similarity_checks"] += 1
        if top_candidates:
            self.stats["matches_found"] += 1

        processing_time = time.time() - start_time
        print(f"⚡ Similarity search: {processing_time:.3f}s")

        if not top_candidates:
            return []

        # Use AI for final ranking
        print("🤖 Running AI ranking...")
        result = await self.ai_rank_candidates_optimized(title, body, top_candidates)
        print(f"✅ AI returned {len(result)} final matches")
        return result

    async def ai_rank_candidates_optimized(
        self, title: str, body: str, candidates: List[Dict]
    ) -> List[Dict]:
        """Optimized AI ranking with better prompting"""
        if not candidates:
            return []

        # Use only top 5 for AI ranking to save tokens
        top_5 = candidates[:5]

        prompt = f"""New post: "{title}" - {body[:200]}

Top similar solved posts:
{json.dumps([{"id": c["id"], "title": c["title"], "body": c["body"][:150]} for c in top_5], indent=1)}

Return JSON array of posts that would help solve the new post:
[{{"id": 123, "similarity": 0.89, "reason": "Brief why it helps"}}]

Only include truly helpful posts (similarity > 0.82). Return [] if none help."""

        try:
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Expert at matching solved posts to new questions. Always return valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=400,
            )

            result = response.choices[0].message.content.strip()

            # Extract JSON
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]

            return json.loads(result)

        except Exception as e:
            logger.error(f"Error in AI ranking: {e}")
            # Fallback to top embedding matches
            return candidates[:3]

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        """Handle new forum posts with optimized processing"""
        print(f"🔍 New thread detected: {thread.name} in channel {thread.parent.id}")

        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != self.forum_channel_id
        ):
            print(f"❌ Wrong channel or not forum. Expected: {self.forum_channel_id}")
            return

        await asyncio.sleep(2)

        try:
            starter_message = await thread.fetch_message(thread.id)

            if not thread.name and not starter_message.content:
                print("❌ No title or content to analyze")
                return

            print(
                f"📝 Analyzing post: '{thread.name}' with {len(self.solved_posts)} solved posts"
            )

            # Find similar solved posts using optimized search
            similar_posts = await self.find_similar_solved_posts_optimized(
                thread.name or "Untitled", starter_message.content or ""
            )

            print(f"🎯 Found {len(similar_posts)} similar posts")

            if similar_posts:
                await self.send_similarity_notification(thread, similar_posts)
                print("✅ Sent similarity notification")
            else:
                print("ℹ️ No similar posts found above threshold")

        except Exception as e:
            print(f"Error processing new thread: {e}")

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        """Detect solved threads with immediate indexing"""
        if (
            not isinstance(after.parent, discord.ForumChannel)
            or after.parent.id != self.forum_channel_id
        ):
            return

        # Check if thread was just marked as solved
        before_solved = (
            self.is_thread_solved(before) if hasattr(before, "applied_tags") else False
        )
        after_solved = self.is_thread_solved(after)

        if not before_solved and after_solved:
            if str(after.id) not in self.solved_posts:
                await self.add_thread_to_index(after)
                logger.info(f"Immediately indexed newly solved post: {after.name}")

    async def send_similarity_notification(self, thread, similar_posts):
        """Send optimized notification"""
        embed = discord.Embed(
            title="🔍 Similar Solved Posts",
            description="Found some similar posts that might help:",
            color=0x00FF00,
        )

        links = []
        for similar in similar_posts[:3]:
            post_data = self.solved_posts.get(str(similar["id"]))
            if post_data:
                similarity_percentage = int(similar["similarity"] * 100)
                title = (
                    post_data["title"][:50] + "..."
                    if len(post_data["title"]) > 50
                    else post_data["title"]
                )
                links.append(
                    f"[{title}](<{post_data['url']}>) ({similarity_percentage}%)"
                )

        if links:
            embed.add_field(
                name="📋 Check these out:", value="\n".join(links), inline=False
            )

            # Add performance footer for debugging
            if len(self.solved_posts) > 50:
                embed.set_footer(text=f"Searched {len(self.solved_posts)} solved posts")

        await asyncio.sleep(60)
        await thread.send(embed=embed)

    def get_stats(self) -> Dict:
        """Get performance statistics"""
        return {
            **self.stats,
            "total_solved_posts": len(self.solved_posts),
            "cached_embeddings": len(self.embedding_cache),
            "cache_hit_rate": self.stats["cache_hits"]
            / max(1, self.stats["similarity_checks"]),
        }


async def setup(bot):
    await bot.add_cog(ForumSimilarityBot(bot))
