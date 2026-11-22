"""Check repository design links, SQLite constraints, and initialization behavior."""

from __future__ import annotations

import ast
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

from init_db import initialize

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-11-22T00:00:00.000Z"
END = "2026-11-23T00:00:00.000Z"


def insert(connection: sqlite3.Connection, table: str, **values: object) -> None:
    """Identifiers here are internal constants, never user input."""
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
    )


def rejects(connection: sqlite3.Connection, label: str, operation: Callable[[], object]) -> None:
    connection.execute("SAVEPOINT rejection")
    try:
        operation()
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError(f"Missing database constraint: {label}")
    finally:
        connection.execute("ROLLBACK TO rejection")
        connection.execute("RELEASE rejection")
    print(f"PASS: {label}")


def run_row(
    connection: sqlite3.Connection, identity: str, subscription: str, **extra: object
) -> None:
    values = dict(
        id=identity,
        subscription_id=subscription,
        business_date="2026-11-22",
        trigger_type="manual",
        idempotency_key=identity,
        status="succeeded",
        snapshot_json="{}",
        window_start=STAMP,
        window_end=END,
        created_at=STAMP,
        updated_at=STAMP,
    )
    values.update(extra)
    insert(connection, "runs", **values)


def brief_row(connection: sqlite3.Connection, identity: str, run: str, **extra: object) -> None:
    values = dict(
        id=identity,
        subscription_id="s1",
        business_date="2026-11-22",
        run_id=run,
        title="Daily AI brief",
        overview="Verified fixture",
        body_markdown="News",
        window_start=STAMP,
        window_end=END,
        created_at=STAMP,
    )
    values.update(extra)
    insert(connection, "briefs", **values)


def delivery_row(
    connection: sqlite3.Connection, identity: str, brief: str, **extra: object
) -> None:
    values = dict(
        id=identity,
        brief_id=brief,
        recipient_email="reader@example.invalid",
        message_id=f"<{identity}@example.invalid>",
        next_attempt_at=STAMP,
        created_at=STAMP,
        updated_at=STAMP,
    )
    values.update(extra)
    insert(connection, "deliveries", **values)


def check_database() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        sql = (ROOT / "database/migrations/001_initial.sql").read_text(encoding="utf-8")
        connection.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        for number in (1, 2):
            insert(
                connection,
                "users",
                id=f"u{number}",
                email=f"u{number}@example.invalid",
                password_hash="fixture-not-a-real-password",
                created_at=STAMP,
                updated_at=STAMP,
            )
            insert(
                connection,
                "subscriptions",
                id=f"s{number}",
                user_id=f"u{number}",
                persona="developer",
                recipient_email=f"u{number}@example.invalid",
                created_at=STAMP,
                updated_at=STAMP,
            )
        run_row(connection, "r1", "s1")
        run_row(connection, "r2", "s1")
        run_row(connection, "other", "s2", business_date="2026-11-23")
        brief_row(connection, "b1", "r1")
        delivery_row(connection, "d1", "b1")
        rejects(
            connection,
            "one formal brief per subscription and day",
            lambda: brief_row(connection, "b2", "r2"),
        )
        rejects(
            connection,
            "brief cannot reference another subscription's run",
            lambda: brief_row(
                connection, "cross", "other", subscription_id="s1", business_date="2026-11-23"
            ),
        )
        rejects(
            connection, "one email delivery per brief", lambda: delivery_row(connection, "d2", "b1")
        )
        rejects(
            connection,
            "invalid delivery state",
            lambda: connection.execute("UPDATE deliveries SET status='delivered'"),
        )
        rejects(
            connection,
            "unverified recipient cannot enable subscription",
            lambda: connection.execute("UPDATE subscriptions SET enabled=1 WHERE id='s1'"),
        )
        rejects(
            connection,
            "unknown foreign key",
            lambda: insert(
                connection,
                "item_sources",
                item_id="missing",
                article_id="missing",
                evidence_level="full_text",
            ),
        )
        rejects(
            connection,
            "idempotency key cannot be reused",
            lambda: run_row(connection, "duplicate", "s1", idempotency_key="r1"),
        )
        run_row(connection, "active", "s1", status="queued")
        rejects(
            connection,
            "only one active run per subscription",
            lambda: run_row(connection, "active2", "s1", status="running"),
        )
        rejects(
            connection,
            "negative budget counters rejected",
            lambda: connection.execute("UPDATE runs SET tool_calls=-1 WHERE id='r1'"),
        )
        connection.execute("UPDATE runs SET status='cancelled' WHERE id='active'")
        run_row(connection, "rollback", "s1", business_date="2026-11-23")
        connection.commit()
        try:
            connection.execute("BEGIN")
            brief_row(connection, "rollback-brief", "rollback", business_date="2026-11-23")
            delivery_row(
                connection,
                "rollback-delivery",
                "rollback-brief",
                message_id="<d1@example.invalid>",
            )
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("Expected outbox failure")
        if connection.execute("SELECT 1 FROM briefs WHERE id='rollback-brief'").fetchone():
            raise AssertionError("Brief and outbox were not atomic")
        print("PASS: brief and outbox rollback together")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "sample.db"
        initialize(database)
        before = database.read_bytes()
        try:
            initialize(database)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Initializer overwrote existing database")
        assert database.read_bytes() == before
        print("PASS: initializer creates schema and refuses overwrite")


def check_documents() -> None:
    count = 0
    documents = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [
            name
            for name in children
            if name
            not in {
                ".git",
                ".venv",
                "node_modules",
                "artifacts",
                "dist",
                "test-results",
                "playwright-report",
            }
        ]
        documents.extend(Path(directory) / name for name in files if name.endswith(".md"))
    for document in documents:
        content = document.read_text(encoding="utf-8")
        assert content.count("```") % 2 == 0, f"Unclosed code fence: {document}"
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", content):
            if "://" in target or target.startswith("#"):
                continue
            target_path = target.split("#", 1)[0]
            assert (document.parent / target_path).is_file(), (document, target)
            count += 1
    for script in (ROOT / "scripts").glob("*.py"):
        ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    config = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("LLM_API_KEY=", "SMTP_USERNAME=", "SMTP_AUTH_CODE="):
        assert key in config.splitlines(), f"Nonempty credential template: {key}"
    for relative in ("backend/app/agent/tools", "backend/app/worker", "frontend/src/views"):
        assert (ROOT / relative).is_dir(), f"Missing scaffold: {relative}"
    print(f"PASS: {count} local document links, code fences, Python syntax, config and scaffold")


if __name__ == "__main__":
    check_documents()
    check_database()
    print("All design checks passed. Business services and SMTP are not exercised.")
