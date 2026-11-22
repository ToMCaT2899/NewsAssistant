"""Offline fixtures; these credentials and providers are never used for real network calls."""

from pathlib import Path

import pytest

from backend.app.conf.settings import Settings
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.migrations import migrate
from backend.app.service.accounts import bootstrap


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        jwt_secret="offline-test-signing-secret-not-for-production",
        admin_email="admin@example.com",
        admin_password="Offline-test-password-2026",
        verified_recipient_email="reader@example.com",
        agent_workspace_root=tmp_path / "workspace",
        rss_sources_json='{"fixture":"https://example.com/feed.xml"}',
    )


@pytest.fixture
def database(settings: Settings):
    migrate(settings.database_url, settings.root / "database/migrations")
    db = Database(settings.database_url)
    bootstrap(db, settings)
    yield db
    db.close()
