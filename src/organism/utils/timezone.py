"""FIX-83: Timezone utilities. Store UTC in DB, display in local timezone."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import settings


def now_local() -> datetime:
    """Return current time in the client timezone."""
    return datetime.now(ZoneInfo(settings.timezone))


def to_local(dt: datetime) -> datetime:
    """Convert a UTC (or naive-UTC) datetime to client timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo(settings.timezone))


def today_local() -> str:
    """Today's date formatted as DD.MM.YYYY in client timezone."""
    return now_local().strftime("%d.%m.%Y")
