"""考虑时区的每日调度，处理夏令时造成的时间缺口和重复。"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_due(day: date, local_time: str, zone: str) -> datetime:
    tz = ZoneInfo(zone)
    candidate = datetime.combine(day, time.fromisoformat(local_time))
    # 跳过时间缺口，选择其后首个有效分钟；fold=0 使重复的本地时刻仅执行一次。
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
