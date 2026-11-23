"""用户模型配置的隔离、加密、运行接入和公网连接边界检查。"""

import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.domain.common import AppError
from backend.app.infrastructure.model_endpoint import pinned_endpoint, validate_endpoint
from backend.app.infrastructure.security import token_for
from backend.app.main import create_app
from backend.app.models.schema import Run, Subscription, User, UserModelConfig
from backend.app.service import model_config
from backend.app.service.runs import create_run
from backend.app.worker.main import process_run
from tests.integration.test_application import FeedFixture, ResearchModel, owner, token
from tests.unit.test_graph import call

KEY = "offline-personal-model-key"


def configuration(version=0, key=KEY, base="https://provider.example/v1"):
    return model_config.ModelConfigInput(
        version=version, base_url=base, model_name="personal-test-model", api_key=key
    )


def test_environment_key_cannot_bypass_user_configuration_and_api_never_echoes_key(
    database, settings
):
    settings.llm_base_url, settings.llm_model = "https://global.example/v1", "global-model"
    settings.llm_api_key = SecretStr("offline-global-key")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/model-config").status_code == 401
        headers = token(client)
        assert not client.get("/api/v1/status", headers=headers).json()["model_configured"]
        assert (
            client.post(
                "/api/v1/runs", headers=headers | {"Idempotency-Key": "missing"}
            ).status_code
            == 503
        )
        data = configuration().model_dump(mode="json")
        data["api_key"] = KEY
        saved = client.put("/api/v1/model-config", headers=headers, json=data)
        assert saved.status_code == 200
        assert KEY not in saved.text and "api_key" not in saved.json()
        assert saved.json()["has_api_key"] and saved.json()["configured"]
        assert client.get("/api/v1/status", headers=headers).json()["model_configured"]
        assert KEY not in client.get("/api/v1/model-config", headers=headers).text
    uid = owner(database)
    with database.session() as session:
        row = session.get(UserModelConfig, uid)
        assert KEY not in row.encrypted_api_key
        assert (
            Fernet(settings.model_encryption_key.get_secret_value())
            .decrypt(row.encrypted_api_key.encode())
            .decode()
            == KEY
        )
    resolved = model_config.resolve(database, settings, uid)
    assert resolved.llm_api_key.get_secret_value() == KEY
    assert resolved.llm_model == "personal-test-model" and resolved.llm_public_endpoint
    assert settings.llm_api_key.get_secret_value() == "offline-global-key"
    saved = model_config.save(database, settings, uid, configuration(1, key=""))
    assert (
        saved["version"] == 2
        and model_config.resolve(database, settings, uid).llm_api_key.get_secret_value() == KEY
    )
    with pytest.raises(AppError):
        model_config.save(
            database, settings, uid, configuration(2, key="", base="https://other.example/v1")
        )
    cleared = model_config.clear(database, settings, uid, 2)
    assert not cleared["configured"] and not cleared["has_api_key"] and cleared["version"] == 3
    with pytest.raises(AppError):
        model_config.save(database, settings, uid, configuration(1))


def test_configuration_is_scoped_to_authenticated_user(database, settings):
    uid = owner(database)
    model_config.save(database, settings, uid, configuration())
    with database.session.begin() as session:
        second = User(email="other@example.com", password_hash="not-used-for-login")
        session.add(second)
        session.flush()
        other_id = second.id
        session.add(Subscription(user_id=other_id, recipient_email="other@example.com"))
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token_for(other_id, settings)}
        assert not client.get("/api/v1/model-config", headers=headers).json()["configured"]
        forged = configuration().model_dump(mode="json") | {
            "api_key": "other-offline-key",
            "user_id": uid,
        }
        assert client.put("/api/v1/model-config", headers=headers, json=forged).status_code == 422
    model_config.save(database, settings, other_id, configuration(key="other-offline-key"))
    assert model_config.resolve(database, settings, uid).llm_api_key.get_secret_value() == KEY
    assert (
        model_config.resolve(database, settings, other_id).llm_api_key.get_secret_value()
        == "other-offline-key"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.1/v1",
        "https://localhost/v1",
        "https://user:pass@provider.example/v1",
        "https://provider.example:8443/v1",
        "https://provider.example/v1?key=secret",
        "https://provider.example/v1#key",
        "https://provider.example/v1/chat/completions",
    ],
)
def test_untrusted_model_urls_are_rejected(url):
    with pytest.raises(AppError) as error:
        validate_endpoint(url)
    assert error.value.code == "INVALID_MODEL_URL"


@pytest.mark.asyncio
async def test_connection_pins_public_dns_and_checks_real_tool_protocol(
    database, settings, monkeypatch
):
    uid = owner(database)
    model_config.save(database, settings, uid, configuration())
    loop = asyncio.get_running_loop()

    async def addresses(*args, **kwargs):
        return [(2, 1, 6, "", ("1.1.1.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", addresses)
    original = httpx.AsyncClient

    def transport(request):
        assert request.url.host == "1.1.1.1"
        assert request.headers["host"] == "provider.example"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert request.extensions["sni_hostname"] == "provider.example"
        body = json.loads(request.content)
        assert KEY not in json.dumps(body) and body["model"] == "personal-test-model"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [call("connection_probe")],
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)),
    )
    assert (await model_config.test_connection(database, settings, uid))["status"] == "ok"
    assert model_config.view(database, settings, uid)["tested_at"]

    async def rebound(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", rebound)
    with pytest.raises(AppError) as error:
        await pinned_endpoint("https://provider.example/v1")
    assert error.value.code == "UNSAFE_MODEL_URL"


@pytest.mark.asyncio
async def test_worker_uses_owner_configuration_and_key_never_enters_run_snapshot(
    database, settings, monkeypatch
):
    import backend.app.worker.main as worker

    uid = owner(database)
    model_config.save(database, settings, uid, configuration())
    ticket = create_run(database, uid, "personal", require_model=True, settings=settings)

    def adapter(config):
        assert config.llm_model == "personal-test-model"
        assert config.llm_api_key.get_secret_value() == KEY
        return ResearchModel()

    monkeypatch.setattr(worker, "ChatCompletionsClient", adapter)
    with pytest.raises(AppError) as error:
        model_config.clear(database, settings, uid, 1)
    assert error.value.code == "MODEL_CONFIG_BUSY"
    await process_run(database, settings, "worker", reader=FeedFixture())
    with database.session() as session:
        row = session.get(Run, ticket["run_id"])
        assert row.status == "succeeded" and KEY not in row.snapshot_json
    settings.model_encryption_key = SecretStr(Fernet.generate_key().decode())
    with pytest.raises(AppError) as error:
        model_config.resolve(database, settings, uid)
    assert error.value.code == "MODEL_KEY_UNAVAILABLE"
