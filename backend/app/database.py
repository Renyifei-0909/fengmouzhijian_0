from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

if TYPE_CHECKING:
    from .schema import SchemaStatus


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        parsed_url = make_url(url)
        backend = parsed_url.get_backend_name()
        if backend == "sqlite" and parsed_url.database not in {None, "", ":memory:"}:
            Path(parsed_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if backend == "sqlite" else {}
        self.engine: Engine = create_engine(
            url,
            connect_args=connect_args,
            future=True,
            pool_pre_ping=backend == "postgresql",
        )
        if backend == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        self.schema_status: SchemaStatus | None = None

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def prepare_schema(self, mode: str) -> SchemaStatus:
        from .schema import prepare_database_schema

        self.schema_status = prepare_database_schema(self.engine, mode=mode)
        return self.schema_status

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    def session(self) -> Generator[Session, None, None]:
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()
