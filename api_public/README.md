# Deduplication API v1

Standalone REST API for finding duplicate entities using semantic similarity.
Uses OpenAI `text-embedding-3-small` embeddings + cosine similarity + union-find grouping.

## Quick Start

```bash
cd api_public
pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY and API_KEYS
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

## Docker

```bash
docker build -t dedup-api ./api_public
docker run -p 8080:8080 --env-file .env dedup-api
```

## Endpoints

### POST /v1/deduplicate

Find duplicate entities in a list.

**Headers:** `X-API-Key: org_...` (required)

**Request:**
```json
{
  "entities": ["OOO Romashka", "Romashka OOO", "IP Petrov", "Petrov IP", "AO Gazprom"],
  "threshold": 0.85
}
```

**Response (200):**
```json
{
  "groups": [
    {"items": ["OOO Romashka", "Romashka OOO"], "similarity": 0.94},
    {"items": ["IP Petrov", "Petrov IP"], "similarity": 0.91}
  ],
  "total_entities": 5,
  "duplicates_found": 4,
  "processing_time_ms": 1234
}
```

**Errors:** 401 (bad key), 422 (validation), 429 (rate limit), 500 (internal)

### GET /v1/health

Health check (no auth required).

```json
{"status": "ok", "version": "1.0.0"}
```

### GET /v1/usage

Usage stats for your API key.

**Headers:** `X-API-Key: org_...` (required)

```json
{"requests_today": 15, "requests_this_month": 234, "plan": "free"}
```

## Pricing Tiers

| Tier  | Requests/day | Max entities/request |
|-------|-------------|---------------------|
| free  | 100         | 50                  |
| basic | 1,000       | 200                 |
| pro   | 10,000      | 500                 |

Default tier: `free`. Override via `API_KEY_TIERS` env var:
```
API_KEY_TIERS={"org_abc...": "pro"}
```

## API Docs

- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| OPENAI_API_KEY | Yes | OpenAI API key |
| OPENAI_BASE_URL | No | Custom base URL (e.g. proxy) |
| API_KEYS | Yes | Valid API keys (comma-separated or JSON list) |
| API_KEY_TIERS | No | JSON dict: key -> tier (free/basic/pro) |
| USAGE_DB_PATH | No | SQLite DB path (default: usage.db) |
