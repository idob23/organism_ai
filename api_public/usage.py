"""Usage tracking via SQLite (aiosqlite).

Fire-and-forget writes — never blocks the API response.
"""

import asyncio
import os
from datetime import datetime, timezone

import aiosqlite
import structlog

_log = structlog.get_logger("usage")

DB_PATH = os.getenv("USAGE_DB_PATH", "usage.db")

_initialized = False


async def _ensure_db() -> None:
    """Create table if not exists."""
    global _initialized
    if _initialized:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                entities_count INTEGER DEFAULT 0,
                groups_found INTEGER DEFAULT 0,
                processing_time_ms INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_key_date
            ON api_usage (api_key, created_at)
        """)
        await db.commit()

    _initialized = True


async def record_usage(
    api_key: str,
    endpoint: str,
    entities_count: int = 0,
    groups_found: int = 0,
    processing_time_ms: int = 0,
) -> None:
    """Record API usage. Safe to call fire-and-forget."""
    try:
        await _ensure_db()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO api_usage
                   (api_key, endpoint, entities_count, groups_found,
                    processing_time_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (api_key, endpoint, entities_count, groups_found,
                 processing_time_ms, now),
            )
            await db.commit()
    except Exception as e:
        _log.warning("usage_record_failed", error=str(e))


async def get_usage_stats(api_key: str) -> dict:
    """Get usage stats for API key."""
    try:
        await _ensure_db()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            # Today
            cursor = await db.execute(
                "SELECT COUNT(*) FROM api_usage WHERE api_key = ? AND created_at >= ?",
                (api_key, today_start),
            )
            row = await cursor.fetchone()
            requests_today = row[0] if row else 0

            # This month
            cursor = await db.execute(
                "SELECT COUNT(*) FROM api_usage WHERE api_key = ? AND created_at >= ?",
                (api_key, month_start),
            )
            row = await cursor.fetchone()
            requests_month = row[0] if row else 0

        return {
            "requests_today": requests_today,
            "requests_this_month": requests_month,
        }
    except Exception as e:
        _log.warning("usage_stats_failed", error=str(e))
        return {"requests_today": 0, "requests_this_month": 0}


def record_usage_background(
    api_key: str,
    endpoint: str,
    entities_count: int = 0,
    groups_found: int = 0,
    processing_time_ms: int = 0,
) -> None:
    """Fire-and-forget usage recording."""
    try:
        asyncio.get_event_loop().create_task(
            record_usage(api_key, endpoint, entities_count,
                         groups_found, processing_time_ms)
        )
    except RuntimeError:
        pass
