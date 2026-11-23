"""事务发件箱的投递认领、渲染、有限重试与结果不确定时的恢复。"""

from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, now, required, stamp
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.qq_smtp import MailResult
from backend.app.models.schema import Brief, Delivery, DeliveryAttempt, Subscription
from backend.app.repository.queries import owned_brief
from backend.app.service.briefs import brief_detail


def render_mail(detail: dict, delivery: Delivery, settings: Settings) -> EmailMessage:
    message = EmailMessage()
    safe_title = " ".join(detail["title"].splitlines())
    message["Subject"] = f"[AI 日报] {detail['business_date']} {safe_title}"
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_username))
    message["To"], message["Message-ID"] = delivery.recipient_email, delivery.message_id
    text = [detail["title"], detail["overview"]]
    html = [f"<h1>{escape(detail['title'])}</h1><p>{escape(detail['overview'])}</p>"]
    for item in detail["items"]:
        text.extend([item["headline"], item["summary"], "与你有关：" + item["why_it_matters"]])
        html.append(
            f"<h2>{escape(item['headline'])}</h2><p>{escape(item['summary'])}</p>"
            f"<p>与你有关：{escape(item['why_it_matters'])}</p>"
        )
        for source in item["sources"]:
            url = source["url"]
            if not url.startswith(("https://", "http://")):
                raise AppError("UNSAFE_URL", "来源链接无效")
            text.append(url)
            html.append(
                f'<p><a href="{escape(url, quote=True)}">{escape(source["title"])}</a>'
                f"（{escape(source['evidence_level'])}）</p>"
            )
    message.set_content("\n\n".join(text))
    message.add_alternative("<html><body>" + "".join(html) + "</body></html>", subtype="html")
    return message


def claim_delivery(database: Database, owner: str, settings: Settings) -> Delivery | None:
    with database.session.begin() as session:
        candidate = session.scalar(
            select(Delivery.id)
            .where(
                Delivery.status.in_(["pending", "retry_wait"]), Delivery.next_attempt_at <= stamp()
            )
            .order_by(Delivery.created_at)
            .limit(1)
        )
        if not candidate:
            return None
        changed = cast(
            CursorResult,
            session.execute(
                update(Delivery)
                .where(Delivery.id == candidate, Delivery.status.in_(["pending", "retry_wait"]))
                .values(
                    status="sending",
                    lease_owner=owner,
                    lease_expires_at=stamp(now() + timedelta(seconds=settings.lease_seconds)),
                    attempt_count=Delivery.attempt_count + 1,
                )
            ),
        ).rowcount
        if changed != 1:
            return None
        row = required(session.get(Delivery, candidate))
        brief = required(session.get(Brief, row.brief_id))
        sub = required(session.get(Subscription, brief.subscription_id))
        if (
            not sub.enabled
            or not sub.recipient_verified_at
            or sub.recipient_email != row.recipient_email
        ):
            row.status = "cancelled"
            return None
        session.add(
            DeliveryAttempt(
                delivery_id=row.id,
                attempt_no=row.attempt_count,
                status="sending",
                started_at=stamp(),
            )
        )
        return row


def finish_delivery(
    database: Database,
    row: Delivery,
    owner: str,
    outcome: MailResult,
    settings: Settings,
) -> None:
    with database.session.begin() as session:
        changed = cast(
            CursorResult,
            session.execute(
                update(Delivery)
                .where(
                    Delivery.id == row.id,
                    Delivery.status == "sending",
                    Delivery.lease_owner == owner,
                )
                .values(updated_at=stamp())
            ),
        ).rowcount
        if changed != 1:
            return
        current = required(session.get(Delivery, row.id))
        status = outcome.status
        if status == "retryable_failure":
            status = "retry_wait" if row.attempt_count < settings.mail_max_attempts else "failed"
            if status == "retry_wait":
                delay = 60 if row.attempt_count == 1 else 300
                current.next_attempt_at = stamp(now() + timedelta(seconds=delay))
        current.status = status
        current.last_error_code = outcome.code
        current.last_error_message = None if status == "accepted" else "请核对投递状态与邮箱配置"
        current.accepted_at = stamp() if status == "accepted" else None
        attempt = session.scalar(
            select(DeliveryAttempt).where(
                DeliveryAttempt.delivery_id == row.id,
                DeliveryAttempt.attempt_no == row.attempt_count,
            )
        )
        attempt = required(attempt)
        attempt.status, attempt.finished_at = outcome.status, stamp()
        attempt.error_code, attempt.smtp_code = outcome.code, outcome.smtp_code


def recover_deliveries(database: Database) -> None:
    with database.session.begin() as session:
        ids = list(
            session.scalars(
                select(Delivery.id).where(
                    Delivery.status == "sending", Delivery.lease_expires_at <= stamp()
                )
            )
        )
        if ids:
            session.execute(
                update(DeliveryAttempt)
                .where(DeliveryAttempt.delivery_id.in_(ids), DeliveryAttempt.status == "sending")
                .values(status="unknown", error_code="SMTP_RESULT_UNKNOWN", finished_at=stamp())
            )
            session.execute(
                update(Delivery)
                .where(Delivery.id.in_(ids))
                .values(status="unknown", last_error_code="SMTP_RESULT_UNKNOWN", updated_at=stamp())
            )


def retry_delivery(
    database: Database,
    user_id: str,
    delivery_id: str,
    confirm_unknown: bool,
    reason: str,
) -> dict:
    with database.session.begin() as session:
        row = required(session.get(Delivery, delivery_id))
        if row is None:
            raise AppError("NOT_FOUND", "投递不存在", 404)
        owned_brief(session, user_id, row.brief_id)
        if row.status == "unknown" and (not confirm_unknown or len(reason.strip()) < 10):
            raise AppError("CONFIRM_REQUIRED", "请先核查邮箱，明确确认可能重复发送并填写原因", 409)
        if row.status not in {"failed", "unknown"}:
            raise AppError("INVALID_STATE", "当前状态不能重试", 409)
        changed = cast(
            CursorResult,
            session.execute(
                update(Delivery)
                .where(Delivery.id == row.id, Delivery.status == row.status)
                .values(
                    status="pending",
                    next_attempt_at=stamp(),
                    retry_confirmed_by=user_id,
                    retry_confirmed_at=stamp(),
                    retry_confirmation_reason=reason or "手动重试明确失败的邮件",
                )
            ),
        ).rowcount
        if changed != 1:
            raise AppError("INVALID_STATE", "投递状态已改变，请刷新后重试", 409)
        return {"id": row.id, "status": "pending"}


def delivery_content(database: Database, row: Delivery, settings: Settings) -> EmailMessage:
    with database.session() as session:
        brief = required(session.get(Brief, row.brief_id))
        sub = required(session.get(Subscription, brief.subscription_id))
        user_id = sub.user_id
    return render_mail(brief_detail(database, user_id, row.brief_id), row, settings)
