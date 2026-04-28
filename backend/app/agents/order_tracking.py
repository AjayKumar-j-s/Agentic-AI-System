from __future__ import annotations

import time
from typing import Any, Dict

from ..monitoring.events import EventType
from ..monitoring.redaction import redact_dict
from ..tools.orders import get_order_status, get_shipment_tracking
from ..guardrails import check_tool_allowed
from .base import BaseAgent
from .escalation import HumanEscalationAgent
from .extractors import extract_order_id, extract_order_id_from_customer


class OrderTrackingAgent(BaseAgent):
    """
    Handles order status and shipping tracking lookups.

    Tools: get_order_status, get_shipment_tracking.
    Escalates when the order id is missing or a tool call fails.
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
        await self._emit_agent_action(state, agent_name="OrderTrackingAgent")

        # --- resolve order id ---
        extracted = state.get("extracted") or {}
        extracted_order_id = None
        if isinstance(extracted, dict):
            v = extracted.get("order_id")
            if isinstance(v, str) and v.strip():
                extracted_order_id = v.strip().upper()

        order_id = (
            extracted_order_id
            or extract_order_id(state["message"])
            or extract_order_id_from_customer(state.get("customer", {}))
        )
        if not order_id:
            return await self._escalation_agent.run(state, reason="missing_order_id")

        # --- get_order_status ---
        t0 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "get_order_status", "args": redact_dict({"order_id": order_id})},
        )
        ok = False
        try:
            check_tool_allowed("get_order_status")
            status = get_order_status(order_id)
            ok = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "get_order_status", "error": type(e).__name__},
            )
            return await self._escalation_agent.run(state, reason="tool_error_get_order_status")
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "get_order_status",
                    "value": int((time.monotonic() - t0) * 1000),
                    "success": ok,
                },
            )

        # --- get_shipment_tracking ---
        t1 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"tool": "get_shipment_tracking", "args": redact_dict({"order_id": order_id})},
        )
        ok2 = False
        try:
            check_tool_allowed("get_shipment_tracking")
            tracking = get_shipment_tracking(order_id)
            ok2 = True
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"tool": "get_shipment_tracking", "error": type(e).__name__},
            )
            return await self._escalation_agent.run(state, reason="tool_error_get_shipment_tracking")
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "get_shipment_tracking",
                    "value": int((time.monotonic() - t1) * 1000),
                    "success": ok2,
                },
            )

        state["resolution"] = "order_tracking_resolved"
        state["response"] = await self._gemini_respond(
            state,
            task="order_tracking_response",
            context={
                "order_id": order_id,
                "order_status": status,
                "shipment_tracking": tracking,
            },
        )
        return state
