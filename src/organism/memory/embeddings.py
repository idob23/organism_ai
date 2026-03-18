import openai
from config.settings import settings
from src.organism.logging.error_handler import get_logger

_log = get_logger("memory.embeddings")

# PERF-2: Lazy singleton — avoid creating client on every call
_client: openai.AsyncOpenAI | None = None
_client_key: str | None = None  # tracks (api_key, base_url) to detect config changes


def _get_client() -> openai.AsyncOpenAI | None:
    """Return singleton AsyncOpenAI client. Lazy init on first call.

    Recreates if api_key or base_url changed (defensive, not expected in runtime).
    Returns None if openai_api_key not configured.
    """
    global _client, _client_key

    if not settings.openai_api_key:
        return None

    current_key = f"{settings.openai_api_key}:{settings.openai_base_url or ''}"
    if _client is not None and _client_key == current_key:
        return _client

    kwargs = {
        "api_key": settings.openai_api_key,
        "timeout": 5.0,      # PERF-2: total timeout 5 sec (was 600 default)
        "max_retries": 0,     # PERF-2: no retries — fail fast, BM25 fallback works
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url

    _client = openai.AsyncOpenAI(**kwargs)
    _client_key = current_key
    return _client


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for text using OpenAI text-embedding-3-small.

    PERF-2: Singleton client, 5s timeout, 0 retries.
    Returns empty list on failure — BM25 fallback handles it.
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
        _log.warning(f"Embedding failed: {e}")
        return []
