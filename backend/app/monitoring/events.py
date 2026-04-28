from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

import anyio
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .db import get_engine
from .models import Base, Event


class EventType(str, Enum):
    orchestrator_input = "orchestrator_input"
    orchestrator_decision = "orchestrator_decision"
    agent_action = "agent_action"   # emitted at the start of each specialized agent run
    tool_call = "tool_call"
    escalation = "escalation"
    guardrail_violation = "guardrail_violation"
    metric = "metric"
    error = "error"


class EventOut(BaseModel):
    id: Optional[int] = None
    ts: datetime
    type: str
    session_id: str
    customer_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)


@dataclass
class _Subscriber:
    session_id: Optional[str]
    queue: "asyncio.Queue[EventOut]"


class EventsService:
    def __init__(self, *, database_url: Optional[str] = None) -> None:
        self._engine = get_engine(database_url)
        Base.metadata.create_all(self._engine)
        self._subscribers: List[_Subscriber] = []
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "EventsService":
        # Reads DATABASE_URL from the environment so Postgres can be used in production.
        import os
        return cls(database_url=os.getenv("DATABASE_URL"))

    async def emit(self, etype: EventType, *, session_id: str, customer_id: str, payload: Dict[str, Any]) -> EventOut:
        evt = EventOut(ts=datetime.utcnow(), type=str(etype.value), session_id=session_id, customer_id=customer_id, payload=_json_safe(payload))

        async with self._lock:
            # Broadcast first (real-time) then persist.
            for sub in list(self._subscribers):
                if sub.session_id is None or sub.session_id == session_id:
                    sub.queue.put_nowait(evt)

        await anyio.to_thread.run_sync(self._persist_sync, evt)
        return evt

    async def subscribe(self, *, session_id: Optional[str] = None) -> AsyncIterator[EventOut]:
        q: asyncio.Queue[EventOut] = asyncio.Queue(maxsize=1000)
        sub = _Subscriber(session_id=session_id, queue=q)

        async with self._lock:
            self._subscribers.append(sub)

        try:
            while True:
                yield await q.get()
        finally:
            async with self._lock:
                if sub in self._subscribers:
                    self._subscribers.remove(sub)

    async def list_events(self, *, session_id: str, limit: int = 200) -> List[EventOut]:
        return await anyio.to_thread.run_sync(self._list_events_sync, session_id, limit)

    def _persist_sync(self, evt: EventOut) -> None:
        with Session(self._engine) as s:
            row = Event(type=evt.type, session_id=evt.session_id, customer_id=evt.customer_id, payload=evt.payload, ts=evt.ts)
            s.add(row)
            s.commit()
            evt.id = row.id

    def _list_events_sync(self, session_id: str, limit: int) -> List[EventOut]:
        with Session(self._engine) as s:
            stmt = select(Event).where(Event.session_id == session_id).order_by(desc(Event.id)).limit(limit)
            rows = list(s.execute(stmt).scalars())
            rows.reverse()
            return [
                EventOut(id=r.id, ts=r.ts, type=r.type, session_id=r.session_id, customer_id=r.customer_id, payload=r.payload or {})
                for r in rows
            ]


def _json_safe(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Ensure payload is JSON serializable (DB JSON + SSE).
    try:
        json.dumps(payload, default=str)
        return payload
    except Exception:  # noqa: BLE001
        return {"_non_serializable_payload": True, "payload_str": str(payload)}

