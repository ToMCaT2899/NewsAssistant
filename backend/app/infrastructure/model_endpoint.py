"""用户可配置模型端点的公网校验与连接地址固定。"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from backend.app.domain.common import AppError
from backend.app.infrastructure.web_reader import public_ip


def validate_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or parsed.hostname.lower() == "localhost"
            or parsed.path.rstrip("/").endswith("/chat/completions")
        ):
            raise ValueError("Invalid endpoint")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            if not public_ip(str(address)):
                raise ValueError("Private endpoint")
        return str(httpx.URL(value.strip())).rstrip("/")
    except (ValueError, httpx.InvalidURL) as exc:
        raise AppError(
            "INVALID_MODEL_URL",
            "请填写公网 HTTPS API 根地址（含 /v1 等前缀），不含密钥、查询参数或 /chat/completions",
            422,
        ) from exc


async def pinned_endpoint(value: str) -> tuple[httpx.URL, dict, dict]:
    url = httpx.URL(validate_endpoint(value) + "/chat/completions")
    host = url.host
    addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = {str(item[4][0]) for item in addresses}
    if not ips or not all(public_ip(ip) for ip in ips):
        raise AppError("UNSAFE_MODEL_URL", "模型地址解析到非公网地址，已阻止连接", 422)
    return url.copy_with(host=sorted(ips)[0]), {"Host": host}, {"sni_hostname": host}
