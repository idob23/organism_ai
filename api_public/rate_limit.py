"""In-memory rate limiting by API key.

Uses defaultdict + time windows. Resets daily at midnight UTC.
"""

import time
from collections import defaultdict

from auth import get_daily_limit

# {api_key: (day_number, request_count)}
_counters: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))


def _current_day() -> int:
    """Day number since epoch (UTC)."""
    return int(time.time()) // 86400


def check_rate_limit(api_key: str) -> tuple[bool, int]:
    """Check if request is within rate limit.

    Returns (allowed, remaining_requests).
    """
    day = _current_day()
    limit = get_daily_limit(api_key)

    stored_day, count = _counters[api_key]
    if stored_day != day:
        # New day — reset counter
        count = 0

    if count >= limit:
        return False, 0

    return True, limit - count


def record_request(api_key: str) -> None:
    """Record a request for rate limiting."""
    day = _current_day()
    stored_day, count = _counters[api_key]
    if stored_day != day:
        count = 0
    _counters[api_key] = (day, count + 1)


def get_usage_today(api_key: str) -> int:
    """Get request count for today."""
    day = _current_day()
    stored_day, count = _counters[api_key]
    if stored_day != day:
        return 0
    return count


# ── IP-based rate limiting (for public web UI) ─────────────────────

# {ip: (day_number, request_count)}
_ip_counters: dict[str, tuple[int, int]] = {}
IP_DAILY_LIMIT = 5


def check_ip_rate_limit(ip: str) -> tuple[bool, int]:
    """Check if IP is within daily rate limit.

    Returns (allowed, remaining_requests).
    """
    day = _current_day()
    stored_day, count = _ip_counters.get(ip, (0, 0))
    if stored_day != day:
        count = 0

    if count >= IP_DAILY_LIMIT:
        return False, 0

    return True, IP_DAILY_LIMIT - count


def record_ip_request(ip: str) -> None:
    """Record a request for IP-based rate limiting."""
    day = _current_day()
    stored_day, count = _ip_counters.get(ip, (0, 0))
    if stored_day != day:
        count = 0
    _ip_counters[ip] = (day, count + 1)
