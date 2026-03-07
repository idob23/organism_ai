import openai
from config.settings import settings
from src.organism.logging.error_handler import get_logger

_log = get_logger("memory.embeddings")


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for text using OpenAI text-embedding-3-small.

    Supports custom base_url for proxy services (proxyapi.ru etc).
    Returns empty list if OpenAI not configured — BM25 fallback.
    """
    if not settings.openai_api_key:
        return []

    text = text[:2000]

    try:
        kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url

        client = openai.AsyncOpenAI(**kwargs)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        _log.warning(f"Embedding failed: {e}")
        return []
