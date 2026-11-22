from pathlib import Path

import pytest

from backend.app.agent.tools.files import FileTools
from backend.app.agent.tools.registry import Registry
from backend.app.domain.common import AppError
from backend.app.infrastructure.web_reader import canonical_url, public_ip


@pytest.mark.asyncio
async def test_five_file_tools_and_command_boundary(tmp_path: Path):
    tools = FileTools(tmp_path / "run", shell_enabled=True)
    registry = Registry(tools.definitions())
    assert len(registry.schemas()) == 5
    assert (await registry.invoke("write_file", {"path": "notes/a.txt", "content": "AI news"}, 3))[
        "ok"
    ]
    assert await tools.read_file("notes/a.txt") == "AI news"
    assert (await tools.list_dir("."))[0]["name"] == "notes"
    assert (await tools.search_content("AI"))[0]["line"] == 1
    assert (await tools.bash("wc notes/a.txt"))["stdout"].strip() == "7"
    assert (await tools.bash("pwd"))["returncode"] == 0
    for command in ("pwd; echo secret", "python -c evil", "wc ../secret", "wc .env"):
        assert not (await registry.invoke("bash", {"command": command}, 3))["ok"]
    for path in ("../secret", "/etc/passwd", "C:/secret", "notes/../../secret"):
        assert not (await registry.invoke("read_file", {"path": path}, 3))["ok"]
    assert not (await registry.invoke("write_file", {"path": "big", "content": "中" * 30000}, 3))[
        "ok"
    ]
    assert not (
        await Registry(FileTools(tmp_path / "other").definitions()).invoke(
            "bash", {"command": "pwd"}, 3
        )
    )["ok"]


@pytest.mark.asyncio
async def test_symlink_escape_is_denied(tmp_path: Path):
    tools = FileTools(tmp_path / "run")
    target = tmp_path / "secret.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (tools.root / "link").symlink_to(target)
    except OSError:
        pytest.skip("Host does not allow creation of symlinks")
    with pytest.raises(AppError):
        await tools.read_file("link")


def test_url_rules_preserve_semantic_queries():
    assert canonical_url("https://example.com/news?id=42&utm_source=x#part") == (
        "https://example.com/news?id=42"
    )
    for url in ("file:///etc/passwd", "https://user:pass@example.com", "http://example.com:9000"):
        with pytest.raises(AppError):
            canonical_url(url)
    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"):
        assert not public_ip(address)
    assert public_ip("8.8.8.8")
