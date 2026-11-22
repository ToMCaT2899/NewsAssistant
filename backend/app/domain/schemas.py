"""Public contracts and model output schemas."""

import re
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Contract):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SubscriptionInput(Contract):
    version: int = Field(ge=1)
    persona: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=10)
    include_keywords: list[str] = Field(default_factory=list, max_length=20)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=20)
    recipient_email: EmailStr
    timezone: str = "Asia/Shanghai"
    local_send_time: str = "08:00"
    max_items: int = Field(5, ge=1, le=10)
    lookback_hours: int = Field(24, ge=1, le=72)
    enabled: bool = False

    @field_validator("topics", "include_keywords", "exclude_keywords")
    @classmethod
    def keywords(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 50 for value in values):
            raise ValueError("关键词须为 1～50 字")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("timezone")
    @classmethod
    def zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效时区") from exc
        return value

    @field_validator("local_send_time")
    @classmethod
    def clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("使用 HH:MM")
        time.fromisoformat(value)
        return value


class SourceRef(Contract):
    article_id: str = Field(min_length=1, max_length=80)
    evidence_level: str = Field(pattern="^(full_text|search_excerpt)$")


class BriefItem(Contract):
    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    why_it_matters: str = Field(min_length=1, max_length=1000)
    event_key: str = Field(min_length=1, max_length=200)
    sources: list[SourceRef] = Field(min_length=1, max_length=5)


class BriefDraft(Contract):
    title: str = Field(min_length=1, max_length=200)
    overview: str = Field(min_length=1, max_length=2000)
    no_news: bool = False
    items: list[BriefItem] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def consistency(self) -> "BriefDraft":
        if self.no_news != (len(self.items) == 0):
            raise ValueError("no_news 与条目不一致")
        if len({item.event_key for item in self.items}) != len(self.items):
            raise ValueError("同事件重复")
        return self


class RetryRequest(Contract):
    confirm_unknown: bool = False
    reason: str = Field("", max_length=500)
