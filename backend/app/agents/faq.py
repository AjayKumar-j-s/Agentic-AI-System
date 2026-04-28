from __future__ import annotations

import time
from typing import Any, Dict

from ..monitoring.events import EventType
from ..monitoring.redaction import redact_dict
from ..tools.faq import faq_search
from ..guardrails import check_tool_allowed
from .base import BaseAgent
from .escalation import HumanEscalationAgent


class FAQAgent(BaseAgent):
    """
    Resolves common policy and how-to queries using the FAQ knowledge base.

    Tool: faq_search.
    Escalates when the faq_search tool fails.
    """

    def __init__(
        self,
        *,
        events,
        genai_client,
        gemini_model: str,
        escalation_agent: HumanEscalationAgent,
    ) -> None:
        super().__init__(events=events, genai_client=genai_client, gemini_model=gemini_model)
        self._escalation_agent = escalation_agent

    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        await self._emit_agent_action(state, agent_name="FAQAgent")

        t0 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={
                "tool": "faq_search",
                "args": redact_dict({"query": state["message"]}, extra_sensitive_keys={"query"}),
            },
        )
        ok = False
        try:
            check_tool_allowed("faq_search")
            answer = faq_search(state["message"])
            ok = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "faq_search", "error": type(e).__name__},
            )
            return await self._escalation_agent.run(state, reason="tool_error_faq_search")
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "faq_search",
                    "value": int((time.monotonic() - t0) * 1000),
                    "success": ok,
                },
            )

        state["resolution"] = "faq_resolved"
        state["response"] = await self._gemini_respond(
            state,
            task="faq_response",
            context={"kb_answer": answer},
        )
        return state
