"""统一的异常、UTC 时间表示和标识符。"""

from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime | None = None) -> str:
    return (
        (value or now()).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(UTC)


def identifier() -> str:
    return str(uuid4())


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


T = TypeVar("T")


def required(value: T | None) -> T:
    if value is None:
        raise AppError("NOT_FOUND", "关联记录不存在", 404)
    return value
