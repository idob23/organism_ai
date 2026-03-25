"""API key authentication.

Keys loaded from API_KEYS env var (comma-separated or JSON list).
Tiers from API_KEY_TIERS env var (JSON dict: key -> tier).
"""

import json
import os
import re

import structlog

_log = structlog.get_logger("auth")

_KEY_PATTERN = re.compile(r"^org_[0-9a-f]{32}$")

# Tier limits: (requests_per_day, max_entities_per_request)
TIER_LIMITS: dict[str, tuple[int, int]] = {
    "free": (100, 50),
    "basic": (1000, 200),
    "pro": (10000, 500),
}

_valid_keys: set[str] | None = None
_key_tiers: dict[str, str] | None = None


def _load_keys() -> set[str]:
    global _valid_keys
    if _valid_keys is not None:
        return _valid_keys

    raw = os.getenv("API_KEYS", "")
    if not raw:
        _valid_keys = set()
        return _valid_keys

    # Try JSON list first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            _valid_keys = {k.strip() for k in parsed if k.strip()}
            return _valid_keys
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: comma-separated
    _valid_keys = {k.strip() for k in raw.split(",") if k.strip()}
    return _valid_keys


def _load_tiers() -> dict[str, str]:
    global _key_tiers
    if _key_tiers is not None:
        return _key_tiers

    raw = os.getenv("API_KEY_TIERS", "")
    if not raw:
        _key_tiers = {}
        return _key_tiers

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            _key_tiers = parsed
            return _key_tiers
    except (json.JSONDecodeError, TypeError):
        pass

    _key_tiers = {}
    return _key_tiers


def validate_key(api_key: str) -> bool:
    """Check if API key is valid."""
    keys = _load_keys()
    return api_key in keys


def get_tier(api_key: str) -> str:
    """Get tier for API key. Default: free."""
    tiers = _load_tiers()
    return tiers.get(api_key, "free")


def get_max_entities(api_key: str) -> int:
    """Max entities per request for this key's tier."""
    tier = get_tier(api_key)
    return TIER_LIMITS.get(tier, TIER_LIMITS["free"])[1]


def get_daily_limit(api_key: str) -> int:
    """Daily request limit for this key's tier."""
    tier = get_tier(api_key)
    return TIER_LIMITS.get(tier, TIER_LIMITS["free"])[0]


def reload_keys() -> None:
    """Force reload keys and tiers from env (for testing)."""
    global _valid_keys, _key_tiers
    _valid_keys = None
    _key_tiers = None
