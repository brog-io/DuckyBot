"""Suggest previously-solved forum threads when a new question is posted.

The cog keeps an on-disk index of solved posts:

  * ``solved_posts_index.json``      - metadata only (title, body, url, ...)
  * ``solved_posts_embeddings.npz``  - the embedding matrix, L2-normalised

Splitting the two keeps the JSON small and human-readable while the vectors
live in a compact binary file. An index written by the older version of this
cog (embeddings inline in the JSON) is migrated automatically on first load.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
import numpy as np
from discord.ext import commands, tasks
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DATA_DIR = Path(".")
META_FILE = DATA_DIR / "solved_posts_index.json"
VECTOR_FILE = DATA_DIR / "solved_posts_embeddings.npz"

FORUM_CHANNEL_ID = 1383504546361380995
SOLVED_TAG_ID = 1383506837252472982
SOLVED_TAG_NAME = "Solved"

# text-embedding-3-* support Matryoshka truncation via `dimensions`, so
# 3-large at 1536 dims gives better retrieval than 3-small at the same
# storage cost. Changing either value re-indexes the corpus automatically.
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMENSIONS = 1536
RANKING_MODEL = "gpt-5.6-luna"

SIMILARITY_THRESHOLD = 0.55
EMBED_BATCH_SIZE = 100
EMBED_MAX_RETRIES = 3
EMBED_TIMEOUT = 60
RERANK_TIMEOUT = 45

# Candidates handed to the embedding shortlist and then to the LLM reranker.
SHORTLIST_SIZE = 8
RERANK_SIZE = 5
MAX_SUGGESTIONS = 3

# Give the self-help cog time to post its answer before piling on.
NOTIFY_DELAY = 50

# Cap per refresh run so a model change can't produce one enormous bill.
REFRESH_BATCH_LIMIT = 250

ARCHIVED_SCAN_LIMIT = 100
ARCHIVED_MAX_NEW = 50
ARCHIVED_MAX_AGE_DAYS = 30


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO timestamp, always returning something timezone-aware."""
    if value:
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime(2020, 1, 1, tzinfo=timezone.utc)


def _normalise(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise row-wise so cosine similarity is a plain dot product."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


class SolvedPostIndex:
    """Metadata + a normalised embedding matrix, persisted side by side."""

    def __init__(self, meta_path: Path, vector_path: Path):
        self.meta_path = Path(meta_path)
        self.vector_path = Path(vector_path)

        self.posts: dict[str, dict] = {}
        self.ids: list[str] = []
        self.matrix: np.ndarray | None = None

        # The model the stored vectors were produced with. Queries are
        # embedded to match the index, not the config, so retrieval keeps
        # working while a migration to a new model is still in progress.
        self.model: str = EMBED_MODEL
        self.dimensions: int = EMBED_DIMENSIONS

        self._rows: dict[str, int] = {}
        self.migrated_from_legacy = False

    # -- persistence -------------------------------------------------------

    def _load_sync(self) -> None:
        posts: dict[str, dict] = {}
        legacy_vectors: dict[str, list[float]] = {}

        if self.meta_path.exists():
            try:
                raw = self.meta_path.read_text(encoding="utf-8").strip()
                posts = json.loads(raw) if raw else {}
            except (OSError, ValueError) as e:
                logger.error(f"Could not read {self.meta_path}: {e!r}; starting empty")
                posts = {}

        # Old format kept the vector inline under "embedding".
        for post_id, data in list(posts.items()):
            if isinstance(data, dict) and "embedding" in data:
                vector = data.pop("embedding")
                if vector:
                    legacy_vectors[str(post_id)] = vector
                self.migrated_from_legacy = True

        posts = {str(k): v for k, v in posts.items() if isinstance(v, dict)}

        ids: list[str] = []
        matrix: np.ndarray | None = None
        model, dimensions = EMBED_MODEL, EMBED_DIMENSIONS

        if legacy_vectors:
            ids = [pid for pid in legacy_vectors if pid in posts]
            if ids:
                matrix = _normalise(np.array([legacy_vectors[i] for i in ids]))
                # Legacy files were always text-embedding-3-small at 1536.
                model = posts[ids[0]].get("embedding_model", "text-embedding-3-small")
                dimensions = matrix.shape[1]
            logger.info(f"Migrating {len(ids)} inline embeddings to {self.vector_path}")
        elif self.vector_path.exists():
            try:
                with np.load(self.vector_path, allow_pickle=False) as bundle:
                    ids = [str(x) for x in bundle["ids"].tolist()]
                    matrix = np.asarray(bundle["vectors"], dtype=np.float32)
                    if "model" in bundle:
                        model = str(bundle["model"].item())
                    if "dimensions" in bundle:
                        dimensions = int(bundle["dimensions"].item())
            except (OSError, ValueError, KeyError) as e:
                logger.error(f"Could not read {self.vector_path}: {e!r}; ignoring")
                ids, matrix = [], None

        # Drop anything that lost its partner on either side.
        if matrix is not None and len(ids) == matrix.shape[0]:
            keep = [i for i, pid in enumerate(ids) if pid in posts]
            if len(keep) != len(ids):
                logger.warning(f"Dropping {len(ids) - len(keep)} orphaned vectors")
                ids = [ids[i] for i in keep]
                matrix = matrix[keep] if keep else None
            if matrix is not None and matrix.size:
                dimensions = matrix.shape[1]
        else:
            if matrix is not None:
                logger.error("Vector/id count mismatch; rebuilding index from scratch")
            ids, matrix = [], None

        self.posts = posts
        self.ids = ids
        self.matrix = matrix
        self.model = model
        self.dimensions = dimensions
        self._reindex_rows()

        logger.info(
            f"Loaded {len(self.posts)} solved posts "
            f"({len(self.ids)} embedded, {self.model}@{self.dimensions})"
        )

    def _save_sync(self) -> None:
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_meta = self.meta_path.with_suffix(self.meta_path.suffix + ".tmp")
        tmp_meta.write_text(
            json.dumps(self.posts, separators=(",", ":")), encoding="utf-8"
        )
        tmp_meta.replace(self.meta_path)

        tmp_vec = self.vector_path.with_suffix(self.vector_path.suffix + ".tmp")
        matrix = (
            self.matrix
            if self.matrix is not None
            else np.zeros((0, self.dimensions), dtype=np.float32)
        )
        # Write through a handle: np.savez_* appends ".npz" to bare paths.
        with open(tmp_vec, "wb") as fh:
            np.savez_compressed(
                fh,
                ids=np.array(self.ids, dtype="U32"),
                vectors=matrix,
                model=np.array(self.model),
                dimensions=np.array(self.dimensions),
            )
        tmp_vec.replace(self.vector_path)

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    async def save(self) -> None:
        await asyncio.to_thread(self._save_sync)

    # -- mutation ----------------------------------------------------------

    def _reindex_rows(self) -> None:
        self._rows = {pid: i for i, pid in enumerate(self.ids)}

    def add_many(self, entries: list[tuple[str, dict, list[float]]]) -> int:
        """Add (post_id, metadata, vector) triples. Existing ids are skipped."""
        fresh = [
            (pid, meta, vec) for pid, meta, vec in entries if pid not in self._rows
        ]
        if not fresh:
            return 0

        block = _normalise(np.array([vec for _, _, vec in fresh], dtype=np.float32))
        if self.matrix is None or self.matrix.size == 0:
            self.matrix = block
        elif block.shape[1] != self.matrix.shape[1]:
            logger.error(
                f"Refusing to add {block.shape[1]}-dim vectors to a "
                f"{self.matrix.shape[1]}-dim index"
            )
            return 0
        else:
            self.matrix = np.vstack([self.matrix, block])

        for pid, meta, _ in fresh:
            self.posts[pid] = meta
            self.ids.append(pid)
        self._reindex_rows()
        self.dimensions = self.matrix.shape[1]
        return len(fresh)

    def replace_all_vectors(
        self, ids: list[str], vectors: np.ndarray, model: str, dimensions: int
    ) -> None:
        """Swap in a freshly embedded matrix after a model change."""
        self.ids = list(ids)
        self.matrix = _normalise(vectors) if len(ids) else None
        self.model = model
        self.dimensions = dimensions
        self._reindex_rows()

    def remove_many(self, post_ids: list[str]) -> int:
        doomed = {pid for pid in post_ids if pid in self.posts or pid in self._rows}
        if not doomed:
            return 0

        for pid in doomed:
            self.posts.pop(pid, None)

        if self.matrix is not None and self.ids:
            keep = [i for i, pid in enumerate(self.ids) if pid not in doomed]
            self.ids = [self.ids[i] for i in keep]
            self.matrix = self.matrix[keep] if keep else None
            self._reindex_rows()
        return len(doomed)

    # -- query -------------------------------------------------------------

    def search(
        self, query_vector: np.ndarray, threshold: float, limit: int
    ) -> list[tuple[str, float]]:
        """Return (post_id, cosine similarity) pairs above the threshold."""
        if self.matrix is None or not self.ids:
            return []

        query = _normalise(query_vector)[0]
        if query.shape[0] != self.matrix.shape[1]:
            logger.error(
                f"Query is {query.shape[0]}-dim but index is "
                f"{self.matrix.shape[1]}-dim; skipping search"
            )
            return []

        # Both sides are normalised, so the dot product *is* cosine similarity.
        scores = self.matrix @ query
        hits = np.flatnonzero(scores > threshold)
        if hits.size == 0:
            return []

        ranked = hits[np.argsort(-scores[hits])][:limit]
        return [(self.ids[i], float(scores[i])) for i in ranked]

    @property
    def needs_reindex(self) -> bool:
        return bool(self.ids) and (
            self.model != EMBED_MODEL or self.dimensions != EMBED_DIMENSIONS
        )

    def missing_vectors(self) -> list[str]:
        return [pid for pid in self.posts if pid not in self._rows]


# --------------------------------------------------------------------------
# Cog
# --------------------------------------------------------------------------


class ForumSimilarityBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        api_key = os.getenv("OPENAI_API_KEY")
        self.openai_client = AsyncOpenAI(api_key=api_key) if api_key else None
        if not self.openai_client:
            logger.warning("OPENAI_API_KEY is not set; similarity search is disabled")

        self.forum_channel_id = FORUM_CHANNEL_ID
        self.index = SolvedPostIndex(META_FILE, VECTOR_FILE)

        self._write_lock = asyncio.Lock()
        self._processing_threads: set[str] = set()

        self.stats = {
            "embeddings_generated": 0,
            "similarity_checks": 0,
            "matches_found": 0,
            "rerank_failures": 0,
        }

    async def cog_load(self) -> None:
        await self.index.load()
        if self.index.migrated_from_legacy:
            async with self._write_lock:
                await self.index.save()
            logger.info("Legacy index migrated to the split metadata/vector format")

        self.check_new_solved_posts.start()
        self.refresh_stale_embeddings.start()

    def cog_unload(self) -> None:
        self.check_new_solved_posts.cancel()
        self.refresh_stale_embeddings.cancel()

    # -- OpenAI ------------------------------------------------------------

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> list[list[float] | None]:
        """Embed texts in batches. Failed batches come back as None entries."""
        if not texts or not self.openai_client:
            return [None] * len(texts)

        model = model or self.index.model
        dimensions = dimensions or self.index.dimensions

        kwargs: dict = {"model": model}
        # Only the v3 models accept Matryoshka truncation.
        if model.startswith("text-embedding-3"):
            kwargs["dimensions"] = dimensions

        results: list[list[float] | None] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            for attempt in range(1, EMBED_MAX_RETRIES + 1):
                try:
                    response = await self.openai_client.embeddings.create(
                        input=batch, timeout=EMBED_TIMEOUT, **kwargs
                    )
                    results.extend(item.embedding for item in response.data)
                    self.stats["embeddings_generated"] += len(batch)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if attempt == EMBED_MAX_RETRIES:
                        logger.error(
                            f"Embedding batch failed after {EMBED_MAX_RETRIES} "
                            f"attempts: {e!r}"
                        )
                        results.extend([None] * len(batch))
                    else:
                        logger.warning(f"Embedding attempt {attempt} failed: {e!r}")
                        await asyncio.sleep(2**attempt)
        return results

    async def embed_one(self, text: str) -> list[float] | None:
        return (await self.embed_texts([text]))[0]

    async def rerank(
        self, title: str, body: str, candidates: list[dict]
    ) -> list[dict] | None:
        """Ask the LLM which shortlisted posts actually help. None on failure."""
        if not candidates or not self.openai_client:
            return None

        shortlist = candidates[:RERANK_SIZE]
        payload = [
            {"id": c["id"], "title": c["title"], "body": c["body"][:150]}
            for c in shortlist
        ]
        prompt = (
            f'New post: "{title}"\n{body[:400]}\n\n'
            f"Candidate solved posts:\n{json.dumps(payload, indent=1)}\n\n"
            'Return JSON: {"matches": [{"id": "<id>", "reason": "<short why>"}]}\n'
            "Include only posts that would genuinely help solve the new post, "
            "best first. Return an empty list if none of them help."
        )

        try:
            response = await self.openai_client.chat.completions.create(
                model=RANKING_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You match previously solved forum posts to a new "
                            "question. Always return valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                timeout=RERANK_TIMEOUT,
            )
            data = json.loads(response.choices[0].message.content or "{}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Rerank failed: {e!r}")
            self.stats["rerank_failures"] += 1
            return None

        matches = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(matches, list):
            logger.warning(f"Rerank returned an unexpected shape: {data!r}")
            self.stats["rerank_failures"] += 1
            return None

        by_id = {c["id"]: c for c in shortlist}
        ordered: list[dict] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            candidate = by_id.get(str(match.get("id")))
            # Keep the real cosine score; the model only decides inclusion.
            if candidate and candidate not in ordered:
                ordered.append(
                    {**candidate, "reason": str(match.get("reason", ""))[:120]}
                )
        return ordered

    # -- indexing ----------------------------------------------------------

    def is_thread_solved(self, thread) -> bool:
        for tag in getattr(thread, "applied_tags", None) or []:
            if getattr(tag, "id", None) == SOLVED_TAG_ID:
                return True
            if getattr(tag, "name", None) == SOLVED_TAG_NAME:
                return True
        return False

    async def _thread_text(
        self, thread: discord.Thread
    ) -> tuple[str, discord.Message | None]:
        """Title + opening post, degrading to title-only when unreadable."""
        starter = None
        try:
            starter = await thread.fetch_message(thread.id)
        except discord.NotFound:
            try:
                async for message in thread.history(limit=1, oldest_first=True):
                    starter = message
                    break
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"No history access for thread {thread.id}: {e!r}")
        except discord.Forbidden:
            logger.warning(f"No permission to read thread {thread.id}")
        except discord.HTTPException as e:
            logger.warning(f"Could not fetch starter of thread {thread.id}: {e!r}")

        title = thread.name or "Untitled"
        body = starter.content if starter else "[Content not accessible]"
        return f"Title: {title}\nBody: {body}", starter

    async def index_threads(self, threads: list[discord.Thread]) -> int:
        """Embed and store a batch of solved threads. Returns how many landed."""
        pending = [t for t in threads if str(t.id) not in self.index.posts]
        if not pending:
            return 0

        texts: list[str] = []
        prepared: list[tuple[discord.Thread, discord.Message | None]] = []
        for thread in pending:
            try:
                text, starter = await self._thread_text(thread)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Could not prepare thread {thread.id}: {e!r}")
                continue
            texts.append(text)
            prepared.append((thread, starter))

        if not texts:
            return 0

        vectors = await self.embed_texts(texts)

        entries: list[tuple[str, dict, list[float]]] = []
        for (thread, starter), vector in zip(prepared, vectors):
            if not vector:
                continue
            entries.append(
                (
                    str(thread.id),
                    {
                        "title": thread.name or "Untitled",
                        "body": (starter.content if starter else "")[:1000],
                        "author_id": starter.author.id if starter else None,
                        "created_at": thread.created_at.isoformat(),
                        "indexed_at": _now_utc().isoformat(),
                        "url": thread.jump_url,
                        "embedding_model": self.index.model,
                        "content_accessible": starter is not None,
                    },
                    vector,
                )
            )

        if not entries:
            return 0

        async with self._write_lock:
            added = self.index.add_many(entries)
            if added:
                await self.index.save()
        return added

    async def add_thread_to_index(self, thread: discord.Thread) -> None:
        thread_id = str(thread.id)
        if thread_id in self._processing_threads or thread_id in self.index.posts:
            return

        self._processing_threads.add(thread_id)
        try:
            await self.index_threads([thread])
        finally:
            self._processing_threads.discard(thread_id)

    # -- background tasks --------------------------------------------------

    @tasks.loop(minutes=30)
    async def check_new_solved_posts(self) -> None:
        forum = self.bot.get_channel(self.forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            return

        found: dict[str, discord.Thread] = {}
        cutoff = _now_utc() - timedelta(days=ARCHIVED_MAX_AGE_DAYS)

        def wanted(thread) -> bool:
            tid = str(thread.id)
            return (
                self.is_thread_solved(thread)
                and tid not in self.index.posts
                and tid not in self._processing_threads
                and tid not in found
            )

        try:
            for thread in forum.threads:
                if wanted(thread):
                    found[str(thread.id)] = thread

            archived = 0
            async for thread in forum.archived_threads(limit=ARCHIVED_SCAN_LIMIT):
                if archived >= ARCHIVED_MAX_NEW:
                    break
                if thread.created_at < cutoff:
                    continue
                if wanted(thread):
                    found[str(thread.id)] = thread
                    archived += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error scanning for solved posts: {e!r}", exc_info=True)
            return

        if not found:
            return

        self._processing_threads.update(found)
        try:
            added = await self.index_threads(list(found.values()))
            logger.info(
                f"Indexed {added} new solved posts (total {len(self.index.posts)})"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error indexing solved posts: {e!r}", exc_info=True)
        finally:
            self._processing_threads.difference_update(found)

    @tasks.loop(hours=24)
    async def refresh_stale_embeddings(self) -> None:
        """Re-embed the corpus after a model or dimension change."""
        if not self.openai_client:
            return

        needs_model_change = self.index.needs_reindex
        missing = self.index.missing_vectors()
        if not needs_model_change and not missing:
            return

        if needs_model_change:
            logger.info(
                f"Re-embedding {len(self.index.posts)} posts: "
                f"{self.index.model}@{self.index.dimensions} -> "
                f"{EMBED_MODEL}@{EMBED_DIMENSIONS}"
            )
            targets = list(self.index.posts)
        else:
            logger.info(f"Backfilling {len(missing)} posts with no embedding")
            targets = missing

        capped = targets[:REFRESH_BATCH_LIMIT]
        texts = [
            f"Title: {self.index.posts[pid].get('title', '')}\n"
            f"Body: {self.index.posts[pid].get('body', '')}"
            for pid in capped
        ]
        vectors = await self.embed_texts(
            texts, model=EMBED_MODEL, dimensions=EMBED_DIMENSIONS
        )

        good = [(pid, vec) for pid, vec in zip(capped, vectors) if vec]
        if not good:
            logger.error("Re-embedding produced no usable vectors; index unchanged")
            return

        async with self._write_lock:
            if needs_model_change:
                if len(good) < len(targets):
                    # A partial swap would leave two models in one matrix.
                    logger.warning(
                        f"Only {len(good)}/{len(targets)} posts re-embedded; "
                        "retrying the rest on the next run"
                    )
                    return
                self.index.replace_all_vectors(
                    [pid for pid, _ in good],
                    np.array([vec for _, vec in good], dtype=np.float32),
                    EMBED_MODEL,
                    EMBED_DIMENSIONS,
                )
            else:
                self.index.add_many(
                    [(pid, self.index.posts[pid], vec) for pid, vec in good]
                )
            for pid, _ in good:
                self.index.posts[pid]["embedding_model"] = EMBED_MODEL
                self.index.posts[pid]["refreshed_at"] = _now_utc().isoformat()
            await self.index.save()

        logger.info(f"Refreshed {len(good)} embeddings")

    @check_new_solved_posts.before_loop
    @refresh_stale_embeddings.before_loop
    async def _wait_for_bot(self) -> None:
        await self.bot.wait_until_ready()
        # Stagger startup so a restart doesn't fire every API call at once.
        await asyncio.sleep(10)

    # -- search ------------------------------------------------------------

    async def find_similar_solved_posts(self, title: str, body: str) -> list[dict]:
        if not self.index.posts or not self.openai_client:
            return []

        started = time.perf_counter()
        query = await self.embed_one(f"Title: {title}\nBody: {body}")
        if not query:
            return []

        hits = self.index.search(
            np.asarray(query, dtype=np.float32), SIMILARITY_THRESHOLD, SHORTLIST_SIZE
        )
        self.stats["similarity_checks"] += 1

        logger.info(
            f"{len(hits)} of {len(self.index.ids)} posts above "
            f"{SIMILARITY_THRESHOLD} in {time.perf_counter() - started:.3f}s"
        )
        if not hits:
            return []

        candidates = [
            {
                "id": pid,
                "similarity": score,
                "title": self.index.posts[pid].get("title", "Untitled"),
                "body": self.index.posts[pid].get("body", "")[:200],
                "url": self.index.posts[pid].get("url", ""),
            }
            for pid, score in hits
            if pid in self.index.posts
        ]

        ranked = await self.rerank(title, body, candidates)
        if ranked is None:
            # Reranker unavailable - fall back to raw embedding order.
            ranked = candidates[:MAX_SUGGESTIONS]

        if ranked:
            self.stats["matches_found"] += 1
        return ranked[:MAX_SUGGESTIONS]

    # -- events ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        if (
            not isinstance(thread.parent, discord.ForumChannel)
            or thread.parent.id != self.forum_channel_id
        ):
            return

        logger.info(f"New thread: {thread.name!r} ({thread.id})")
        await asyncio.sleep(2)

        try:
            _, starter = await self._thread_text(thread)
            title = thread.name or ""
            body = starter.content if starter else ""
            if not title and not body:
                logger.info(f"Thread {thread.id} has no title or content to analyse")
                return

            similar = await self.find_similar_solved_posts(title, body)
            if similar:
                await self.send_similarity_notification(thread, similar)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error processing thread {thread.id}: {e!r}", exc_info=True)

    @commands.Cog.listener()
    async def on_thread_update(self, before, after) -> None:
        if (
            not isinstance(after.parent, discord.ForumChannel)
            or after.parent.id != self.forum_channel_id
        ):
            return

        if self.is_thread_solved(before) or not self.is_thread_solved(after):
            return

        thread_id = str(after.id)
        if thread_id in self.index.posts or thread_id in self._processing_threads:
            return

        await self.add_thread_to_index(after)
        logger.info(f"Indexed newly solved post {after.name!r} ({after.id})")

    async def send_similarity_notification(
        self, thread: discord.Thread, similar_posts: list[dict]
    ) -> None:
        embed = discord.Embed(
            title="🔍 Similar Solved Posts",
            description="Found some similar posts that might help:",
            color=0xFFCD3F,
        )

        links = []
        for post in similar_posts[:MAX_SUGGESTIONS]:
            if not post.get("url"):
                continue
            title = post["title"]
            if len(title) > 50:
                title = title[:50] + "..."
            # Real cosine similarity, not a number the LLM made up.
            links.append(f"[{title}](<{post['url']}>) ({post['similarity']:.0%})")

        if not links:
            return

        embed.add_field(
            name="📋 Check these out:", value="\n".join(links), inline=False
        )
        if len(self.index.posts) > 50:
            embed.set_footer(text=f"Searched {len(self.index.posts)} solved posts")

        await asyncio.sleep(NOTIFY_DELAY)
        try:
            await thread.send(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Could not post suggestions in thread {thread.id}: {e!r}")

    # -- maintenance -------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            **self.stats,
            "total_solved_posts": len(self.index.posts),
            "embedded_posts": len(self.index.ids),
            "embedding_model": f"{self.index.model}@{self.index.dimensions}",
            "currently_processing": len(self._processing_threads),
        }

    async def cleanup_inaccessible_threads(self) -> int:
        """Drop index entries whose threads no longer exist."""
        forum = self.bot.get_channel(self.forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            return 0

        # One pass over the archive, not one pass per indexed post.
        live_ids = {str(t.id) for t in forum.threads}
        try:
            async for thread in forum.archived_threads(limit=None):
                live_ids.add(str(thread.id))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Could not enumerate archived threads: {e!r}")
            return 0

        gone = [pid for pid in self.index.posts if pid not in live_ids]
        if not gone:
            return 0

        async with self._write_lock:
            removed = self.index.remove_many(gone)
            await self.index.save()

        logger.info(f"Removed {removed} inaccessible threads from the index")
        return removed


async def setup(bot):
    await bot.add_cog(ForumSimilarityBot(bot))
