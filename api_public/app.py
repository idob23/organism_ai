"""Deduplication API v1 — standalone FastAPI service.

Usage:
    uvicorn api_public.app:app --host 0.0.0.0 --port 8080
"""

import asyncio

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import structlog
from dotenv import load_dotenv

load_dotenv()

from auth import get_max_entities, get_tier, validate_key
from dedup import find_duplicates
from rate_limit import check_rate_limit, get_usage_today, record_request
from usage import get_usage_stats, record_usage_background

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
_log = structlog.get_logger("app")

API_VERSION = "1.0.0"

app = FastAPI(
    title="Deduplication API",
    description="Find duplicate entities using semantic similarity (embeddings + cosine).",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ──────────────────────────────────────

class DeduplicateRequest(BaseModel):
    entities: list[str] = Field(
        ..., min_length=2, max_length=500,
        description="List of entity names to check for duplicates (2\u2013500).",
    )
    threshold: float = Field(
        default=0.85, ge=0.5, le=1.0,
        description="Similarity threshold (0.5\u20131.0). Higher = stricter.",
    )


class DuplicateGroupResponse(BaseModel):
    items: list[str]
    similarity: float


class DeduplicateResponse(BaseModel):
    groups: list[DuplicateGroupResponse]
    total_entities: int
    duplicates_found: int
    processing_time_ms: int


class HealthResponse(BaseModel):
    status: str
    version: str


class UsageResponse(BaseModel):
    requests_today: int
    requests_this_month: int
    plan: str


class ErrorResponse(BaseModel):
    detail: str


# ── Auth dependency ────────────────────────────────────────────────

def _require_api_key(x_api_key: str | None = Header(None)) -> str:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    if not validate_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# ── Endpoints ──────────────────────────────────────────────────────

@app.get("/v1/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version=API_VERSION)


@app.post(
    "/v1/deduplicate",
    response_model=DeduplicateResponse,
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def deduplicate(
    body: DeduplicateRequest,
    x_api_key: str | None = Header(None),
):
    api_key = _require_api_key(x_api_key)

    # Rate limit check
    allowed, remaining = check_rate_limit(api_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Upgrade your plan for higher limits.",
        )

    # Entity count limit per tier
    max_ent = get_max_entities(api_key)
    if len(body.entities) > max_ent:
        raise HTTPException(
            status_code=422,
            detail=f"Too many entities: {len(body.entities)}. "
                   f"Your plan allows max {max_ent} per request.",
        )

    # Record rate limit hit
    record_request(api_key)

    try:
        result = await find_duplicates(body.entities, body.threshold)
    except Exception as e:
        _log.error("deduplicate_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Internal processing error")

    groups_resp = [
        DuplicateGroupResponse(items=g.items, similarity=g.similarity)
        for g in result.groups
    ]

    # Fire-and-forget usage tracking
    record_usage_background(
        api_key=api_key,
        endpoint="/v1/deduplicate",
        entities_count=result.total_entities,
        groups_found=len(result.groups),
        processing_time_ms=result.processing_time_ms,
    )

    return DeduplicateResponse(
        groups=groups_resp,
        total_entities=result.total_entities,
        duplicates_found=result.duplicates_found,
        processing_time_ms=result.processing_time_ms,
    )


@app.get(
    "/v1/usage",
    response_model=UsageResponse,
    responses={401: {"model": ErrorResponse}},
)
async def usage(x_api_key: str | None = Header(None)):
    api_key = _require_api_key(x_api_key)
    stats = await get_usage_stats(api_key)
    tier = get_tier(api_key)
    return UsageResponse(
        requests_today=stats["requests_today"],
        requests_this_month=stats["requests_this_month"],
        plan=tier,
    )
