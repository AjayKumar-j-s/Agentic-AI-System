from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, Optional

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import ConfigService
from .monitoring.events import (
    EventsService,
    EventType,
)
from .monitoring.redaction import redact_dict
from .monitoring.telemetry import configure_otel, instrument_fastapi
from .orchestrator import Orchestrator, OrchestratorResult


class QueryRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    customer: Optional[Dict[str, Any]] = None


class QueryResponse(BaseModel):
    session_id: str
    resolution: str
    response: str
    escalated: bool
    escalation_reason: Optional[str] = None


class RefundLimitUpdate(BaseModel):
    refund_limit_usd: float = Field(..., gt=0)


def create_app() -> FastAPI:
    # Load local `.env` if present. (Deployed env vars still take precedence.)
    load_dotenv(override=False)
    configure_otel(service_name=os.getenv("OTEL_SERVICE_NAME", "agentic-support"))

    app = FastAPI(title="Agentic Support System", version="0.1.0")
    instrument_fastapi(app)

    events = EventsService.from_env()
    config = ConfigService()
    orchestrator = Orchestrator(events=events, config=config)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/query", response_model=QueryResponse)
    async def query(req: QueryRequest = Body(...)) -> QueryResponse:
        await events.emit(
            EventType.orchestrator_input,
            session_id=req.session_id,
            customer_id=req.customer_id,
            payload=redact_dict(
                {"message": req.message, "customer": req.customer or {}},
                extra_sensitive_keys={"message"},
            ),
        )

        try:
            result: OrchestratorResult = await orchestrator.handle(
                session_id=req.session_id,
                customer_id=req.customer_id,
                message=req.message,
                customer=req.customer or {},
            )
        except Exception as e:  # noqa: BLE001 - API boundary
            await events.emit(
                EventType.error,
                session_id=req.session_id,
                customer_id=req.customer_id,
                payload={"error": type(e).__name__},
            )
            raise HTTPException(status_code=500, detail="Internal error") from e

        return QueryResponse(
            session_id=req.session_id,
            resolution=result.resolution,
            response=result.response,
            escalated=result.escalated,
            escalation_reason=result.escalation_reason,
        )

    @app.get("/events")
    async def list_events(session_id: str, limit: int = 200) -> JSONResponse:
        if limit < 1 or limit > 1000:
            raise HTTPException(status_code=400, detail="limit must be 1..1000")
        rows = await events.list_events(session_id=session_id, limit=limit)
        return JSONResponse(content={"events": [r.model_dump(mode="json") for r in rows]})

    @app.get("/events/stream")
    async def stream_events(session_id: Optional[str] = None) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            async for evt in events.subscribe(session_id=session_id):
                # SSE format: https://html.spec.whatwg.org/multipage/server-sent-events.html
                yield f"event: {evt.type}\n".encode("utf-8")
                yield f"data: {evt.model_dump_json()}\n\n".encode("utf-8")

        return StreamingResponse(gen(), media_type="text/event-stream")

    def _require_admin(admin_key: Optional[str]) -> None:
        expected = os.getenv("ADMIN_API_KEY")
        if not expected:
            raise HTTPException(status_code=500, detail="ADMIN_API_KEY is not set")
        if not admin_key or admin_key != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/config/refund-limit")
    async def get_refund_limit(x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, float]:
        _require_admin(x_admin_key)
        v = await config.get("refund_limit_usd")
        effective = float(v) if v is not None else float(os.getenv("REFUND_LIMIT_USD", "75"))
        return {"refund_limit_usd": effective}

    @app.put("/config/refund-limit")
    async def set_refund_limit(
        body: RefundLimitUpdate = Body(...),
        x_admin_key: Optional[str] = Header(default=None),
    ) -> Dict[str, float]:
        _require_admin(x_admin_key)
        await config.set("refund_limit_usd", str(body.refund_limit_usd))
        return {"refund_limit_usd": body.refund_limit_usd}

    return app


app = create_app()

