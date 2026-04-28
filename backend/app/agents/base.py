from __future__ import annotations

import json
import time
from typing import Any, Dict

import anyio

from ..monitoring.events import EventsService, EventType
from ..monitoring.redaction import redact_dict
from ..guardrails import check_tool_allowed


class BaseAgent:
    """
    Shared base class for all specialized agents.

    Provides:
    - ``_gemini_respond``: calls Gemini to produce the final user-facing text.
    - ``_emit_agent_action``: emits an ``agent_action`` event at the start of a run.
    """

    def __init__(
        self,
        *,
        events: EventsService,
        genai_client: Any,   # google.genai.Client instance
        gemini_model: str,
    ) -> None:
        self._events = events
        self._genai = genai_client
        self._gemini_model = gemini_model

    async def _emit_agent_action(self, state: Dict[str, Any], *, agent_name: str) -> None:
        """Emits an agent_action event so the monitoring layer knows which agent is running."""
        await self._events.emit(
            EventType.agent_action,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"agent": agent_name, "action": "run"},
        )

    async def _gemini_respond(
        self,
        state: Dict[str, Any],
        *,
        task: str,
        context: Dict[str, Any],
    ) -> str:
        """Calls Gemini to produce the final user-facing support response."""
        t0 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "gemini_respond", "args": redact_dict({"task": task, "context": context})},
        )
        try:
            check_tool_allowed("gemini_respond")
            prompt = (
                "You are a customer support assistant for an e-commerce company.\n"
                "Follow these rules:\n"
                "- Be concise and helpful.\n"
                "- Do NOT reveal sensitive customer info.\n"
                "- If a refund is above the auto-approval limit, say it requires human review.\n"
                "- Use the provided tool context; do not invent order status/tracking/refund ids.\n"
                f"Task: {task}\n"
                f"User message: {state['message']}\n"
                f"Context JSON: {json.dumps(context, default=str)}\n"
            )

            def _call() -> str:
                resp = self._genai.models.generate_content(model=self._gemini_model, contents=prompt)
                return (resp.text or "").strip()

            text = await anyio.to_thread.run_sync(_call)
            return text or "Unable to generate a response."
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"component": "gemini_respond", "error": type(e).__name__},
            )
            return "Unable to generate a response right now. I'm escalating this to a human agent."
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "gemini_respond",
                    "value": int((time.monotonic() - t0) * 1000),
                },
            )
