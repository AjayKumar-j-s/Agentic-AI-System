from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from .monitoring.db import get_engine
from .monitoring.models import Base, Setting


@dataclass
class ConfigService:
    """
    Simple DB-backed config with a small in-memory TTL cache.
    """

    cache_ttl_s: float = 2.0

    def __post_init__(self) -> None:
        self._engine = get_engine()
        Base.metadata.create_all(self._engine)
        self._cache: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> Optional[str]:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) <= self.cache_ttl_s:
            return cached[1]
        value = await anyio.to_thread.run_sync(self._get_sync, key)
        if value is not None:
            self._cache[key] = (now, value)
        return value

    async def set(self, key: str, value: str) -> None:
        await anyio.to_thread.run_sync(self._set_sync, key, value)
        self._cache[key] = (time.monotonic(), value)

    def _get_sync(self, key: str) -> Optional[str]:
        with Session(self._engine) as s:
            stmt = select(Setting).where(Setting.key == key)
            row = s.execute(stmt).scalar_one_or_none()
            return row.value if row else None

    def _set_sync(self, key: str, value: str) -> None:
        with Session(self._engine) as s:
            row = s.get(Setting, key)
            if row is None:
                row = Setting(key=key, value=value)
                s.add(row)
            else:
                row.value = value
            s.commit()


def env_refund_limit_default() -> float:
    return float(os.getenv("REFUND_LIMIT_USD", "75"))

