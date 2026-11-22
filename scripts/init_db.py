"""Create a new SQLite database from the versioned initial schema."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "database" / "migrations" / "001_initial.sql"


def initialize(database: Path) -> None:
    """Create exclusively; rollback and remove only this invocation's new file on failure."""
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting an existing database, including concurrent setup.
    with database.open("xb"):
        pass
    connection = None
    try:
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = ON")
        sql = SCHEMA.read_text(encoding="utf-8")
        connection.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database foreign key check failed")
    except BaseException:
        if connection is not None:
            connection.rollback()
            connection.close()
            connection = None
        database.unlink(missing_ok=True)
        raise
    finally:
        if connection is not None:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    try:
        initialize(args.database)
    except FileExistsError:
        parser.exit(1, "Refusing to overwrite an existing database.\n")
    print(f"Initialized schema v1: {args.database.resolve()}")


if __name__ == "__main__":
    main()
