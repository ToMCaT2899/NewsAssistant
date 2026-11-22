"""Timezone-aware daily schedule, including DST gaps and folds."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_due(day: date, local_time: str, zone: str) -> datetime:
    tz = ZoneInfo(zone)
    candidate = datetime.combine(day, time.fromisoformat(local_time))
    # Pick the first valid local minute after a gap; fold=0 runs an ambiguous time once.
    for _ in range(181):
        aware = candidate.replace(tzinfo=tz, fold=0)
        utc = aware.astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) == candidate:
            return utc
        candidate += timedelta(minutes=1)
    raise ValueError("无法计算本地发送时刻")


def next_due(current: datetime, local_time: str, zone: str) -> datetime:
    day = current.astimezone(ZoneInfo(zone)).date()
    due = local_due(day, local_time, zone)
    return due if due > current else local_due(day + timedelta(days=1), local_time, zone)
