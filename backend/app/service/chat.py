"""持久化对话与任务入口，聊天工具只读，生成和发信由显式操作触发。"""

import json
from datetime import timedelta
from typing import Literal, cast

from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from backend.app.agent.graph.runner import AgentRunner
from backend.app.agent.tools.chat import ChatTools
from backend.app.agent.tools.registry import Registry
from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, now, required, stamp
from backend.app.domain.schemas import Contract
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.llm import ChatCompletionsClient, ModelClient
from backend.app.models.schema import ChatSession, ChatTurn, Run
from backend.app.service.briefs import brief_detail
from backend.app.service.model_config import require_config, resolve
from backend.app.service.runs import create_run_in_session, get_run, list_tool_calls


class TurnInput(Contract):
    message: str = Field(min_length=1, max_length=2000)
    action: Literal["chat", "generate", "generate_send"] = "chat"


def owned_session(session, user_id: str, session_id: str) -> ChatSession:
    row = session.scalar(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    if row is None:
        raise AppError("NOT_FOUND", "对话不存在", 404)
    return row


def sessions(database: Database, user_id: str) -> list[dict]:
    with database.session() as session:
        return [
            {"id": row.id, "title": row.title, "created_at": row.created_at}
            for row in session.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc())
                .limit(30)
            )
        ]


def new_session(database: Database, user_id: str) -> dict:
    with database.session.begin() as session:
        row = ChatSession(user_id=user_id, title="新的新闻对话")
        session.add(row)
        session.flush()
        return {"id": row.id, "title": row.title, "created_at": row.created_at}


def start_turn(
    database: Database, settings: Settings, user_id: str, session_id: str, key: str, data: TurnInput
) -> dict:
    if not data.message.strip():
        raise AppError("INVALID_INPUT", "请输入消息", 422)
    require_config(database, settings, user_id)
    try:
        with database.session.begin() as session:
            thread = owned_session(session, user_id, session_id)
            existing = session.scalar(
                select(ChatTurn).where(
                    ChatTurn.session_id == session_id, ChatTurn.idempotency_key == key
                )
            )
            if existing:
                if existing.question != data.message or existing.action != data.action:
                    raise AppError("IDEMPOTENCY_CONFLICT", "重试请求内容发生变化，请刷新对话", 409)
                return {"id": existing.id}
            if session.scalar(
                select(ChatTurn.id).where(
                    ChatTurn.session_id == session_id, ChatTurn.status.in_(["queued", "running"])
                )
            ):
                raise AppError("TURN_ACTIVE", "请等待上一条消息处理完成", 409)
            turn = ChatTurn(
                session_id=session_id,
                idempotency_key=key,
                question=data.message,
                action=data.action,
            )
            session.add(turn)
            session.flush()
            if data.action != "chat":
                ticket = create_run_in_session(
                    session,
                    user_id,
                    "chat:" + turn.id,
                    settings=settings,
                    regenerate=True,
                    send_mail=data.action == "generate_send",
                    instruction=data.message,
                )
                turn.run_id = ticket["run_id"]
            thread.title = data.message[:40]
            thread.updated_at = stamp()
            return {"id": turn.id}
    except IntegrityError as exc:
        raise AppError("TURN_ACTIVE", "对话正在处理，请刷新后重试", 409) from exc


def turns(database: Database, user_id: str, session_id: str) -> list[dict]:
    with database.session() as session:
        owned_session(session, user_id, session_id)
        rows = list(
            session.scalars(
                select(ChatTurn)
                .where(ChatTurn.session_id == session_id)
                .order_by(ChatTurn.created_at.desc())
                .limit(40)
            )
        )
    result = []
    for row in reversed(rows):
        item = {
            "id": row.id,
            "question": row.question,
            "answer": row.answer,
            "action": row.action,
            "status": row.status,
            "error_code": row.error_code,
            "created_at": row.created_at,
            "run_id": row.run_id,
            "tools": json.loads(row.events_json),
            "brief": None,
        }
        if row.run_id:
            run = get_run(database, user_id, row.run_id)
            item["status"] = (
                "failed" if run["status"] in {"incomplete", "cancelled"} else run["status"]
            )
            item["error_code"] = run["error_code"]
            item["tools"] = list_tool_calls(database, user_id, row.run_id, 100, 0)
            if run["brief_id"]:
                item["brief"] = brief_detail(database, user_id, run["brief_id"])
                item["answer"] = "新的简报已生成。" + (
                    "邮件状态请查看下方投递结果。"
                    if row.action == "generate_send"
                    else "可在此继续追问，或生成并推送新版本。"
                )
        result.append(item)
    return result


def sync_turns(database: Database) -> None:
    with database.session.begin() as session:
        expired = list(
            session.scalars(
                select(ChatTurn).where(
                    ChatTurn.status == "running",
                    ChatTurn.run_id.is_(None),
                    ChatTurn.lease_expires_at <= stamp(),
                )
            )
        )
        for turn in expired:
            turn.status, turn.error_code, turn.updated_at = "failed", "TURN_EXPIRED", stamp()
            events = json.loads(turn.events_json)
            for event in events:
                if event["status"] == "running":
                    event.update(status="failed", error_code="TOOL_INTERRUPTED")
            turn.events_json = json.dumps(events)
        pairs = session.execute(
            select(ChatTurn, Run)
            .join(Run, ChatTurn.run_id == Run.id)
            .where(ChatTurn.status.in_(["queued", "running"]))
        ).all()
        for turn, run in pairs:
            if run.status not in {"queued", "running"}:
                turn.status = "succeeded" if run.status == "succeeded" else "failed"
                turn.error_code, turn.updated_at = run.error_code, stamp()


async def process_chat(
    database: Database, settings: Settings, owner: str, model: ModelClient | None = None
) -> bool:
    sync_turns(database)
    with database.session.begin() as session:
        turn_id = session.scalar(
            select(ChatTurn.id)
            .where(ChatTurn.status == "queued", ChatTurn.action == "chat")
            .order_by(ChatTurn.created_at)
            .limit(1)
        )
        if not turn_id:
            return False
        claimed = session.execute(
            update(ChatTurn)
            .where(ChatTurn.id == turn_id, ChatTurn.status == "queued")
            .values(
                status="running",
                lease_owner=owner,
                lease_expires_at=stamp(now() + timedelta(seconds=settings.agent_max_seconds + 30)),
            )
        )
        if cast(CursorResult, claimed).rowcount != 1:
            return False
        turn = required(session.get(ChatTurn, turn_id))
        thread = required(session.get(ChatSession, turn.session_id))
        user_id = thread.user_id
        history = list(
            session.scalars(
                select(ChatTurn)
                .where(
                    ChatTurn.session_id == turn.session_id,
                    ChatTurn.status == "succeeded",
                    ChatTurn.id != turn.id,
                )
                .order_by(ChatTurn.created_at.desc())
                .limit(8)
            )
        )

    def audit(event: dict) -> None:
        with database.session.begin() as session:
            row = required(session.get(ChatTurn, turn_id))
            events = json.loads(row.events_json)
            item = {
                "id": event["event_id"],
                "sequence": len(events) + 1,
                "tool_name": event["name"],
                "arguments": {},
                "status": "running"
                if event.get("phase") == "running"
                else "succeeded"
                if event["ok"]
                else "failed",
                "duration_ms": event["duration_ms"],
                "error_code": event["error"],
            }
            previous = next((i for i, e in enumerate(events) if e["id"] == item["id"]), None)
            if previous is None:
                events.append(item)
            else:
                item["sequence"] = events[previous]["sequence"]
                events[previous] = item
            row.events_json = json.dumps(events)

    def finalize(text: str) -> dict:
        if not text.strip() or len(text) > 20000:
            raise ValueError("回答为空或过长")
        return {"answer": text}

    prompt = (settings.root / "backend/app/agent/prompts/chat.txt").read_text(encoding="utf-8")
    try:
        if model is None:
            settings = resolve(database, settings, user_id)
        state = await AgentRunner(
            model or ChatCompletionsClient(settings),
            Registry(ChatTools(database, user_id).definitions()),
            settings,
            lambda _: None,
            audit,
            system_prompt=prompt,
            finalize=finalize,
        ).run(
            {
                "question": turn.question,
                "history": [
                    {"question": row.question, "answer": row.answer} for row in reversed(history)
                ],
            }
        )
        error = state.get("error") or None
        answer = state.get("result", {}).get("answer")
    except Exception:
        error, answer = "CHAT_FAILED", None
    with database.session.begin() as session:
        current = required(session.get(ChatTurn, turn_id))
        if error and current.status == "running" and current.lease_owner == owner:
            events = json.loads(current.events_json)
            for event in events:
                if event["status"] == "running":
                    event.update(status="failed", error_code="TOOL_INTERRUPTED")
            current.events_json = json.dumps(events)
        session.execute(
            update(ChatTurn)
            .where(
                ChatTurn.id == turn_id,
                ChatTurn.status == "running",
                ChatTurn.lease_owner == owner,
                ChatTurn.lease_expires_at > stamp(),
            )
            .values(
                status="failed" if error else "succeeded",
                answer=answer,
                error_code=error,
                updated_at=stamp(),
            )
        )
    return True
