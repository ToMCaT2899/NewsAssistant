"""独立工作进程：调度任务、执行一次生成，再处理一条邮件投递。"""

import asyncio
import json
import logging
import signal
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from backend.app.agent.graph.runner import AgentRunner
from backend.app.agent.tools.files import FileTools
from backend.app.agent.tools.news import NewsTools
from backend.app.agent.tools.registry import Registry
from backend.app.conf.settings import Settings, get_settings
from backend.app.domain.common import AppError, identifier, now, stamp
from backend.app.domain.schedule import local_due, next_due
from backend.app.domain.schemas import BriefDraft
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.llm import ChatCompletionsClient, ModelClient
from backend.app.infrastructure.qq_smtp import MailResult, QQSender
from backend.app.models.schema import Subscription
from backend.app.service.chat import process_chat
from backend.app.service.deliveries import (
    claim_delivery,
    delivery_content,
    finish_delivery,
    recover_deliveries,
)
from backend.app.service.runs import (
    audit_tool,
    claim_run,
    create_run,
    finish_run,
    publish,
    recover_expired,
)

logger = logging.getLogger(__name__)


def schedule_due(database: Database) -> None:
    current = now()
    with database.session() as session:
        subscriptions = list(
            session.scalars(
                select(Subscription).where(
                    Subscription.enabled == 1, Subscription.next_run_at <= stamp(current)
                )
            )
        )
    for sub in subscriptions:
        day = current.astimezone(ZoneInfo(sub.timezone)).date()
        try:
            if local_due(day, sub.local_send_time, sub.timezone) <= current:
                create_run(
                    database,
                    sub.user_id,
                    day.isoformat(),
                    trigger="scheduled",
                    expected_version=sub.version,
                )
        except AppError as exc:
            if exc.code not in {"TASK_CONFLICT", "SUBSCRIPTION_CHANGED"}:
                logger.warning("Schedule failure: %s", exc.code)
        with database.session.begin() as session:
            session.execute(
                update(Subscription)
                .where(Subscription.id == sub.id, Subscription.version == sub.version)
                .values(next_run_at=stamp(next_due(current, sub.local_send_time, sub.timezone)))
            )


async def process_run(
    database: Database,
    settings: Settings,
    owner: str,
    model: ModelClient | None = None,
    reader=None,
) -> bool:
    run = claim_run(database, owner, settings)
    if run is None:
        return False
    result: dict = {}
    try:
        news = NewsTools(database, run, settings, reader)
        files = FileTools(settings.agent_workspace_root / run.id, settings.shell_tool_enabled)
        runner = AgentRunner(
            model or ChatCompletionsClient(settings),
            Registry([*files.definitions(), *news.definitions()]),
            settings,
            news.validate_sources,
            lambda event: audit_tool(database, run.id, event),
        )
        snapshot = json.loads(run.snapshot_json)
        preferences = {
            key: snapshot[key]
            for key in (
                "persona",
                "topics",
                "include_keywords",
                "exclude_keywords",
                "max_items",
                "lookback_hours",
                "timezone",
            )
        }
        result = dict(
            await runner.run(
                {
                    "subscription": preferences,
                    **news.available(),
                    "business_date": run.business_date,
                    "user_instruction": snapshot.get("_instruction", ""),
                    "regenerate": snapshot.get("_regenerate", False),
                }
            )
        )
        if result.get("result") and not result.get("error"):
            publish(
                database, run.id, owner, BriefDraft.model_validate(result["result"]), dict(result)
            )
        else:
            finish_run(database, run.id, owner, dict(result))
    except AppError as exc:
        finish_run(database, run.id, owner, {**result, "error": exc.code})
    except Exception as exc:
        # 仅记录异常类型，外部异常信息可能包含令牌和响应正文。
        logger.error("Run %s failed: %s", run.id, type(exc).__name__)
        finish_run(database, run.id, owner, {**result, "error": "RUN_FAILED"})
    return True


async def process_mail(database: Database, settings: Settings, owner: str, sender=None) -> bool:
    row = claim_delivery(database, owner, settings)
    if row is None:
        return False
    try:
        message = delivery_content(database, row, settings)
    except Exception:
        finish_delivery(database, row, owner, MailResult("failed", "MAIL_RENDER_FAILED"), settings)
        return True
    try:
        outcome = await asyncio.to_thread(
            (sender or QQSender(settings)).send, message, row.recipient_email
        )
    except Exception:
        # 不能根据未预期异常就认定 SMTP 尚未接收邮件。
        outcome = MailResult("unknown", "SMTP_RESULT_UNKNOWN")
    finish_delivery(database, row, owner, outcome, settings)
    return True


async def run_worker() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    owner = identifier()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except NotImplementedError:
            pass
    try:
        while not stopped.is_set():
            (Path(tempfile.gettempdir()) / "news-worker-heartbeat").write_text(
                str(time.time()), encoding="utf-8"
            )
            recover_expired(database)
            recover_deliveries(database)
            schedule_due(database)
            did_work = await process_run(database, settings, owner)
            did_work = await process_chat(database, settings, owner) or did_work
            did_work = await process_mail(database, settings, owner) or did_work
            if not did_work:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        database.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
