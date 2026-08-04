from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from .config import Settings
from .services.storage import FileStorage


def get_db(request: Request) -> Generator[Session, None, None]:
    db = request.app.state.database.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_storage(request: Request) -> FileStorage:
    return request.app.state.storage


def get_settings(request: Request) -> Settings:
    return request.app.state.settings

