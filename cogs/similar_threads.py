import discord
from discord.ext import commands, tasks
import json
import asyncio
import openai
import numpy as np
import os
import logging
from datetime import datetime, timedelta, timezone  # ADD: Import timezone
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

        # ADD: Thread-safe operations
        self._file_lock = asyncio.Lock()
        self._processing_threads = set()  # Track threads being processed

        # Optimization settings
        self.embedding_model = "text-embedding-3-small"
        self.embedding_version = "v1"
        self.batch_size = 100
        self.max_retries = 3
        self.cache_duration_days = 60

        # Performance tracking
        self.stats = {
            "embeddings_generated": 0,
            "cache_hits": 0,
            "similarity_checks": 0,
            "matches_found": 0,
        }

        # Initialize cache before loading data
        self.embedding_cache = {}

        # Load existing data
        self.solved_posts = self.load_solved_posts()

        # Start background tasks
        self.check_new_solved_posts.start()
        self.refresh_old_embeddings.start()

    # HELPER METHOD: Get timezone-aware datetime
    def _now_utc(self):
        """Get current UTC datetime with timezone info"""
        return datetime.now(timezone.utc)

    # HELPER METHOD: Parse datetime with timezone handling
    def _parse_datetime_safe(
        self, dt_string: str, default: str = "2020-01-01T00:00:00+00:00"
    ):
        """Parse datetime string and ensure it's timezone-aware"""
        try:
            dt = datetime.fromisoformat(dt_string)
            # If naive, assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return datetime.fromisoformat(default)

    def load_solved_posts(self):
        try:
            with open(self.solved_posts_file, "r") as f:
                content = f.read().strip()
                if not content:
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
        recent_cutoff = self._now_utc() - timedelta(days=7)
        for post_id, post_data in posts_data.items():
            if post_data.get("embedding"):
                indexed_at = self._parse_datetime_safe(
                    post_data.get("indexed_at", "2020-01-01T00:00:00+00:00")
                )
                if indexed_at > recent_cutoff:
                    self.embedding_cache[post_id] = np.array(post_data["embedding"])

    async def save_solved_posts(self):
        """Thread-safe save with duplicate prevention"""
        async with self._file_lock:
            # ADDED: Remove any potential duplicates before saving
            self._remove_duplicates()

            # Convert numpy arrays back to lists for JSON serialization
            serializable_data = {}
            for post_id, post_data in self.solved_posts.items():
                serializable_data[post_id] = post_data.copy()
                if "embedding" in serializable_data[post_id] and isinstance(
                    serializable_data[post_id]["embedding"], np.ndarray
                ):
                    serializable_data[post_id]["embedding"] = serializable_data[
                        post_id
                    ]["embedding"].tolist()

            # Use compact JSON for embeddings (no pretty printing)
            with open(self.solved_posts_file, "w") as f:
                json.dump(serializable_data, f, separators=(",", ":"))

    def _remove_duplicates(self):
        """Remove duplicate entries based on thread ID"""
        # Convert all keys to strings to ensure consistency
        cleaned_posts = {}
        seen_ids = set()

        for post_id, post_data in self.solved_posts.items():
            str_id = str(post_id)
            if str_id not in seen_ids:
                cleaned_posts[str_id] = post_data
                seen_ids.add(str_id)
            else:
                logger.warning(f"Removed duplicate entry for thread {str_id}")

        self.solved_posts = cleaned_posts

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
                        logger.error(
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
        """Check for newly solved posts with batch processing and deduplication"""
        await self.bot.wait_until_ready()

        # ADDED: Wait a bit after bot startup to avoid API spam
        if not hasattr(self, "_first_run_done"):
            await asyncio.sleep(10)
            self._first_run_done = True

        forum_channel = self.bot.get_channel(self.forum_channel_id)
        if not forum_channel:
            return

        # CHANGED: Use set to prevent duplicates, then convert to list
        new_thread_ids = set()
        new_threads = []

        try:
            # Collect new solved threads from active threads
            for thread in forum_channel.threads:
                thread_id = str(thread.id)
                if (
                    self.is_thread_solved(thread)
                    and thread_id not in self.solved_posts
                    and thread_id
                    not in self._processing_threads  # ADDED: Check if already being processed
                ):
                    if thread_id not in new_thread_ids:  # ADDED: Deduplicate
                        new_thread_ids.add(thread_id)
                        new_threads.append(thread)

            # Check archived threads (limited batch) - prioritize recent ones
            count = 0
            # FIX: Use timezone-aware datetime
            cutoff_date = self._now_utc() - timedelta(
                days=30
            )  # Only check recent archived threads
            async for thread in forum_channel.archived_threads(limit=100):
                if count >= 50:
                    break

                # FIX: Safe datetime comparison - thread.created_at is timezone-aware
                if thread.created_at < cutoff_date:
                    continue

                thread_id = str(thread.id)
                if (
                    self.is_thread_solved(thread)
                    and thread_id not in self.solved_posts
                    and thread_id
                    not in self._processing_threads  # ADDED: Check if already being processed
                ):
                    if thread_id not in new_thread_ids:  # ADDED: Deduplicate
                        new_thread_ids.add(thread_id)
                        new_threads.append(thread)
                        count += 1

            if new_threads:
                # ADDED: Mark threads as being processed
                for thread in new_threads:
                    self._processing_threads.add(str(thread.id))

                try:
                    successfully_added = await self.batch_add_threads_to_index(
                        new_threads
                    )
                    logger.info(
                        f"Successfully added {successfully_added} new solved posts. Total: {len(self.solved_posts)}"
                    )
                finally:
                    # ADDED: Remove from processing set
                    for thread in new_threads:
                        self._processing_threads.discard(str(thread.id))

        except Exception as e:
            logger.error(f"Error checking for new solved posts: {e}")
            # ADDED: Clean up processing set on error
            for thread in new_threads:
                self._processing_threads.discard(str(thread.id))

    @tasks.loop(hours=24)
    async def refresh_old_embeddings(self):
        """Refresh embeddings older than cache duration"""
        await self.bot.wait_until_ready()

        cutoff_date = self._now_utc() - timedelta(days=self.cache_duration_days)
        old_posts = []

        for post_id, post_data in self.solved_posts.items():
            indexed_date = self._parse_datetime_safe(
                post_data.get("indexed_at", "2020-01-01T00:00:00+00:00")
            )
            if (
                indexed_date < cutoff_date
                and post_data.get("embedding_version") != self.embedding_version
            ):
                old_posts.append((post_id, post_data))

        if old_posts:
            logger.info(f"Refreshing {len(old_posts)} old embeddings...")
            await self.batch_update_embeddings(old_posts)

    async def batch_add_threads_to_index(self, threads: List[discord.Thread]) -> int:
        """Add multiple threads to index with batch embedding generation"""
        if not threads:
            return 0

        # ADDED: Final check for duplicates before processing
        unique_threads = []
        for thread in threads:
            if str(thread.id) not in self.solved_posts:
                unique_threads.append(thread)
            else:
                logger.info(f"Skipping duplicate thread {thread.id} during batch add")

        if not unique_threads:
            return 0

        # Collect all texts for batch processing
        texts = []
        thread_data = []
        failed_threads = []

        for thread in unique_threads:
            try:
                # IMPROVED: Try multiple methods to get thread content
                starter_message = None
                combined_text = None

                # Method 1: Try fetching with thread.id (original starter message)
                try:
                    starter_message = await thread.fetch_message(thread.id)
                    combined_text = f"Title: {thread.name or 'Untitled'}\nBody: {starter_message.content or ''}"
                except discord.NotFound:
                    # Method 2: Try getting the first message from history
                    try:
                        async for message in thread.history(limit=1, oldest_first=True):
                            starter_message = message
                            combined_text = f"Title: {thread.name or 'Untitled'}\nBody: {message.content or ''}"
                            break
                    except discord.Forbidden:
                        # Method 3: Use just the title if we can't access messages
                        combined_text = f"Title: {thread.name or 'Untitled'}\nBody: [Content not accessible]"
                        logger.warning(
                            f"Could not access messages for thread {thread.id}, using title only"
                        )
                except discord.Forbidden:
                    # No permission to read messages, use title only
                    combined_text = f"Title: {thread.name or 'Untitled'}\nBody: [Content not accessible]"
                    logger.warning(
                        f"No permission to read messages for thread {thread.id}, using title only"
                    )

                if combined_text:
                    texts.append(combined_text)
                    thread_data.append(
                        {
                            "thread": thread,
                            "starter_message": starter_message,
                            "text": combined_text,
                        }
                    )
                else:
                    failed_threads.append(thread.id)
                    logger.warning(f"Could not get any content for thread {thread.id}")

            except Exception as e:
                failed_threads.append(thread.id)
                # IMPROVED: More specific error logging
                if "10008" in str(e):  # Unknown Message
                    logger.warning(
                        f"Starter message not found for thread {thread.id} ({thread.name}): Message may have been deleted"
                    )
                elif "50001" in str(e):  # Missing Access
                    logger.warning(
                        f"No access to thread {thread.id} ({thread.name}): Missing permissions"
                    )
                else:
                    logger.error(
                        f"Unexpected error preparing thread {thread.id} ({thread.name}): {e}"
                    )
                continue

        if failed_threads:
            logger.info(
                f"Failed to process {len(failed_threads)} threads out of {len(unique_threads)}"
            )

        if not texts:
            logger.warning("No threads could be processed for embedding generation")
            return 0

        # Generate embeddings in batch
        embeddings = await self.generate_embeddings_batch(texts)

        # Store results
        successful_adds = 0
        for i, data in enumerate(thread_data):
            if i < len(embeddings) and embeddings[i]:
                thread = data["thread"]
                starter_message = data["starter_message"]
                thread_id = str(thread.id)

                # ADDED: Double-check before storing
                if thread_id not in self.solved_posts:
                    # Store in index
                    self.solved_posts[thread_id] = {
                        "title": thread.name or "Untitled",
                        "body": (
                            starter_message.content
                            if starter_message
                            else "[Content not accessible]"
                        ),
                        "author_id": (
                            starter_message.author.id if starter_message else None
                        ),
                        # FIX: Store datetime as ISO string with timezone
                        "created_at": thread.created_at.isoformat(),
                        "indexed_at": self._now_utc().isoformat(),
                        "url": thread.jump_url,
                        "embedding": embeddings[i],
                        "embedding_version": self.embedding_version,
                        "content_accessible": starter_message
                        is not None,  # Track if we got the actual content
                    }

                    # Add to memory cache
                    self.embedding_cache[thread_id] = np.array(embeddings[i])
                    successful_adds += 1
                else:
                    logger.warning(f"Thread {thread_id} already exists, skipping")

        await self.save_solved_posts()

        if successful_adds > 0:
            logger.info(f"Successfully indexed {successful_adds} threads")
        if failed_threads:
            logger.info(
                f"Could not index {len(failed_threads)} threads due to access issues"
            )

        # Return the count of successful additions for accurate logging
        return successful_adds

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
                self.solved_posts[post_id]["refreshed_at"] = self._now_utc().isoformat()

                # Update cache
                self.embedding_cache[post_id] = np.array(embeddings[i])

        await self.save_solved_posts()  # CHANGED: Made async

    def is_thread_solved(self, thread):
        """Check if a thread has the solved tag"""
        if hasattr(thread, "applied_tags"):
            for tag in thread.applied_tags:
                if tag.name == self.solved_tag_name:
                    return True
        return False

    async def add_thread_to_index(self, thread):
        """Add single thread to index (fallback for immediate updates)"""
        thread_id = str(thread.id)

        # ADDED: Check if already processing or exists
        if thread_id in self._processing_threads or thread_id in self.solved_posts:
            logger.info(f"Thread {thread_id} already being processed or exists")
            return

        # ADDED: Mark as processing
        self._processing_threads.add(thread_id)

        try:
            await self.batch_add_threads_to_index([thread])
        finally:
            # ADDED: Always remove from processing set
            self._processing_threads.discard(thread_id)

    async def find_similar_solved_posts_optimized(
        self, title: str, body: str
    ) -> List[Dict]:
        """Optimized similarity search with smart filtering"""
        logger.info(
            f"Starting similarity search with {len(self.solved_posts)} solved posts"
        )

        if not self.solved_posts:
            logger.info("No solved posts in index")
            return []

        start_time = time.time()

        # Generate embedding for new post
        new_text = f"Title: {title}\nBody: {body}"
        logger.info(f"Generating embedding for: '{title[:50]}...'")
        new_embedding = await self.generate_embedding(new_text)
        if not new_embedding:
            logger.info("Failed to generate embedding")
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

        logger.info(f"Comparing against {len(embedding_batch)} posts with embeddings")

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

            logger.info(
                f"{above_threshold} posts above threshold {self.similarity_threshold}"
            )

        # Sort by similarity and get top candidates
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        top_candidates = similarities[:8]

        # Update stats
        self.stats["similarity_checks"] += 1
        if top_candidates:
            self.stats["matches_found"] += 1

        processing_time = time.time() - start_time
        logger.info(f"Similarity search: {processing_time:.3f}s")

        if not top_candidates:
            return []

        # Use AI for final ranking
        logger.info("Running AI ranking...")
        result = await self.ai_rank_candidates_optimized(title, body, top_candidates)
        logger.info(f"AI returned {len(result)} final matches")
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
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Expert at matching solved posts to new questions. Always return valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
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
        logger.info(f"New thread detected: {thread.name} in channel {thread.parent.id}")

        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != self.forum_channel_id
        ):
            return

        await asyncio.sleep(2)

        try:
            starter_message = await thread.fetch_message(thread.id)

            if not thread.name and not starter_message.content:
                logger.error("No title or content to analyze")
                return

            logger.info(
                f"Analyzing post: '{thread.name}' with {len(self.solved_posts)} solved posts"
            )

            # Find similar solved posts using optimized search
            similar_posts = await self.find_similar_solved_posts_optimized(
                thread.name or "Untitled", starter_message.content or ""
            )

            logger.info(f"Found {len(similar_posts)} similar posts")

            if similar_posts:
                await self.send_similarity_notification(thread, similar_posts)
                logger.info("Sent similarity notification")
            else:
                logger.info("No similar posts found above threshold")

        except Exception as e:
            logger.error(f"Error processing new thread: {e}")

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        """Detect solved threads with immediate indexing and duplicate prevention"""
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
            thread_id = str(after.id)

            # ADDED: More thorough duplicate checking
            if (
                thread_id not in self.solved_posts
                and thread_id not in self._processing_threads
            ):
                await self.add_thread_to_index(after)
                logger.info(f"Immediately indexed newly solved post: {after.name}")
            else:
                logger.info(f"Thread {thread_id} already indexed or being processed")

    async def send_similarity_notification(self, thread, similar_posts):
        """Send optimized notification"""
        embed = discord.Embed(
            title="🔍 Similar Solved Posts",
            description="Found some similar posts that might help:",
            color=0xFFCD3F,
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

        await asyncio.sleep(50)
        await thread.send(embed=embed)

    def get_stats(self) -> Dict:
        """Get performance statistics"""
        return {
            **self.stats,
            "total_solved_posts": len(self.solved_posts),
            "cached_embeddings": len(self.embedding_cache),
            "cache_hit_rate": self.stats["cache_hits"]
            / max(1, self.stats["similarity_checks"]),
            "currently_processing": len(self._processing_threads),  # ADDED
        }

    # ADDED: Utility method to clean existing duplicates
    async def clean_duplicates(self):
        """Manually clean duplicates from the index"""
        original_count = len(self.solved_posts)
        self._remove_duplicates()
        await self.save_solved_posts()
        cleaned_count = len(self.solved_posts)

        if original_count != cleaned_count:
            logger.info(f"Cleaned {original_count - cleaned_count} duplicate entries")
            return original_count - cleaned_count
        return 0

    # ADDED: Method to clean up inaccessible threads
    async def cleanup_inaccessible_threads(self):
        """Remove threads from index that are no longer accessible"""
        forum_channel = self.bot.get_channel(self.forum_channel_id)
        if not forum_channel:
            return 0

        inaccessible_threads = []

        for thread_id in list(self.solved_posts.keys()):
            try:
                # Try to get the thread
                thread = forum_channel.get_thread(int(thread_id))
                if not thread:
                    # Try archived threads
                    found = False
                    async for archived_thread in forum_channel.archived_threads(
                        limit=None
                    ):
                        if str(archived_thread.id) == thread_id:
                            found = True
                            break

                    if not found:
                        inaccessible_threads.append(thread_id)
            except Exception:
                inaccessible_threads.append(thread_id)

        if inaccessible_threads:
            for thread_id in inaccessible_threads:
                del self.solved_posts[thread_id]
                self.embedding_cache.pop(thread_id, None)

            await self.save_solved_posts()
            logger.info(
                f"Cleaned up {len(inaccessible_threads)} inaccessible threads from index"
            )

        return len(inaccessible_threads)


async def setup(bot):
    await bot.add_cog(ForumSimilarityBot(bot))
