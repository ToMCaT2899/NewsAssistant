"""使用真实外部服务验收生成与单次投递，隔离数据库且不启动每日调度。"""

import argparse
import asyncio
import json
import secrets
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.conf.settings import get_settings
from backend.app.domain.common import identifier, required, stamp
from backend.app.domain.schemas import SubscriptionInput
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.migrations import migrate
from backend.app.models.schema import Brief, Delivery, DeliveryAttempt, Run, ToolCall, User
from backend.app.service.accounts import (
    bootstrap,
    read_subscription,
    save_subscription,
    verify_recipient,
)
from backend.app.service.briefs import brief_detail
from backend.app.service.model_config import ModelConfigInput, save
from backend.app.service.runs import create_run
from backend.app.worker.main import process_mail, process_run


async def accept(send_mail: bool, extra_sources: list[str], require_news: bool) -> dict:
    settings = get_settings().model_copy(deep=True)
    if not settings.model_configured or (send_mail and not settings.mail_configured):
        raise ValueError("缺少真实模型或邮件配置")
    # 仅自发自收，不能通过参数向任意第三方发送验收邮件。
    recipient = settings.smtp_username
    # 验收不依赖生产管理员账号，也不覆盖用户的本地账号配置。
    settings.admin_email = recipient if send_mail else "acceptance@example.com"
    settings.admin_password = type(settings.admin_password)(secrets.token_urlsafe(32))
    sources = json.loads(settings.rss_sources_json)
    for item in extra_sources:
        name, url = item.split("=", 1)
        sources[name] = url
    settings.rss_sources_json = json.dumps(sources)
    with tempfile.TemporaryDirectory(prefix="news-live-acceptance-") as folder:
        root = Path(folder)
        settings.database_url = "sqlite:///" + (root / "acceptance.db").as_posix()
        settings.agent_workspace_root = root / "workspace"
        migrate(settings.database_url, settings.root / "database/migrations")
        database = Database(settings.database_url)
        try:
            bootstrap(database, settings)
            with database.session() as session:
                user = required(session.scalar(select(User)))
                user_id, email = user.id, user.email
            if not settings.model_encryption_key.get_secret_value():
                settings.model_encryption_key = SecretStr(Fernet.generate_key().decode())
            save(
                database,
                settings,
                user_id,
                ModelConfigInput(
                    version=0,
                    base_url=settings.llm_base_url,
                    model_name=settings.llm_model,
                    api_key=settings.llm_api_key,
                ),
            )
            if send_mail:
                verify_recipient(database, email, recipient)
            subscription = read_subscription(database, user_id)
            save_subscription(
                database,
                user_id,
                SubscriptionInput(
                    version=subscription["version"],
                    persona="关注 AI 模型、开源工具和应用开发的开发者",
                    topics=["AI", "大模型", "开源"],
                    recipient_email=recipient if send_mail else subscription["recipient_email"],
                    max_items=3,
                    lookback_hours=72,
                    enabled=send_mail,
                ),
                settings,
            )
            ticket = create_run(
                database, user_id, identifier(), require_model=True, settings=settings
            )
            print(json.dumps({"phase": "real_generation_started", "at": stamp()}), flush=True)
            await process_run(database, settings, "live-acceptance")
            with database.session() as session:
                run = required(session.get(Run, ticket["run_id"]))
                report = {
                    "at": stamp(),
                    "run_id": run.id,
                    "status": run.status,
                    "error_code": run.error_code,
                    "model": settings.llm_model,
                    "source_ids": list(sources),
                    "model_calls": run.model_calls,
                    "tool_calls": run.tool_calls,
                    "usage": json.loads(run.token_usage_json or "{}"),
                    "window_start": run.window_start,
                    "window_end": run.window_end,
                    "tools": [
                        {"name": tool.tool_name, "status": tool.status, "error": tool.error_code}
                        for tool in session.scalars(
                            select(ToolCall)
                            .where(ToolCall.run_id == run.id)
                            .order_by(ToolCall.sequence)
                        )
                    ],
                }
                brief = session.scalar(select(Brief).where(Brief.run_id == run.id))
                brief_id = brief.id if brief else None
            if brief_id:
                detail = brief_detail(database, user_id, brief_id)
                report["brief"] = {
                    key: detail[key] for key in ("title", "overview", "no_news", "items")
                }
            has_news = bool(report.get("brief", {}).get("items"))
            report["news_content_present"] = has_news
            if send_mail and brief_id and (has_news or not require_news):
                print(json.dumps({"phase": "real_mail_started", "at": stamp()}), flush=True)
                # 只尝试一次；状态不确定时不得通过重跑循环自动重发。
                await process_mail(database, settings, "live-acceptance")
                with database.session() as session:
                    delivery = required(session.scalar(select(Delivery)))
                    attempt = session.scalar(select(DeliveryAttempt))
                    report["mail"] = {
                        "status": delivery.status,
                        "attempt_count": delivery.attempt_count,
                        "smtp_code": attempt.smtp_code if attempt else None,
                        "error_code": delivery.last_error_code,
                        "message_id": delivery.message_id,
                        "accepted_at": delivery.accepted_at,
                        "recipient": "configured_smtp_sender_self",
                        "inbox_receipt_verified": False,
                    }
            report["finished_at"] = stamp()
            return report
        finally:
            database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-mail", action="store_true", help="向配置的发件邮箱自发自收一次")
    parser.add_argument(
        "--extra-source", action="append", default=[], help="仅为本次验收补充 RSS，格式为名称=URL"
    )
    parser.add_argument(
        "--require-news", action="store_true", help="必须有真实新闻条目才通过，空简报不发送"
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(accept(args.send_mail, args.extra_source, args.require_news))
    except Exception as exc:
        # 外部异常可能包含敏感响应，仅输出异常类型。
        print(json.dumps({"status": "acceptance_error", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False), flush=True)
    mail_ok = not args.send_mail or report.get("mail", {}).get("status") == "accepted"
    news_ok = not args.require_news or report["news_content_present"]
    raise SystemExit(0 if report["status"] == "succeeded" and mail_ok and news_ok else 1)


if __name__ == "__main__":
    main()
