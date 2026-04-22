from zoneinfo import ZoneInfo
from datetime import datetime

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)
