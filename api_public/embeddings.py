"""Standalone embeddings client for Deduplication API.

Adapted from src/organism/memory/embeddings.py — no internal imports.
Lazy singleton AsyncOpenAI client, 5s timeout, 0 retries.

API-PUBLIC-2: get_embeddings_batch() for bulk requests (1 API call per 100 texts).
"""

import os

import openai
import structlog

_log = structlog.get_logger("embeddings")

_client: openai.AsyncOpenAI | None = None
_client_key: str | None = None


def _get_client() -> openai.AsyncOpenAI | None:
    """Return singleton AsyncOpenAI client. Lazy init on first call."""
    global _client, _client_key

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    if not api_key:
        return None

    current_key = f"{api_key}:{base_url}"
    if _client is not None and _client_key == current_key:
        return _client

    kwargs: dict = {
        "api_key": api_key,
        "timeout": 30.0,
        "max_retries": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url

    _client = openai.AsyncOpenAI(**kwargs)
    _client_key = current_key
    return _client


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector via text-embedding-3-small.

    Returns empty list on failure.
    """
    client = _get_client()
    if client is None:
        return []

    text = text[:2000]

    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        _log.warning("embedding_failed", error=str(e))
        return []


_BATCH_CHUNK = 100  # OpenAI limit per request


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings for multiple texts in minimal API calls.

    Chunks into groups of 100 (OpenAI batch limit).
    Returns list aligned with input: empty list for failed items.
    """
    client = _get_client()
    if client is None:
        return [[] for _ in texts]

    truncated = [t[:2000] for t in texts]
    result: list[list[float]] = []

    for i in range(0, len(truncated), _BATCH_CHUNK):
        chunk = truncated[i : i + _BATCH_CHUNK]
        try:
            response = await client.embeddings.create(
                model="text-embedding-3-small",
                input=chunk,
            )
            # response.data is sorted by index
            sorted_data = sorted(response.data, key=lambda d: d.index)
            result.extend(d.embedding for d in sorted_data)
        except Exception as e:
            _log.warning("batch_embedding_failed", error=str(e), chunk_start=i)
            result.extend([] for _ in chunk)

    return result
