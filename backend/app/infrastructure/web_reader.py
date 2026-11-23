"""限制公网 HTTP 读取范围，固定已校验的 DNS 地址并验证重定向。"""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from backend.app.domain.common import AppError


def canonical_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode

    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise AppError("UNSAFE_URL", "仅允许无凭证的 HTTP(S) 地址")
    try:
        if parts.port not in {None, 80, 443}:
            raise AppError("UNSAFE_URL", "不允许此端口")
    except ValueError as exc:
        raise AppError("UNSAFE_URL", "无效端口") from exc
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def public_ip(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
        return value.is_global and not value.is_multicast and not value.is_reserved
    except ValueError:
        return False


class WebReader:
    async def get(self, url: str, timeout: float = 15) -> bytes:
        async with asyncio.timeout(timeout):
            return await self._read(url, timeout)

    async def _read(self, url: str, timeout: float) -> bytes:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeout
        ) as client:
            for _ in range(6):
                url = canonical_url(url)
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
                ips = {str(item[4][0]) for item in addresses}
                if not ips or not all(public_ip(ip) for ip in ips):
                    raise AppError("UNSAFE_URL", "目标地址不可访问")
                # 连接已校验的地址，同时保留 TLS SNI 和 HTTP Host。
                ip = sorted(ips)[0]
                target = httpx.URL(url).copy_with(host=ip)
                async with client.stream(
                    "GET",
                    target,
                    headers={
                        "Host": parsed.netloc,
                        "User-Agent": "NewsAssistant/0.2 (personal AI digest)",
                        "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise AppError("FETCH_FAILED", "跳转缺少目标")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            raise AppError("CONTENT_TOO_LARGE", "内容超过 2 MiB")
                    return bytes(data)
            raise AppError("FETCH_FAILED", "跳转次数超限")
