"""Validate the built Compose stack with disposable accounts, ports and named volumes.

This never supplies real model/SMTP credentials and never sends mail. All resources use a random
test project name; normal application data and other projects are not touched.
"""

import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    project = "news-assistant-smoke-" + secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    env = os.environ | {
        "APP_PORT": "0",
        "COMPOSE_PROFILES": "",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ADMIN_EMAIL": "compose-test@example.com",
        "ADMIN_PASSWORD": password,
        "VERIFIED_RECIPIENT_EMAIL": "",
        "SMTP_USERNAME": "",
        "SMTP_AUTH_CODE": "",
        "LLM_BASE_URL": "",
        "LLM_MODEL": "",
        "LLM_API_KEY": "",
        "WORKER_POLL_SECONDS": "1",
    }
    prefix = ["docker", "compose", "-p", project]

    def compose(*args: str, capture: bool = False) -> str:
        result = subprocess.run(
            [*prefix, *args],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout.strip() if capture else ""

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = ""
    token = ""

    def request(path: str, data=None, method: str = "GET", extra_headers=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra_headers or {})
        req = urllib.request.Request(
            base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        with opener.open(req, timeout=10) as response:
            return json.load(response)

    def sign_in() -> str:
        nonlocal token
        deadline = time.monotonic() + 40
        while True:
            try:
                token = request(
                    "/api/v1/auth/login",
                    {
                        "email": "compose-test@example.com",
                        "password": password,
                    },
                    "POST",
                )["access_token"]
                return token
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    try:
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
        base = "http://" + compose("port", "frontend", "8080", capture=True)
        sign_in()
        subscription = request("/api/v1/subscription")
        for key in ("id", "recipient_verified", "next_run_at"):
            subscription.pop(key)
        subscription["persona"] = "Compose persistence test"
        saved = request("/api/v1/subscription", subscription, "PUT")
        assert saved["version"] == 2
        assert not request("/api/v1/status")["model_configured"]
        try:
            request("/api/v1/runs", {}, "POST", {"Idempotency-Key": "compose-unconfigured"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert json.load(exc)["error"]["code"] == "MODEL_NOT_CONFIGURED"
        else:
            raise AssertionError("Unconfigured live model was not rejected")
        compose("up", "-d", "--no-build", "--force-recreate", "api", "worker")
        sign_in()
        assert request("/api/v1/subscription")["persona"] == "Compose persistence test"
        compose("down", "--remove-orphans")
        compose(
            "run",
            "--rm",
            "--no-deps",
            "migrate",
            "python",
            "-c",
            "import sqlite3; c=sqlite3.connect('/app/data/news_assistant.db'); "
            "assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; "
            "assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]==2; "
            "assert c.execute('SELECT persona FROM subscriptions').fetchone()[0]"
            "=='Compose persistence test'; print('Compose volume persistence verified')",
        )
        print(
            "Compose smoke passed: health, auth, subscription, missing-provider error, recreation, "
            "and persistent schema v2. Live model/SMTP and public deployment were not tested."
        )
    finally:
        subprocess.run([*prefix, "down", "--remove-orphans"], cwd=ROOT, env=env, check=False)
        for suffix in ("news-data", "news-workspace"):
            name = f"{project}_{suffix}"
            result = subprocess.run(
                ["docker", "volume", "inspect", name],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                continue
            info = json.loads(result.stdout)[0]
            if info.get("Labels", {}).get("com.docker.compose.project") != project:
                raise RuntimeError("Refusing to remove a volume outside the smoke project")
            subprocess.run(["docker", "volume", "rm", name], check=True, capture_output=True)


if __name__ == "__main__":
    main()
