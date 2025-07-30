import discord from discord.ext import commands, tasks import json import asyncio import openai import numpy as np import os import logging from datetime import datetime, timedelta import time from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(name)

Use the async OpenAI client if available

from openai import AsyncOpenAI

class ForumSimilarityBot(commands.Cog): def init(self, bot): self.bot = bot self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) self.forum_channel_id = 1383504546361380995 self.similarity_threshold = 0.55 self.solved_posts_file = "solved_posts_index.json" self.solved_tag_name = "Solved"

self._file_lock = asyncio.Lock()
    self._processing_threads = set()

    self.embedding_model = "text-embedding-3-small"
    self.embedding_version = "v1"
    self.batch_size = 100
    self.max_retries = 3
    self.cache_duration_days = 60

    self.stats = {
        "embeddings_generated": 0,
        "cache_hits": 0,
        "similarity_checks": 0,
        "matches_found": 0,
    }

    self.embedding_cache = {}
    self.solved_posts = self.load_solved_posts()

    self.check_new_solved_posts.start()
    self.refresh_old_embeddings.start()
    self.monitor_loop_health.start()

def load_solved_posts(self):
    try:
        with open(self.solved_posts_file, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
            self.preload_embeddings(data)
            return data
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load solved posts file: {e}")
        logger.info("Starting with empty index...")
        return {}

def preload_embeddings(self, posts_data):
    recent_cutoff = datetime.now() - timedelta(days=7)
    for post_id, post_data in posts_data.items():
        if (
            post_data.get("embedding")
            and datetime.fromisoformat(post_data.get("indexed_at", "2020-01-01")) > recent_cutoff
        ):
            self.embedding_cache[post_id] = np.array(post_data["embedding"])

async def save_solved_posts(self):
    async with self._file_lock:
        self._remove_duplicates()
        serializable_data = {}
        for post_id, post_data in self.solved_posts.items():
            serializable_data[post_id] = post_data.copy()
            if "embedding" in serializable_data[post_id] and isinstance(
                serializable_data[post_id]["embedding"], np.ndarray
            ):
                serializable_data[post_id]["embedding"] = serializable_data[post_id]["embedding"].tolist()

        with open(self.solved_posts_file, "w") as f:
            json.dump(serializable_data, f, separators=(",", ":"))

def _remove_duplicates(self):
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

async def generate_embeddings_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
    if not texts:
        return []

    all_embeddings = []
    for i in range(0, len(texts), self.batch_size):
        batch = texts[i : i + self.batch_size]
        for attempt in range(self.max_retries):
            try:
                response = await self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=batch,
                )
                batch_embeddings = [data.embedding for data in response.data]
                all_embeddings.extend(batch_embeddings)
                self.stats["embeddings_generated"] += len(batch_embeddings)
                break
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed to generate embeddings after {self.max_retries} attempts: {e}")
                    all_embeddings.extend([None] * len(batch))
                else:
                    await asyncio.sleep(2 ** attempt)

    return all_embeddings

async def find_similar_solved_posts_optimized(self, title: str, body: str) -> List[Dict]:
    logger.info(f"Starting similarity search with {len(self.solved_posts)} solved posts")
    if not self.solved_posts:
        return []

    new_text = f"Title: {title}\nBody: {body}"
    new_embedding = await self.generate_embedding(new_text)
    if not new_embedding:
        return []

    new_embedding_np = np.array(new_embedding)

    embedding_batch = []
    post_ids_batch = []
    post_data_batch = []
    for post_id, post_data in self.solved_posts.items():
        embedding = self.get_embedding_from_cache(post_id)
        if embedding is not None:
            embedding_batch.append(embedding)
            post_ids_batch.append(post_id)
            post_data_batch.append(post_data)

    similarities = await asyncio.to_thread(self.compute_similarities, embedding_batch, new_embedding_np)

    results = []
    for i, similarity in enumerate(similarities):
        if similarity > self.similarity_threshold:
            results.append({
                "id": int(post_ids_batch[i]),
                "similarity": float(similarity),
                "title": post_data_batch[i]["title"],
                "body": post_data_batch[i]["body"][:200],
                "url": post_data_batch[i]["url"]
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    self.stats["similarity_checks"] += 1
    if results:
        self.stats["matches_found"] += 1

    return results[:8]

def compute_similarities(self, batch, new_emb):
    matrix = np.vstack(batch)
    return np.dot(matrix, new_emb) / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(new_emb))

def get_embedding_from_cache(self, post_id: str) -> Optional[np.ndarray]:
    if post_id in self.embedding_cache:
        self.stats["cache_hits"] += 1
        return self.embedding_cache[post_id]

    post_data = self.solved_posts.get(post_id)
    if post_data and "embedding" in post_data:
        embedding = np.array(post_data["embedding"])
        self.embedding_cache[post_id] = embedding
        return embedding

    return None

@tasks.loop(seconds=60)
async def monitor_loop_health(self):
    logger.info("Loop heartbeat check passed.")

async def generate_embedding(self, text: str) -> Optional[List[float]]:
    embeddings = await self.generate_embeddings_batch([text])
    return embeddings[0] if embeddings else None

async def send_similarity_notification(self, thread, similar_posts):
    embed = discord.Embed(
        title="🔍 Similar Solved Posts",
        description="Found some similar posts that might help:",
        color=0xFFCD3F,
    )

    links = []
    for similar in similar_posts[:3]:
        post_data = self.solved_posts.get(str(similar["id"]))
        if post_data:
            sim_pct = int(similar["similarity"] * 100)
            title = (post_data["title"][:50] + "...") if len(post_data["title"]) > 50 else post_data["title"]
            links.append(f"[{title}](<{post_data['url']}>) ({sim_pct}%)")

    if links:
        embed.add_field(name="📋 Check these out:", value="\n".join(links), inline=False)

    if len(self.solved_posts) > 50:
        embed.set_footer(text=f"Searched {len(self.solved_posts)} solved posts")

    await thread.send(embed=embed)

async def setup(bot): await bot.add_cog(ForumSimilarityBot(bot))

