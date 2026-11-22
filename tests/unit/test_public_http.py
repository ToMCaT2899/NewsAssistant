import asyncio
import socket

import httpx
import pytest

from backend.app.domain.common import AppError
from backend.app.infrastructure.web_reader import WebReader


@pytest.mark.asyncio
async def test_private_redirect_is_blocked_and_public_connection_is_pinned(monkeypatch):
    original = httpx.AsyncClient
    seen = []

    async def addresses(host, port, **kwargs):
        ip = "127.0.0.1" if host == "127.0.0.1" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def transport(request):
        seen.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs),
    )
    with pytest.raises(AppError, match="不可访问"):
        await WebReader().get("https://example.com/news")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_oversized_http_response_is_rejected(monkeypatch):
    original = httpx.AsyncClient

    async def addresses(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
            ),
            **kwargs,
        ),
    )
    with pytest.raises(AppError, match="2 MiB"):
        await WebReader().get("https://example.com/news")
