"""Run-scoped file tools and a fixed-command subprocess bridge."""

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path

from pydantic import Field

from backend.app.agent.tools.registry import Tool
from backend.app.domain.common import AppError
from backend.app.domain.schemas import Contract

MAX_BYTES = 64 * 1024


class PathInput(Contract):
    path: str = Field(".", max_length=200)


class SearchInput(Contract):
    keyword: str = Field(min_length=1, max_length=100)
    dir: str = Field(".", max_length=200)


class WriteInput(PathInput):
    content: str = Field(max_length=MAX_BYTES)


class CommandInput(Contract):
    command: str = Field(min_length=1, max_length=200)


class FileTools:
    def __init__(self, root: Path, shell_enabled: bool = False) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.shell_enabled = shell_enabled

    def resolve(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute() or ":" in value or "\\" in value:
            raise AppError("PATH_DENIED", "只允许运行工作区内的相对路径")
        candidate = self.root / path
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise AppError("PATH_DENIED", "路径越界")
        for part in (candidate, *candidate.parents):
            if part == self.root.parent:
                break
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise AppError("PATH_DENIED", "禁止符号链接或目录联接")
        return resolved

    async def list_dir(self, path: str = ".") -> object:
        target = self.resolve(path)
        return [
            {"name": item.name, "is_dir": item.is_dir()} for item in sorted(target.iterdir())[:100]
        ]

    async def read_file(self, path: str = ".") -> object:
        target = self.resolve(path)
        if target.stat().st_size > MAX_BYTES:
            raise AppError("FILE_TOO_LARGE", "文件超过 64 KiB")
        return target.read_text(encoding="utf-8")

    async def write_file(self, path: str, content: str) -> object:
        target = self.resolve(path)
        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise AppError("FILE_TOO_LARGE", "文件超过 64 KiB")
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if used + len(data) > 512 * 1024:
            raise AppError("WORKSPACE_FULL", "工作区配额已满")
        if len(target.relative_to(self.root).parts) > 8:
            raise AppError("PATH_DENIED", "目录层级过深")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        self.resolve(temporary.relative_to(self.root).as_posix())
        temporary.write_bytes(data)
        temporary.replace(target)
        return {"path": str(target.relative_to(self.root)), "bytes": len(data)}

    async def search_content(self, keyword: str, dir: str = ".") -> object:
        results: list[dict] = []
        for index, path in enumerate(self.resolve(dir).rglob("*")):
            if index >= 200 or len(results) >= 100:
                break
            safe = self.resolve(path.relative_to(self.root).as_posix())
            if not safe.is_file() or safe.stat().st_size > MAX_BYTES:
                continue
            try:
                for number, line in enumerate(safe.read_text(encoding="utf-8").splitlines(), 1):
                    if keyword in line:
                        results.append(
                            {
                                "path": path.relative_to(self.root).as_posix(),
                                "line": number,
                                "text": line[:300],
                            }
                        )
                        if len(results) >= 100:
                            break
            except UnicodeError:
                continue
        return results

    async def bash(self, command: str) -> object:
        if not self.shell_enabled:
            raise AppError("TOOL_DISABLED", "受限命令工具未启用")
        parts = shlex.split(command)
        if parts == ["pwd"]:
            argv = [sys.executable, "-I", "-c", "import os; print(os.getcwd())"]
        elif len(parts) == 2 and parts[0] == "wc":
            target = self.resolve(parts[1])
            if not target.is_file() or target.stat().st_size > MAX_BYTES:
                raise AppError("COMMAND_DENIED", "仅允许统计小型文本文件")
            argv = [
                sys.executable,
                "-I",
                "-c",
                "import pathlib,sys; print(len(pathlib.Path(sys.argv[1]).read_bytes()))",
                str(target),
            ]
        else:
            raise AppError("COMMAND_DENIED", "仅允许 pwd 或 wc <工作区文件>")
        result = await asyncio.to_thread(
            subprocess.run,
            argv,
            cwd=self.root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
            env={"PATH": os.defpath, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
        return {"stdout": result.stdout[:1000], "returncode": result.returncode}

    def definitions(self) -> list[Tool]:
        return [
            Tool("list_dir", "列出本次运行工作区文件，最多100项", PathInput, self.list_dir),
            Tool("read_file", "读取本次工作区UTF-8小文件", PathInput, self.read_file),
            Tool("search_content", "字面关键词搜索本次工作区", SearchInput, self.search_content),
            Tool("write_file", "写入本次工作区的文本，不写项目或配置", WriteInput, self.write_file),
            Tool("bash", "受限命令：pwd 或 wc <文件>；默认关闭", CommandInput, self.bash),
        ]
