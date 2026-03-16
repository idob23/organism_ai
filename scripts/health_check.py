#!/usr/bin/env python3
"""Organism AI — Docker health check script.

Checks:
1. Database connectivity (SELECT 1)
2. Heartbeat file freshness (data/heartbeat not older than 120s)

Exit code 0 = healthy, 1 = unhealthy.
Sync-only (no async) — designed for Docker HEALTHCHECK.
"""

import os
import sys
import time


def check_database() -> bool:
    """Check database connectivity via psycopg2."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("UNHEALTHY: DATABASE_URL not set")
        return False

    # Convert asyncpg URL to psycopg2 format
    dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"UNHEALTHY: Database check failed: {e}")
        return False


def check_heartbeat() -> bool:
    """Check that data/heartbeat exists and is fresh (< 120s)."""
    heartbeat_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "heartbeat",
    )

    if not os.path.exists(heartbeat_path):
        print("UNHEALTHY: Heartbeat file not found")
        return False

    try:
        with open(heartbeat_path, "r") as f:
            ts = float(f.read().strip())
        age = time.time() - ts
        if age > 120:
            print(f"UNHEALTHY: Heartbeat stale ({age:.0f}s old)")
            return False
        return True
    except Exception as e:
        print(f"UNHEALTHY: Heartbeat read error: {e}")
        return False


def main() -> None:
    db_ok = check_database()
    hb_ok = check_heartbeat()

    if db_ok and hb_ok:
        print("HEALTHY")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
