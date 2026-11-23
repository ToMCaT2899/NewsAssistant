"""API、持久化和工作进程的离线测试，不调用真实模型或 SMTP。"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend.app.domain.common import AppError, now, stamp
from backend.app.domain.schemas import BriefDraft
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.migrations import migrate
from backend.app.infrastructure.qq_smtp import MailResult
from backend.app.main import create_app
from backend.app.models.schema import (
    Brief,
    Delivery,
    Run,
    Subscription,
    User,
)
from backend.app.service.deliveries import (
    claim_delivery,
    recover_deliveries,
    retry_delivery,
)
from backend.app.service.runs import create_run, publish
from backend.app.worker.main import process_mail, process_run
from tests.unit.test_graph import call


def owner(database):
    with database.session() as session:
        return session.scalar(select(User.id))


def token(client):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@example.com",
            "password": "Offline-test-password-2026",
        },
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_auth_subscription_version_and_missing_model(database, settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/subscription").status_code == 401
        headers = token(client)
        subscription = client.get("/api/v1/subscription", headers=headers).json()
        for name in ("id", "recipient_verified", "next_run_at"):
            subscription.pop(name)
        subscription["topics"] = ["AI"]
        response = client.put("/api/v1/subscription", headers=headers, json=subscription)
        assert response.status_code == 200
        assert response.json()["version"] == 2
        assert (
            client.put("/api/v1/subscription", headers=headers, json=subscription).status_code
            == 409
        )
        response = client.post("/api/v1/runs", headers=headers | {"Idempotency-Key": "test"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert client.get("/api/v1/runs/missing", headers=headers).status_code == 404
        assert client.get("/ready").json()["schema_version"] == 4


class FeedFixture:
    """显式注入的离线 RSS 替身，不用于应用默认运行路径。"""

    async def get(self, url, timeout=15):
        date = (now() - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        return f"""<?xml version="1.0"?><rss version="2.0"><channel>
        <title>Offline fixture</title><link>https://example.com/</link><description>Fixture</description>
        <item><title>Open model release</title><link>https://example.com/news?id=42</link>
        <pubDate>{date}</pubDate><description>AI model tools</description></item>
        </channel></rss>""".encode()


class ResearchModel:
    def __init__(self):
        self.count = 0

    async def complete(self, messages, tools, timeout):
        self.count += 1
        assert "reader@example.com" not in messages[1]["content"]
        if self.count == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [call("fetch_feed", args='{"source_id":"fixture"}')],
            }
        observation = json.loads(messages[-1]["content"])
        article = observation["data"][0]
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "title": "开发者 AI 简报",
                    "overview": "离线夹具生成，不是真实新闻验收",
                    "no_news": False,
                    "items": [
                        {
                            "headline": article["title"],
                            "summary": "测试摘要",
                            "why_it_matters": "测试开发者相关性",
                            "event_key": "fixture-release",
                            "sources": [
                                {
                                    "article_id": article["article_id"],
                                    "evidence_level": "search_excerpt",
                                }
                            ],
                        }
                    ],
                }
            ),
        }


@pytest.mark.asyncio
async def test_offline_run_publication_delivery_and_daily_idempotency(database, settings):
    uid = owner(database)
    with database.session.begin() as session:
        sub = session.scalar(select(Subscription))
        sub.enabled = 1
    ticket = create_run(database, uid, "same")
    assert create_run(database, uid, "same")["run_id"] == ticket["run_id"]
    assert await process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    with database.session() as session:
        run = session.get(Run, ticket["run_id"])
        assert run.status == "succeeded"
        assert run.model_calls == 2 and run.tool_calls == 1
        brief = session.scalar(select(Brief))
        assert session.scalar(select(func.count()).select_from(Delivery)) == 1
        brief_id = brief.id
    assert create_run(database, uid, "different")["brief_id"] == brief_id

    class FakeSender:
        def send(self, message, recipient):
            assert message["Message-ID"]
            assert recipient == "reader@example.com"
            return MailResult("accepted", smtp_code=250)

    assert await process_mail(database, settings, "mail-worker", FakeSender())
    assert not await process_mail(database, settings, "mail-worker", FakeSender())
    with database.session() as session:
        assert session.scalar(select(Delivery)).status == "accepted"
    with TestClient(create_app(settings)) as client:
        detail = client.get("/api/v1/briefs/" + brief_id, headers=token(client)).json()
        assert detail["items"][0]["sources"][0]["url"] == "https://example.com/news?id=42"
        assert detail["delivery"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_unknown_sending_lease_does_not_auto_resend(database, settings):
    uid = owner(database)
    with database.session.begin() as session:
        session.scalar(select(Subscription)).enabled = 1
    create_run(database, uid, "unknown")
    await process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    row = claim_delivery(database, "crashed-mail-worker", settings)
    with database.session.begin() as session:
        session.execute(
            update(Delivery)
            .where(Delivery.id == row.id)
            .values(lease_expires_at=stamp(now() - timedelta(seconds=1)))
        )
    recover_deliveries(database)
    assert claim_delivery(database, "new-worker", settings) is None
    with pytest.raises(AppError):
        retry_delivery(database, uid, row.id, False, "")
    retry_delivery(database, uid, row.id, True, "已检查测试发件箱，明确允许重发")
    assert claim_delivery(database, "new-worker", settings).attempt_count == 2


@pytest.mark.asyncio
async def test_invalid_reference_rolls_back_brief_and_outbox(database, settings):
    uid = owner(database)
    ticket = create_run(database, uid, "invalid")
    from backend.app.service.runs import claim_run

    claim_run(database, "worker", settings)
    draft = BriefDraft.model_validate(
        {
            "title": "invalid",
            "overview": "test",
            "items": [
                {
                    "headline": "x",
                    "summary": "x",
                    "why_it_matters": "x",
                    "event_key": "x",
                    "sources": [{"article_id": "not-visible", "evidence_level": "full_text"}],
                }
            ],
        }
    )
    with pytest.raises(AppError):
        publish(database, ticket["run_id"], "worker", draft, {"model_calls": 1, "tool_calls": 0})
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Brief)) == 0
        assert session.scalar(select(func.count()).select_from(Delivery)) == 0


def test_second_user_cannot_read_first_user_task(database, settings):
    from backend.app.infrastructure.security import password_hash, token_for

    ticket = create_run(database, owner(database), "private")
    with database.session.begin() as session:
        user = User(
            email="other@example.com", password_hash=password_hash.hash("offline-other-pass")
        )
        session.add(user)
        session.flush()
        other_id = user.id
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token_for(other_id, settings)}
        assert client.get(f"/api/v1/runs/{ticket['run_id']}", headers=headers).status_code == 404
        assert (
            client.get(f"/api/v1/runs/{ticket['run_id']}/tool-calls", headers=headers).status_code
            == 404
        )


def test_v1_migration_keeps_existing_account(settings):
    import sqlite3

    path = settings.database_url.removeprefix("sqlite:///")
    with sqlite3.connect(path) as connection:
        sql = (settings.root / "database/migrations/001_initial.sql").read_text(encoding="utf-8")
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?)",
            ("legacy", "legacy@example.com", "legacy-hash", 1, stamp(), stamp()),
        )
    assert migrate(settings.database_url, settings.root / "database/migrations") == 4
    assert migrate(settings.database_url, settings.root / "database/migrations") == 4
    db = Database(settings.database_url)
    with db.session() as session:
        assert session.get(User, "legacy").password_hash == "legacy-hash"
    db.close()


@pytest.mark.asyncio
async def test_publication_failure_preserves_actual_execution_counters(
    database, settings, monkeypatch
):
    import backend.app.worker.main as worker

    ticket = create_run(database, owner(database), "late-publication")

    def fail_publish(*args):
        raise AppError("LEASE_LOST", "late result")

    monkeypatch.setattr(worker, "publish", fail_publish)
    await worker.process_run(database, settings, "worker", ResearchModel(), FeedFixture())
    with database.session() as session:
        run = session.get(Run, ticket["run_id"])
        assert run.status == "incomplete"
        assert run.model_calls == 2 and run.tool_calls == 1
        assert session.scalar(select(func.count()).select_from(Brief)) == 0
