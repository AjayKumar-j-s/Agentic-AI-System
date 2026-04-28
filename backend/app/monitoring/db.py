from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _default_sqlite_url() -> str:
    # Local dev default; production should set DATABASE_URL (e.g. Postgres).
    return "sqlite:///./agentic_support.db"


@lru_cache(maxsize=1)
def get_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or os.getenv("DATABASE_URL") or _default_sqlite_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)

