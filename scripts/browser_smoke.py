"""使用临时凭证和隔离数据库执行本地真实浏览器与 API 冒烟检查。

有意不启动工作进程；仅验证任务入队，不验证真实模型生成或 SMTP 投递。
使用宿主机 Chrome，不下载浏览器，也不将服务暴露到公网。
"""

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Smoke service exited; inspect artifacts/browser-smoke logs")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("Smoke service startup timed out")


def free_port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return int(connection.getsockname()[1])


def main() -> None:
    run_dir = ROOT / "artifacts" / ("browser-smoke-" + secrets.token_hex(4))
    run_dir.mkdir(parents=True)
    password = secrets.token_urlsafe(24)
    api_port, web_port = free_port(), free_port()
    env = os.environ | {
        "DATABASE_URL": "sqlite:///" + (run_dir / "browser.db").as_posix(),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ADMIN_EMAIL": "browser-test@example.com",
        "ADMIN_PASSWORD": password,
        "VERIFIED_RECIPIENT_EMAIL": "",
        "LLM_PROVIDER": "chat_completions",
        "LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "LLM_MODEL": "offline-browser-fixture",
        "LLM_API_KEY": "offline-browser-fixture",
        "SMTP_USERNAME": "",
        "SMTP_AUTH_CODE": "",
        "E2E_PASSWORD": password,
        "E2E_BASE_URL": f"http://127.0.0.1:{web_port}",
        "API_PROXY_TARGET": f"http://127.0.0.1:{api_port}",
    }
    subprocess.run(
        [sys.executable, "-m", "backend.app.cli", "migrate"], cwd=ROOT, env=env, check=True
    )
    processes = []
    with (
        (run_dir / "api.log").open("w", encoding="utf-8") as api_log,
        (run_dir / "vite.log").open("w", encoding="utf-8") as vite_log,
    ):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            api = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=ROOT,
                env=env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            processes.append(api)
            wait_for(f"http://127.0.0.1:{api_port}/ready", api)
            vite = subprocess.Popen(
                [
                    "node",
                    str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                    "--strictPort",
                ],
                cwd=ROOT / "frontend",
                env=env,
                stdout=vite_log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            processes.append(vite)
            wait_for(f"http://127.0.0.1:{web_port}", vite)
            subprocess.run(
                ["node", str(ROOT / "frontend/node_modules/@playwright/test/cli.js"), "test"],
                cwd=ROOT / "frontend",
                env=env,
                check=True,
            )
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print("Browser/API smoke passed; no live model, worker, or SMTP was exercised.")


if __name__ == "__main__":
    main()
