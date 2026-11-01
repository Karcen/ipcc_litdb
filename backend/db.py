"""Thin, driver-agnostic database layer (SQLAlchemy Core + text() SQL)."""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import DATABASE_URL

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if DATABASE_URL.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(DATABASE_URL, **kwargs)
    return _engine


def query(sql: str, **params: Any) -> list[dict]:
    with engine().connect() as con:
        rows = con.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def query_one(sql: str, **params: Any) -> dict | None:
    r = query(sql, **params)
    return r[0] if r else None


def execute(sql: str, **params: Any) -> None:
    with engine().begin() as con:
        con.execute(text(sql), params)


def executemany(sql: str, rows: Iterable[dict]) -> None:
    with engine().begin() as con:
        for r in rows:
            con.execute(text(sql), r)
