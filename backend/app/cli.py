"""管理操作入口，命令行参数不得包含凭证。"""

import argparse
import asyncio
import json

from backend.app.conf.settings import get_settings
from backend.app.domain.common import required
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.migrations import migrate
from backend.app.models.schema import Run
from backend.app.service.accounts import bootstrap, verify_recipient
from backend.app.service.runs import create_run
from backend.app.worker.main import process_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    verify = commands.add_parser("verify-recipient")
    verify.add_argument("--user-email", required=True)
    verify.add_argument("--recipient", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--user-id", required=True)
    generate.add_argument("--key", required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "migrate":
        version = migrate(settings.database_url, settings.root / "database/migrations")
        database = Database(settings.database_url)
        try:
            created = bootstrap(database, settings)
            print(json.dumps({"schema_version": version, "account_created": created}))
        finally:
            database.close()
        return
    database = Database(settings.database_url)
    try:
        if args.command == "verify-recipient":
            verify_recipient(database, args.user_email, args.recipient)
            print("Recipient verified by administrator")
        elif args.command == "generate":
            ticket = create_run(
                database, args.user_id, args.key, require_model=True, settings=settings
            )
            if not ticket["reused"]:
                asyncio.run(process_run(database, settings, "cli"))
            with database.session() as session:
                row = required(session.get(Run, ticket["run_id"]))
                print(
                    json.dumps(
                        {"run_id": row.id, "status": row.status, "error_code": row.error_code}
                    )
                )
    finally:
        database.close()


if __name__ == "__main__":
    main()
