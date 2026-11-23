"""ORM 映射；数据库约束由版本化 SQL 管理，不使用 create_all() 创建。"""

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.domain.common import identifier, stamp


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)


class Created:
    created_at: Mapped[str] = mapped_column(String, default=stamp)


class Updated:
    updated_at: Mapped[str] = mapped_column(String, default=stamp, onupdate=stamp)


class User(Entity, Created, Updated, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class Subscription(Entity, Created, Updated, Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    persona: Mapped[str] = mapped_column(Text, default="开发者")
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    include_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    exclude_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    recipient_email: Mapped[str] = mapped_column(String)
    recipient_verified_at: Mapped[str | None] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String, default="Asia/Shanghai")
    local_send_time: Mapped[str] = mapped_column(String, default="08:00")
    max_items: Mapped[int] = mapped_column(Integer, default=5)
    lookback_hours: Mapped[int] = mapped_column(Integer, default=24)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[str | None] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Run(Entity, Created, Updated, Base):
    __tablename__ = "runs"
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    business_date: Mapped[str] = mapped_column(String)
    trigger_type: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    snapshot_json: Mapped[str] = mapped_column(Text)
    window_start: Mapped[str] = mapped_column(String)
    window_end: Mapped[str] = mapped_column(String)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    token_usage_json: Mapped[str | None] = mapped_column(Text)
    draft_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[str | None] = mapped_column(String)
    deadline_at: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)


class Article(Entity, Created, Base):
    __tablename__ = "articles"
    canonical_url: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String)
    published_at: Mapped[str | None] = mapped_column(String)
    fetched_at: Mapped[str] = mapped_column(String)
    body_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String)
    evidence_level: Mapped[str] = mapped_column(String)


class RunArticle(Base):
    __tablename__ = "run_articles"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    evidence_level: Mapped[str] = mapped_column(String)


class ToolCall(Entity, Base):
    __tablename__ = "tool_calls"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    model_tool_call_id: Mapped[str] = mapped_column(String)
    tool_name: Mapped[str] = mapped_column(String)
    arguments_json: Mapped[str] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)


class Brief(Entity, Created, Base):
    __tablename__ = "briefs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "subscription_id", "business_date"],
            ["runs.id", "runs.subscription_id", "runs.business_date"],
        ),
    )
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    business_date: Mapped[str] = mapped_column(String)
    run_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    overview: Mapped[str] = mapped_column(Text)
    body_markdown: Mapped[str] = mapped_column(Text)
    no_news: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[str] = mapped_column(String)
    window_end: Mapped[str] = mapped_column(String)


class BriefItem(Entity, Base):
    __tablename__ = "brief_items"
    brief_id: Mapped[str] = mapped_column(ForeignKey("briefs.id"))
    position: Mapped[int] = mapped_column(Integer)
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    event_key: Mapped[str] = mapped_column(String)


class ItemSource(Base):
    __tablename__ = "item_sources"
    item_id: Mapped[str] = mapped_column(ForeignKey("brief_items.id"), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    evidence_level: Mapped[str] = mapped_column(String)


class Delivery(Entity, Created, Updated, Base):
    __tablename__ = "deliveries"
    brief_id: Mapped[str] = mapped_column(ForeignKey("briefs.id"))
    channel: Mapped[str] = mapped_column(String, default="email")
    recipient_email: Mapped[str] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str] = mapped_column(String)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[str | None] = mapped_column(String)
    accepted_at: Mapped[str | None] = mapped_column(String)
    last_error_code: Mapped[str | None] = mapped_column(String)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    retry_confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    retry_confirmation_reason: Mapped[str | None] = mapped_column(Text)
    retry_confirmed_at: Mapped[str | None] = mapped_column(String)


class DeliveryAttempt(Entity, Base):
    __tablename__ = "delivery_attempts"
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
