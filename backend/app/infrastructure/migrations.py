"""按编号以事务执行 SQL 迁移，保留已归档的初始 DDL。"""

import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def database_path(url: str) -> Path:
    parsed = make_url(url)
    if parsed.drivername != "sqlite" or not parsed.database or parsed.database == ":memory:":
        raise ValueError("MVP requires a file-backed SQLite database")
    return Path(parsed.database).resolve()


def migrate(url: str, directory: Path) -> int:
    path = database_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as connection:
        # SQLite 重建被引用的表需暂时关闭外键执行；事务提交前统一检查全部外键。
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
            ).fetchone()
            version = (
                connection.execute(
                    "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
                ).fetchone()[0]
                if exists
                else 0
            )
            files = sorted(directory.glob("[0-9]*.sql"))
            expected = int(files[-1].name.split("_")[0])
            if version > expected:
                raise RuntimeError("Database version is newer than this application")
            for file in files:
                if int(file.name.split("_")[0]) <= version:
                    continue
                statement = ""
                for line in file.read_text(encoding="utf-8").splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError(f"Incomplete migration: {file.name}")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Foreign key validation failed")
            connection.commit()
            connection.execute("PRAGMA foreign_keys=ON")
            return expected
        except BaseException:
            connection.rollback()
            raise
