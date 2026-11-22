"""Idempotent task creation, claims, audit and atomic publication."""

import json
from datetime import timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, identifier, now, parse_time, required, stamp
from backend.app.domain.schemas import BriefDraft
from backend.app.infrastructure.database import Database
from backend.app.models.schema import (
    Article,
    Brief,
    BriefItem,
    Delivery,
    ItemSource,
    Run,
    RunArticle,
    Subscription,
    ToolCall,
)
from backend.app.repository.queries import (
    brief_for_run,
    owned_run,
    runs_for,
    subscription_for,
    tools_for,
)
from backend.app.service.accounts import subscription_view


def run_view(run: Run, brief_id: str | None = None, reused: bool = False) -> dict:
    return {
        key: getattr(run, key)
        for key in (
            "id",
            "status",
            "business_date",
            "trigger_type",
            "model_calls",
            "tool_calls",
            "created_at",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
        )
    } | {"run_id": run.id, "brief_id": brief_id, "reused": reused}


def create_run(
    database: Database,
    user_id: str,
    key: str,
    trigger: str = "manual",
    require_model: bool = False,
    settings: Settings | None = None,
    expected_version: int | None = None,
) -> dict:
    if require_model and (settings is None or not settings.model_configured):
        raise AppError("MODEL_NOT_CONFIGURED", "请先配置模型地址、名称和 API Key", 503)
    try:
        with database.session.begin() as session:
            sub = subscription_for(session, user_id)
            if expected_version is not None and (
                sub.version != expected_version or not sub.enabled
            ):
                raise AppError("SUBSCRIPTION_CHANGED", "订阅已变更，跳过旧调度", 409)
            current = now()
            day = current.astimezone(ZoneInfo(sub.timezone)).date().isoformat()
            brief = session.scalar(
                select(Brief).where(Brief.subscription_id == sub.id, Brief.business_date == day)
            )
            if brief:
                return run_view(required(session.get(Run, brief.run_id)), brief.id, True)
            actual_key = f"daily:{day}" if trigger == "scheduled" else f"manual:{key}"
            existing = session.scalar(
                select(Run).where(Run.subscription_id == sub.id, Run.idempotency_key == actual_key)
            )
            if not existing:
                existing = session.scalar(
                    select(Run).where(
                        Run.subscription_id == sub.id, Run.status.in_(["queued", "running"])
                    )
                )
            if existing:
                return run_view(existing, reused=True)
            run = Run(
                subscription_id=sub.id,
                business_date=day,
                trigger_type=trigger,
                idempotency_key=actual_key,
                snapshot_json=json.dumps(subscription_view(sub), ensure_ascii=False),
                window_start=stamp(current - timedelta(hours=sub.lookback_hours)),
                window_end=stamp(current),
            )
            session.add(run)
            session.flush()
            return run_view(run)
    except IntegrityError as exc:
        raise AppError("TASK_CONFLICT", "已有任务正在创建，请用相同幂等键重试", 409) from exc


def claim_run(database: Database, owner: str, settings: Settings) -> Run | None:
    with database.session.begin() as session:
        candidate = session.scalar(
            select(Run.id).where(Run.status == "queued").order_by(Run.created_at).limit(1)
        )
        if not candidate:
            return None
        current = now()
        count = cast(
            CursorResult,
            session.execute(
                update(Run)
                .where(Run.id == candidate, Run.status == "queued")
                .values(
                    status="running",
                    lease_owner=owner,
                    started_at=stamp(current),
                    lease_expires_at=stamp(
                        current
                        + timedelta(
                            seconds=max(settings.lease_seconds, settings.agent_max_seconds + 60)
                        )
                    ),
                    deadline_at=stamp(current + timedelta(seconds=settings.agent_max_seconds)),
                )
            ),
        ).rowcount
        return required(session.get(Run, candidate)) if count == 1 else None


def audit_tool(database: Database, run_id: str, event: dict) -> None:
    with database.session.begin() as session:
        sequence = (
            session.scalar(
                select(func.coalesce(func.max(ToolCall.sequence), 0)).where(
                    ToolCall.run_id == run_id
                )
            )
            or 0
        ) + 1
        # Sequence is independent from the execution budget.
        safe_args = {
            key: str(value)[:200]
            if key
            in {"query", "source_id", "path", "dir", "keyword", "article_id", "days", "limit"}
            else "<omitted>"
            for key, value in event["arguments"].items()
        }
        session.add(
            ToolCall(
                run_id=run_id,
                sequence=sequence,
                model_tool_call_id=event["call_id"],
                tool_name=event["name"],
                arguments_json=json.dumps(safe_args, ensure_ascii=False),
                result_summary=event["summary"],
                status="succeeded"
                if event["ok"]
                else "rejected"
                if event["error"]
                in {"PATH_DENIED", "COMMAND_DENIED", "UNKNOWN_TOOL", "INVALID_ARGUMENTS"}
                else "failed",
                error_code=event["error"],
                duration_ms=event["duration_ms"],
                started_at=stamp(),
                finished_at=stamp(),
            )
        )


def finish_run(database: Database, run_id: str, owner: str, result: dict) -> None:
    error = result.get("error") or "INVALID_BRIEF"
    with database.session.begin() as session:
        session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "running", Run.lease_owner == owner)
            .values(
                status="incomplete"
                if error in {"BUDGET_EXHAUSTED", "INVALID_BRIEF", "LEASE_LOST"}
                else "failed",
                model_calls=result.get("model_calls", 0),
                tool_calls=result.get("tool_calls", 0),
                draft_json=json.dumps(result.get("result")) if result.get("result") else None,
                error_code=error,
                error_message="生成未完成，请查看错误类别与工具记录",
                finished_at=stamp(),
            )
        )


def publish(database: Database, run_id: str, owner: str, draft: BriefDraft, result: dict) -> str:
    with database.session.begin() as session:
        # Acquire write ownership before reading; prevents lease recovery racing publication.
        changed = cast(
            CursorResult,
            session.execute(
                update(Run)
                .where(
                    Run.id == run_id,
                    Run.status == "running",
                    Run.lease_owner == owner,
                    Run.lease_expires_at > stamp(),
                    Run.deadline_at > stamp(),
                )
                .values(updated_at=stamp())
            ),
        ).rowcount
        if changed != 1:
            raise AppError("LEASE_LOST", "任务已过期，不得发布")
        run = required(session.get(Run, run_id))
        sub = required(session.get(Subscription, run.subscription_id))
        existing = session.scalar(
            select(Brief).where(
                Brief.subscription_id == sub.id, Brief.business_date == run.business_date
            )
        )
        if existing:
            brief_id = existing.id
        else:
            brief = Brief(
                subscription_id=sub.id,
                business_date=run.business_date,
                run_id=run.id,
                title=draft.title,
                overview=draft.overview,
                body_markdown=draft.overview,
                no_news=int(draft.no_news),
                window_start=run.window_start,
                window_end=run.window_end,
            )
            session.add(brief)
            session.flush()
            brief_id = brief.id
            for position, item in enumerate(draft.items, 1):
                row = BriefItem(
                    brief_id=brief.id,
                    position=position,
                    headline=item.headline,
                    summary=item.summary,
                    why_it_matters=item.why_it_matters,
                    event_key=item.event_key,
                )
                session.add(row)
                session.flush()
                for ref in item.sources:
                    scope = required(session.get(RunArticle, (run.id, ref.article_id)))
                    article = required(session.get(Article, ref.article_id))
                    if ref.evidence_level == "full_text" and scope.evidence_level != "full_text":
                        raise AppError("INVALID_SOURCE", "尚未取得本次原文证据")
                    if not article.published_at or not (
                        parse_time(run.window_start)
                        <= parse_time(article.published_at)
                        < parse_time(run.window_end)
                    ):
                        raise AppError("INVALID_SOURCE", "来源发布时间不在任务窗口内")
                    session.add(
                        ItemSource(
                            item_id=row.id,
                            article_id=ref.article_id,
                            evidence_level=ref.evidence_level,
                        )
                    )
            if sub.enabled and sub.recipient_verified_at:
                session.add(
                    Delivery(
                        brief_id=brief.id,
                        recipient_email=sub.recipient_email,
                        message_id=f"<{identifier()}@news-assistant.local>",
                        next_attempt_at=stamp(),
                    )
                )
        if not existing:
            brief = required(session.get(Brief, brief_id))
            brief.body_markdown = "\n\n".join(
                [draft.overview]
                + [
                    f"## {item.headline}\n{item.summary}\n与你有关：{item.why_it_matters}"
                    for item in draft.items
                ]
            )
        run.status, run.finished_at = "succeeded", stamp()
        run.model_calls, run.tool_calls = result["model_calls"], result["tool_calls"]
        run.token_usage_json = json.dumps(result.get("usage", {}))
        return brief_id


def recover_expired(database: Database) -> None:
    with database.session.begin() as session:
        session.execute(
            update(Run)
            .where(Run.status == "running", Run.lease_expires_at <= stamp())
            .values(
                status="incomplete",
                error_code="LEASE_EXPIRED",
                error_message="任务租约已过期，请重新生成",
                finished_at=stamp(),
            )
        )


def list_runs(database: Database, user_id: str, limit: int, offset: int) -> list[dict]:
    with database.session() as session:
        return [run_view(row) for row in runs_for(session, user_id, limit, offset)]


def get_run(database: Database, user_id: str, run_id: str) -> dict:
    with database.session() as session:
        run = owned_run(session, user_id, run_id)
        brief = brief_for_run(session, run)
        return run_view(run, brief.id if brief else None)


def list_tool_calls(
    database: Database, user_id: str, run_id: str, limit: int, offset: int
) -> list[dict]:
    with database.session() as session:
        owned_run(session, user_id, run_id)
        return [
            {
                "id": row.id,
                "sequence": row.sequence,
                "tool_name": row.tool_name,
                "arguments": json.loads(row.arguments_json),
                "status": row.status,
                "duration_ms": row.duration_ms,
                "error_code": row.error_code,
            }
            for row in tools_for(session, run_id, limit, offset)
        ]
