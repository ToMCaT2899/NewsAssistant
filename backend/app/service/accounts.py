"""Account bootstrap and versioned subscription updates."""

import json
from typing import cast

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, now, stamp
from backend.app.domain.schedule import next_due
from backend.app.domain.schemas import SubscriptionInput
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.security import password_hash, token_for, user_from_token
from backend.app.models.schema import Brief, Delivery, Subscription, User
from backend.app.repository.queries import subscription_for, user_for


def subscription_view(row: Subscription) -> dict:
    return {
        "id": row.id,
        "version": row.version,
        "persona": row.persona,
        "topics": json.loads(row.topics_json),
        "include_keywords": json.loads(row.include_keywords_json),
        "exclude_keywords": json.loads(row.exclude_keywords_json),
        "recipient_email": row.recipient_email,
        "recipient_verified": bool(row.recipient_verified_at),
        "timezone": row.timezone,
        "local_send_time": row.local_send_time,
        "max_items": row.max_items,
        "lookback_hours": row.lookback_hours,
        "enabled": bool(row.enabled),
        "next_run_at": row.next_run_at,
    }


def bootstrap(database: Database, settings: Settings) -> bool:
    if not settings.admin_email or not settings.admin_password.get_secret_value():
        return False
    email = str(TypeAdapter(EmailStr).validate_python(settings.admin_email)).lower()
    password = settings.admin_password.get_secret_value()
    if not 12 <= len(password) <= 256:
        raise ValueError("ADMIN_PASSWORD must contain 12 to 256 characters")
    with database.session.begin() as session:
        if session.scalar(select(User).where(User.email == email)):
            return False
        user = User(email=email, password_hash=password_hash.hash(password))
        session.add(user)
        session.flush()
        recipient = settings.verified_recipient_email or email
        TypeAdapter(EmailStr).validate_python(recipient)
        session.add(
            Subscription(
                user_id=user.id,
                recipient_email=recipient,
                recipient_verified_at=stamp() if settings.verified_recipient_email else None,
            )
        )
    return True


def login(database: Database, settings: Settings, email: str, password: str) -> str:
    with database.session() as session:
        user = session.scalar(select(User).where(User.email == email.lower(), User.enabled == 1))
        # Hash verification uses the same expensive path for unknown users.
        target = user.password_hash if user else password_hash.hash("unknown-user-dummy")
        try:
            valid = password_hash.verify(password, target)
        except Exception:
            valid = False
        if user is None or not valid:
            raise AppError("INVALID_LOGIN", "邮箱或密码不正确", 401)
        return token_for(user.id, settings)


def save_subscription(
    database: Database,
    user_id: str,
    data: SubscriptionInput,
    settings: Settings,
) -> dict:
    with database.session.begin() as session:
        row = subscription_for(session, user_id)
        verified = (
            row.recipient_verified_at if row.recipient_email == str(data.recipient_email) else None
        )
        if settings.verified_recipient_email.casefold() == str(data.recipient_email).casefold():
            verified = stamp()
        if data.enabled and not verified:
            raise AppError("RECIPIENT_NOT_VERIFIED", "请先由管理员验证收件邮箱", 422)
        values = data.model_dump(
            exclude={"version", "topics", "include_keywords", "exclude_keywords"}
        )
        values.update(
            topics_json=json.dumps(data.topics, ensure_ascii=False),
            include_keywords_json=json.dumps(data.include_keywords, ensure_ascii=False),
            exclude_keywords_json=json.dumps(data.exclude_keywords, ensure_ascii=False),
            recipient_verified_at=verified,
            version=row.version + 1,
            updated_at=stamp(),
            next_run_at=stamp(next_due(now(), data.local_send_time, data.timezone))
            if data.enabled
            else None,
        )
        changed = cast(
            CursorResult,
            session.execute(
                update(Subscription)
                .where(Subscription.id == row.id, Subscription.version == data.version)
                .values(**values)
            ),
        ).rowcount
        if changed != 1:
            raise AppError("VERSION_CONFLICT", "订阅已更新，请重新加载", 409)
        if not data.enabled:
            brief_ids = select(Brief.id).where(Brief.subscription_id == row.id)
            session.execute(
                update(Delivery)
                .where(
                    Delivery.brief_id.in_(brief_ids), Delivery.status.in_(["pending", "retry_wait"])
                )
                .values(status="cancelled", updated_at=stamp())
            )
        session.refresh(row)
        return subscription_view(row)


def verify_recipient(database: Database, user_email: str, recipient: str) -> None:
    recipient = str(TypeAdapter(EmailStr).validate_python(recipient))
    with database.session.begin() as session:
        user = session.scalar(select(User).where(User.email == user_email.lower()))
        if user is None:
            raise AppError("NOT_FOUND", "账号不存在", 404)
        sub = subscription_for(session, user.id)
        sub.recipient_email, sub.recipient_verified_at = recipient, stamp()
        sub.version += 1


def read_subscription(database: Database, user_id: str) -> dict:
    with database.session() as session:
        return subscription_view(subscription_for(session, user_id))


def authenticate_token(database: Database, settings: Settings, token: str) -> str:
    user_id = user_from_token(token, settings)
    with database.session() as session:
        user = user_for(session, user_id)
        if user is None or not user.enabled:
            raise AppError("UNAUTHORIZED", "登录已失效", 401)
    return user_id
