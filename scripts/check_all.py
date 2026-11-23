"""供 Compose 检查镜像执行的离线检查，不调用真实模型或 SMTP 服务。"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = (
        [sys.executable, "scripts/check_design.py"],
        ["ruff", "check", "--no-cache", "backend", "scripts", "tests"],
        ["ruff", "format", "--check", "--no-cache", "backend", "scripts", "tests"],
        ["mypy", "backend", "--cache-dir", "/tmp/mypy"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            "/tmp/pytest-news-assistant",
        ],
    )
    for command in commands:
        print("Running: " + " ".join(command), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
