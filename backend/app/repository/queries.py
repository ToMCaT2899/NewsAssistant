"""Ownership-scoped reads. No commits in this module."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domain.common import AppError
from backend.app.models.schema import Brief, Run, Subscription


def subscription_for(session: Session, user_id: str) -> Subscription:
    row = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if row is None:
        raise AppError("NOT_FOUND", "请先由管理员初始化账号", 404)
    return row


def owned_run(session: Session, user_id: str, run_id: str) -> Run:
    row = session.scalar(
        select(Run)
        .join(Subscription, Run.subscription_id == Subscription.id)
        .where(Run.id == run_id, Subscription.user_id == user_id)
    )
    if row is None:
        raise AppError("NOT_FOUND", "任务不存在", 404)
    return row


def owned_brief(session: Session, user_id: str, brief_id: str) -> Brief:
    row = session.scalar(
        select(Brief)
        .join(Subscription, Brief.subscription_id == Subscription.id)
        .where(Brief.id == brief_id, Subscription.user_id == user_id)
    )
    if row is None:
        raise AppError("NOT_FOUND", "简报不存在", 404)
    return row
