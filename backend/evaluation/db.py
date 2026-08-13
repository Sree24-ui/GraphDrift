"""Isolated SQLite session helpers for evaluation databases."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.detection.fusion import ensure_db_schema


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def create_eval_engine(db_path: Path):
    path = db_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        _sqlite_url(path),
        connect_args={"check_same_thread": False},
    )
    return engine


def init_eval_db(db_path: Path) -> sessionmaker[Session]:
    from app import models  # noqa: F401

    engine = create_eval_engine(db_path)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with session_factory() as db:
        ensure_db_schema(db)
    return session_factory


def session_for(db_path: Path) -> Generator[Session, None, None]:
    factory = init_eval_db(db_path)
    db = factory()
    try:
        yield db
    finally:
        db.close()
