"""One shared OpenAI client for every cog.

Each AsyncOpenAI instance owns its own HTTP connection pool and TLS context,
so creating one per cog multiplies that memory for no benefit.
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI | None:
    """Return the shared client, or None when OPENAI_API_KEY is not set."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            _client = AsyncOpenAI(api_key=api_key)
    return _client
