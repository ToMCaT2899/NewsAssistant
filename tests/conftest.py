"""离线测试夹具；其中的凭证和服务配置不会用于真实网络调用。"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.app.conf.settings import Settings
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.migrations import migrate
from backend.app.service.accounts import bootstrap


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="chat_completions",
        llm_base_url="",
        llm_model="",
        llm_api_key="",
        smtp_username="",
        smtp_auth_code="",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        model_encryption_key=Fernet.generate_key().decode(),
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
