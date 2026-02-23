import anthropic
from config.settings import settings


async def get_embedding(text: str) -> list[float]:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Truncate to avoid token limits
    text = text[:2000]

    # Claude doesnt have embeddings API  use a simple hash-based fallback
    # or integrate OpenAI embeddings. For now: use text-embedding-3-small via OpenAI if available,
    # otherwise return None and skip vector search
    try:
        import openai
        oa = openai.AsyncOpenAI()
        response = await oa.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
    except Exception:
        # Fallback: no embeddings available, memory will work without vector search
        return []
