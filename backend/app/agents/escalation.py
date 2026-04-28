from __future__ import annotations

import time
from typing import Any, Callable, Dict

from ..monitoring.events import EventType
from ..monitoring.redaction import redact_dict
from ..tools.tickets import create_human_ticket
from ..guardrails import check_tool_allowed
from .base import BaseAgent


class HumanEscalationAgent(BaseAgent):
    """
    Creates a human support ticket and generates an escalation response.

    Called when intent is 'escalate', a guardrail blocks an action,
    a required entity is missing, a tool fails, or the loop limit is hit.

    Tool: create_human_ticket.
    """

    def __init__(
        self,
        *,
        events,
        genai_client,
        gemini_model: str,
        get_guardrails: Callable,
    ) -> None:
        super().__init__(events=events, genai_client=genai_client, gemini_model=gemini_model)
        self._get_guardrails = get_guardrails

    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph graph node entry point (explicit escalation intent path)."""
        await self._emit_agent_action(state, agent_name="HumanEscalationAgent")
        reason = state.get("escalation_reason") or "explicit_escalation"
        return await self.run(state, reason=reason)

    async def run(self, state: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        """
        Core escalation logic.  Used both as a LangGraph node (via ``__call__``) and
        for mid-flow escalations triggered by other agents.
        """
        state["escalated"] = True
        state["escalation_reason"] = reason

        await self._events.emit(
            EventType.escalation,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"reason": reason},
        )

        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "create_human_ticket", "args": redact_dict({"reason": reason})},
        )
        t0 = time.monotonic()
        ok = False
        try:
            check_tool_allowed("create_human_ticket")
            ticket = create_human_ticket(
                summary=state["message"][:200],
                reason=reason,
                redacted_context=redact_dict(
                    {"customer": state.get("customer", {}), "message": state["message"]},
                    extra_sensitive_keys={"message"},
                ),
            )
            ok = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "create_human_ticket", "error": type(e).__name__},
            )
            ticket = {"ticket_id": "unknown"}
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "create_human_ticket",
                    "value": int((time.monotonic() - t0) * 1000),
                    "success": ok,
                },
            )

        guardrails = self._get_guardrails()
        state["resolution"] = "escalated_to_human"
        state["response"] = await self._gemini_respond(
            state,
            task="escalation_response",
            context={"reason": reason, "ticket": ticket, "refund_limit_usd": guardrails.refund_limit_usd},
        )
        return state
