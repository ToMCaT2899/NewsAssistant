"""Explicitly configured chat-completions tool-calling adapter."""

import json
from typing import Protocol

import httpx

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError


class ModelClient(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> dict: ...


class ChatCompletionsClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> dict:
        settings = self.settings
        if not settings.model_configured:
            raise AppError("MODEL_NOT_CONFIGURED", "请配置模型地址、名称和 API Key", 503)
        if settings.llm_provider != "chat_completions":
            raise AppError("MODEL_PROVIDER_UNSUPPORTED", "仅支持 chat_completions 适配器", 503)
        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                    json={
                        "model": settings.llm_model,
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                        "temperature": 0.2,
                    },
                ) as response:
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise AppError("MODEL_TEMPORARY", "模型服务暂时不可用", 503)
                    if response.status_code != 200:
                        raise AppError("MODEL_REQUEST_FAILED", "模型请求被拒绝，请核对配置", 503)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            raise AppError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过限制", 503)
                payload = json.loads(data)
                message = payload["choices"][0]["message"]
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise ValueError("Invalid content")
                calls = message.get("tool_calls") or []
                if not isinstance(calls, list) or len(calls) > 100:
                    raise ValueError("Invalid tool call batch")
                seen = set()
                for call in calls:
                    function = call["function"]
                    if (
                        not isinstance(call["id"], str)
                        or not call["id"]
                        or call["id"] in seen
                        or call.get("type") != "function"
                        or not isinstance(function["name"], str)
                        or not isinstance(function["arguments"], str)
                    ):
                        raise ValueError("Invalid tool call protocol")
                    seen.add(call["id"])
                # Do not forward provider-specific hidden reasoning fields.
                result = {"role": "assistant", "content": content}
                if message.get("tool_calls"):
                    result["tool_calls"] = message["tool_calls"]
                result["_usage"] = payload.get("usage", {})
                return result
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise AppError("MODEL_TEMPORARY", "模型连接超时或不可用", 503) from exc
            except (KeyError, ValueError, IndexError, TypeError, AttributeError) as exc:
                raise AppError("MODEL_PROTOCOL_ERROR", "模型未返回有效的工具调用协议", 503) from exc
