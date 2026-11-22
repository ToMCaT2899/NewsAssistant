"""Protocol-level HTTP fixtures; these are not a real provider integration."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.domain.common import AppError
from backend.app.infrastructure.llm import ChatCompletionsClient


@pytest.mark.asyncio
async def test_missing_model_configuration_is_explicit(settings):
    with pytest.raises(AppError) as error:
        await ChatCompletionsClient(settings).complete([], [], 2)
    assert error.value.code == "MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_chat_protocol_preserves_tool_calls_but_omits_hidden_reasoning(settings, monkeypatch):
    settings.llm_base_url = "https://model.example/v1"
    settings.llm_model = "offline-fixture"
    settings.llm_api_key = SecretStr("offline-protocol-key")
    original = httpx.AsyncClient

    def transport(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto" and body["tools"] == []
        assert request.headers["Authorization"] == "Bearer offline-protocol-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning_content": "must never be forwarded",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "fetch_feed",
                                        "arguments": '{"source_id":"fixture"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(transport),
            **kwargs,
        ),
    )
    response = await ChatCompletionsClient(settings).complete([], [], 2)
    assert response["tool_calls"][0]["function"]["name"] == "fetch_feed"
    assert "reasoning_content" not in response
    assert "offline-protocol-key" not in json.dumps(response)


@pytest.mark.asyncio
async def test_invalid_duplicate_tool_ids_are_a_protocol_error(settings, monkeypatch):
    settings.llm_base_url = "https://model.example/v1"
    settings.llm_model = "offline-fixture"
    settings.llm_api_key = SecretStr("offline-protocol-key")
    original = httpx.AsyncClient
    call = {"id": "duplicate", "type": "function", "function": {"name": "probe", "arguments": "{}"}}
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": None, "tool_calls": [call, call]}}],
                    },
                )
            ),
            **kwargs,
        ),
    )
    with pytest.raises(AppError) as error:
        await ChatCompletionsClient(settings).complete([], [], 2)
    assert error.value.code == "MODEL_PROTOCOL_ERROR"
