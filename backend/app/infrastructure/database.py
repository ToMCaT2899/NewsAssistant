"""Short-lived database sessions; services own transaction boundaries."""

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 5})

        @event.listens_for(self.engine, "connect")
        def configure(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def schema_version(self) -> int:
        with self.engine.connect() as connection:
            value = connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar()
            return int(value or 0)

    def close(self) -> None:
        self.engine.dispose()
