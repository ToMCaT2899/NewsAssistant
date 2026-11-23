"""经过校验的响应契约，不直接序列化原始 ORM 实体。"""

from pydantic import BaseModel, Field

from backend.app.domain.schemas import SubscriptionInput


class TokenOutput(BaseModel):
    access_token: str
    token_type: str


class SubscriptionOutput(SubscriptionInput):
    id: str
    recipient_verified: bool
    next_run_at: str | None = None


class ServiceStatus(BaseModel):
    model_configured: bool
    mail_configured: bool
    news_provider: str


class RunOutput(BaseModel):
    id: str
    run_id: str
    status: str
    business_date: str
    trigger_type: str
    model_calls: int
    tool_calls: int
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    brief_id: str | None = None
    reused: bool = False


class ToolOutput(BaseModel):
    id: str
    sequence: int
    tool_name: str
    arguments: dict[str, str]
    status: str
    duration_ms: int | None = None
    error_code: str | None = None


class BriefSummary(BaseModel):
    id: str
    title: str
    overview: str
    business_date: str
    no_news: bool


class SourceOutput(BaseModel):
    title: str
    url: str
    source_name: str
    published_at: str | None
    evidence_level: str


class ItemOutput(BaseModel):
    headline: str
    summary: str
    why_it_matters: str
    sources: list[SourceOutput]


class DeliveryOutput(BaseModel):
    id: str
    status: str
    attempt_count: int
    last_error_code: str | None = None


class BriefOutput(BriefSummary):
    window_start: str
    window_end: str
    created_at: str
    items: list[ItemOutput] = Field(default_factory=list)
    delivery: DeliveryOutput | None = None


class RetryOutput(BaseModel):
    id: str
    status: str
