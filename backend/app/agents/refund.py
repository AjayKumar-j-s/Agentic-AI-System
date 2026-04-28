from __future__ import annotations

import time
from typing import Any, Callable, Dict

from ..guardrails import GuardrailBlocked
from ..monitoring.events import EventType
from ..monitoring.redaction import redact_dict
from ..tools.refunds import calculate_refund_eligibility, issue_refund
from ..guardrails import check_tool_allowed
from .base import BaseAgent
from .escalation import HumanEscalationAgent
from .extractors import extract_amount_usd, extract_order_id, extract_order_id_from_customer


class RefundAgent(BaseAgent):
    """
    Handles refund eligibility checks and refund issuance.

    Enforces the refund-limit guardrail: amounts above the configured limit
    are blocked and escalated for human sign-off.

    Tools: calculate_refund_eligibility, issue_refund.
    """

    def __init__(
        self,
        *,
        events,
        genai_client,
        gemini_model: str,
        escalation_agent: HumanEscalationAgent,
        get_guardrails: Callable,
    ) -> None:
        super().__init__(events=events, genai_client=genai_client, gemini_model=gemini_model)
        self._escalation_agent = escalation_agent
        self._get_guardrails = get_guardrails

    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        await self._emit_agent_action(state, agent_name="RefundAgent")

        # Fetch current guardrails (may be updated from DB between requests).
        guardrails = self._get_guardrails()

        # --- resolve order id and requested amount ---
        extracted = state.get("extracted") or {}
        extracted_order_id = None
        extracted_amount = None
        if isinstance(extracted, dict):
            v = extracted.get("order_id")
            if isinstance(v, str) and v.strip():
                extracted_order_id = v.strip().upper()
            a = extracted.get("refund_amount_usd")
            if isinstance(a, (int, float)):
                extracted_amount = float(a)

        order_id = (
            extracted_order_id
            or extract_order_id(state["message"])
            or extract_order_id_from_customer(state.get("customer", {}))
        )
        if not order_id:
            return await self._escalation_agent.run(state, reason="missing_order_id_for_refund")

        requested_amount = extracted_amount if extracted_amount is not None else extract_amount_usd(state["message"])

        # --- calculate_refund_eligibility ---
        t0 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "calculate_refund_eligibility", "args": redact_dict({"order_id": order_id})},
        )
        ok = False
        try:
            check_tool_allowed("calculate_refund_eligibility")
            elig = calculate_refund_eligibility(order_id)
            ok = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "calculate_refund_eligibility", "error": type(e).__name__},
            )
            return await self._escalation_agent.run(state, reason="tool_error_calculate_refund_eligibility")
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "calculate_refund_eligibility",
                    "value": int((time.monotonic() - t0) * 1000),
                    "success": ok,
                },
            )

        if not elig["eligible"]:
            state["resolution"] = "refund_denied"
            state["response"] = await self._gemini_respond(
                state,
                task="refund_denied_response",
                context={"order_id": order_id, "eligibility": elig},
            )
            return state

        amount = float(
            requested_amount if requested_amount is not None
            else min(elig["max_refund_usd"], guardrails.refund_limit_usd)
        )

        # --- GUARDRAIL: refund limit ---
        try:
            guardrails.check_refund_amount(amount)
        except GuardrailBlocked:
            await self._events.emit(
                EventType.guardrail_violation,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "type": "refund_limit",
                    "attempted_amount": amount,
                    "limit": guardrails.refund_limit_usd,
                },
            )
            return await self._escalation_agent.run(
                state,
                reason=f"refund_requires_human_signoff_over_{int(guardrails.refund_limit_usd)}",
            )

        # --- issue_refund ---
        t1 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "issue_refund", "args": redact_dict({"order_id": order_id, "amount": amount})},
        )
        ok2 = False
        try:
            check_tool_allowed("issue_refund")
            receipt = issue_refund(order_id=order_id, amount_usd=amount)
            ok2 = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "issue_refund", "error": type(e).__name__},
            )
            return await self._escalation_agent.run(state, reason="tool_error_issue_refund")
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "issue_refund",
                    "value": int((time.monotonic() - t1) * 1000),
                    "success": ok2,
                },
            )

        state["resolution"] = "refund_approved"
        state["response"] = await self._gemini_respond(
            state,
            task="refund_approved_response",
            context={
                "order_id": order_id,
                "amount_usd": amount,
                "receipt": receipt,
                "refund_limit_usd": guardrails.refund_limit_usd,
            },
        )
        return state
