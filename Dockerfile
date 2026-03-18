FROM python:3.11-slim

WORKDIR /app

# System deps for pymupdf, pptx, etc.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev libssl-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e . 2>/dev/null || \
    pip install --no-cache-dir \
        anthropic tavily-python asyncpg sqlalchemy "sqlalchemy[asyncio]" \
        pgvector aiohttp aiogram openai python-pptx numpy httpx \
        "pydantic-settings" structlog psycopg2-binary aiofiles python-dotenv \
        docker beautifulsoup4 lxml requests tiktoken aiosqlite \
        reportlab pypdf2 openpyxl pymupdf

COPY . .

RUN mkdir -p data/logs data/outputs data/workspace

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "scripts/health_check.py"]

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "main.py", "--telegram"]
