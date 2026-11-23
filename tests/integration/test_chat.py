"""聊天、版本重生成和显式推送的离线边界检查，不发送真实邮件。"""

import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend.app.agent.tools import chat as chat_tools
from backend.app.domain.common import AppError
from backend.app.infrastructure.migrations import migrate
from backend.app.infrastructure.qq_smtp import MailResult
from backend.app.main import create_app
from backend.app.models.schema import Brief, ChatTurn, Delivery, Run, Subscription
from backend.app.service import chat
from backend.app.service.runs import create_run
from backend.app.worker.main import process_mail, process_run
from tests.integration.test_application import FeedFixture, ResearchModel, owner, token
from tests.unit.test_graph import call


def configured(settings, database):
    settings.llm_base_url = "https://example.com/v1"
    settings.llm_model = "offline-chat-test"
    settings.llm_api_key = type(settings.llm_api_key)("offline-test-key")
    from backend.app.service.model_config import ModelConfigInput, save

    save(
        database,
        settings,
        owner(database),
        ModelConfigInput(
            version=0,
            base_url="https://example.com/v1",
            model_name="offline-chat-test",
            api_key="offline-test-key",
        ),
    )
    settings.smtp_username = "sender@example.com"
    settings.smtp_auth_code = type(settings.smtp_auth_code)("offline-test-code")


@pytest.mark.asyncio
async def test_regenerate_preserves_daily_and_sends_once_when_daily_disabled(database, settings):
    configured(settings, database)
    uid = owner(database)
    create_run(database, uid, "daily")
    await process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    with database.session() as session:
        original_id = session.scalar(select(Brief.id))
    thread = chat.new_session(database, uid)
    data = chat.TurnInput(message="重新生成并推送", action="generate_send")
    ticket = chat.start_turn(database, settings, uid, thread["id"], "one", data)
    assert chat.start_turn(database, settings, uid, thread["id"], "one", data) == ticket
    with pytest.raises(AppError):
        chat.start_turn(
            database, settings, uid, thread["id"], "one", chat.TurnInput(message="不同内容")
        )
    await process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    chat.sync_turns(database)
    result = chat.turns(database, uid, thread["id"])[0]
    assert result["brief"]["edition"] == 1
    assert result["brief"]["id"] != original_id
    assert result["brief"]["items"][0]["sources"]
    with database.session() as session:
        assert session.get(Brief, original_id).edition == 0
        assert session.scalar(select(func.count()).select_from(Brief)) == 2
        assert session.scalar(select(Subscription.enabled)) == 0

    class Sender:
        def send(self, message, recipient):
            assert recipient == "reader@example.com"
            return MailResult("accepted", smtp_code=250)

    assert await process_mail(database, settings, "mail", Sender())
    assert not await process_mail(database, settings, "mail", Sender())
    assert chat.turns(database, uid, thread["id"])[0]["brief"]["delivery"]["status"] == "accepted"
    # 再次主动生成另存新版本，未选择发送时不创建邮件。
    chat.start_turn(
        database,
        settings,
        uid,
        thread["id"],
        "two",
        chat.TurnInput(message="只生成", action="generate"),
    )
    await process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    assert chat.turns(database, uid, thread["id"])[-1]["brief"]["edition"] == 2
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Delivery)) == 1


def test_chat_permission_and_unverified_send_are_atomic(database, settings):
    configured(settings, database)
    uid = owner(database)
    thread = chat.new_session(database, uid)
    with database.session.begin() as session:
        session.execute(update(Subscription).values(recipient_verified_at=None))
    with pytest.raises(AppError) as error:
        chat.start_turn(
            database,
            settings,
            uid,
            thread["id"],
            "send",
            chat.TurnInput(message="发邮件", action="generate_send"),
        )
    assert error.value.code == "RECIPIENT_NOT_VERIFIED"
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ChatTurn)) == 0
        assert session.scalar(select(func.count()).select_from(Run)) == 0
    with pytest.raises(AppError) as error:
        chat.turns(database, "another-user", thread["id"])
    assert error.value.status == 404
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/chat/sessions").status_code == 401
        response = client.post(
            f"/api/v1/chat/sessions/{thread['id']}/turns",
            headers=token(client) | {"Idempotency-Key": "blank"},
            json={"message": "  "},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_uses_readonly_tools_and_persists_running_events(
    database, settings, monkeypatch
):
    configured(settings, database)
    uid = owner(database)
    thread = chat.new_session(database, uid)
    chat.start_turn(
        database, settings, uid, thread["id"], "ask", chat.TurnInput(message="最近有什么简报？")
    )
    observed = []

    def list_probe(*args):
        events = chat.turns(database, uid, thread["id"])[0]["tools"]
        observed.extend(events)
        return []

    monkeypatch.setattr(chat_tools, "list_briefs", list_probe)

    class ChatModel:
        calls = 0

        async def complete(self, messages, tools, timeout):
            self.calls += 1
            assert "reader@example.com" not in json.dumps(messages)
            assert {item["function"]["name"] for item in tools} == {
                "list_briefs",
                "read_brief",
                "read_preferences",
            }
            if self.calls == 1:
                return {"role": "assistant", "content": None, "tool_calls": [call("list_briefs")]}
            return {"role": "assistant", "content": "还没有简报，可以选择重新生成。"}

    assert await chat.process_chat(database, settings, "chat-worker", ChatModel())
    assert observed[0]["status"] == "running"
    turn = chat.turns(database, uid, thread["id"])[0]
    assert turn["status"] == "succeeded"
    assert turn["tools"][0]["status"] == "succeeded"
    assert turn["answer"] == "还没有简报，可以选择重新生成。"
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Delivery)) == 0


@pytest.mark.asyncio
async def test_v2_upgrade_preserves_briefs_sources_and_deliveries(tmp_path, settings):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ("001_initial.sql", "002_run_sources.sql"):
        shutil.copy(settings.root / "database/migrations" / name, migrations / name)
    settings.database_url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    assert migrate(settings.database_url, migrations) == 2
    # 用 SQL 构造真实旧表数据，避免依赖新增 ORM 列。
    with sqlite3.connect(tmp_path / "upgrade.db") as c:
        c.execute("INSERT INTO users VALUES ('u','u@example.com','hash',1,'t','t')")
        c.execute("""
            INSERT INTO subscriptions (id,user_id,persona,recipient_email,created_at,updated_at)
            VALUES ('s','u','开发者','u@example.com','t','t')
        """)
        c.execute("""
            INSERT INTO runs
            (id,subscription_id,business_date,trigger_type,idempotency_key,snapshot_json,window_start,window_end,created_at,updated_at)
            VALUES ('r','s','2026-11-23','manual','k','{}','a','b','t','t')
        """)
        c.execute("""
            INSERT INTO briefs VALUES ('b','s','2026-11-23','r','标题','概览','正文',0,'a','b','t')
        """)
        c.execute("INSERT INTO brief_items VALUES ('i','b',1,'标题','摘要','相关性','event')")
        c.execute("""
            INSERT INTO articles VALUES
            ('a','https://example.com/','https://example.com/','来源','test','t','t','正文',NULL,'full_text','t')
        """)
        c.execute("INSERT INTO item_sources VALUES ('i','a','full_text')")
        c.execute("""
            INSERT INTO deliveries
            (id,brief_id,recipient_email,message_id,next_attempt_at,created_at,updated_at) VALUES
            ('d','b','u@example.com','<test@example.com>','t','t','t')
        """)
    assert migrate(settings.database_url, settings.root / "database/migrations") == 4
    assert migrate(settings.database_url, settings.root / "database/migrations") == 4
    with sqlite3.connect(tmp_path / "upgrade.db") as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("SELECT edition,title FROM briefs").fetchone() == (0, "标题")
        assert c.execute("SELECT manual_requested FROM deliveries").fetchone() == (0,)
        assert c.execute("SELECT COUNT(*) FROM item_sources").fetchone()[0] == 1


def test_failed_foreign_key_check_rolls_back_table_rebuild(tmp_path, settings):
    migrations = tmp_path / "rollback-migrations"
    migrations.mkdir()
    for name in ("001_initial.sql", "002_run_sources.sql"):
        shutil.copy(settings.root / "database/migrations" / name, migrations / name)
    url = f"sqlite:///{tmp_path / 'rollback.db'}"
    assert migrate(url, migrations) == 2
    shutil.copy(settings.root / "database/migrations/003_chat_and_editions.sql", migrations)
    (migrations / "004_invalid.sql").write_text(
        "INSERT INTO chat_sessions VALUES ('c','unknown-user','标题','t','t');\n"
        "INSERT INTO schema_migrations VALUES (4,'t');\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Foreign key validation failed"):
        migrate(url, migrations)
    with sqlite3.connect(tmp_path / "rollback.db") as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
        assert "edition" not in {row[1] for row in connection.execute("PRAGMA table_info(briefs)")}
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='chat_sessions'"
            ).fetchone()
            is None
        )


def test_expired_chat_closes_running_tool_and_allows_next_turn(database, settings):
    configured(settings, database)
    uid = owner(database)
    thread = chat.new_session(database, uid)
    chat.start_turn(database, settings, uid, thread["id"], "old", chat.TurnInput(message="旧问题"))
    with database.session.begin() as session:
        row = session.scalar(select(ChatTurn))
        row.status, row.lease_expires_at = "running", "2020-01-01T00:00:00.000Z"
        row.events_json = json.dumps([{"status": "running", "tool_name": "read_brief"}])
    chat.sync_turns(database)
    with database.session() as session:
        row = session.scalar(select(ChatTurn))
        assert row.status == "failed" and row.error_code == "TURN_EXPIRED"
        assert json.loads(row.events_json)[0]["status"] == "failed"
    ticket = chat.start_turn(
        database, settings, uid, thread["id"], "new", chat.TurnInput(message="新问题")
    )
    assert ticket["id"]
