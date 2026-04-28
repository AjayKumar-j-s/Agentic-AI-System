from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, TypedDict

import anyio
from google import genai
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from .agents import FAQAgent, HumanEscalationAgent, OrderTrackingAgent, RefundAgent
from .config import ConfigService, env_refund_limit_default
from .guardrails import ALLOWED_TOOLS, Guardrails, check_tool_allowed  # noqa: F401 — ALLOWED_TOOLS imported for reference
from .monitoring.events import EventsService, EventType
from .monitoring.redaction import redact_dict


Intent = Literal["order_tracking", "refund", "faq", "escalate"]


class FlowState(TypedDict, total=False):
    session_id: str
    customer_id: str
    message: str
    customer: Dict[str, Any]
    intent: Intent
    intent_confidence: float
    attempts: int
    resolution: str
    response: str
    escalated: bool
    escalation_reason: Optional[str]
    extracted: Dict[str, Any]


@dataclass(frozen=True)
class OrchestratorResult:
    resolution: str
    response: str
    escalated: bool
    escalation_reason: Optional[str] = None


class Orchestrator:
    """
    Routes incoming customer queries through a LangGraph workflow to one of four
    specialized agents: OrderTrackingAgent, RefundAgent, FAQAgent, or
    HumanEscalationAgent.

    Guardrails enforced here:
    - Refund limit (via RefundAgent + Guardrails.check_refund_amount)
    - Session isolation (FlowState is ephemeral per call; no cross-session state)
    - Loop limit (GraphRecursionError → guardrail_violation event + escalation)
    - Tool allowlist (see ALLOWED_TOOLS in guardrails.py; only those tools are
      callable from agent code)
    """

    def __init__(self, *, events: EventsService, config: ConfigService) -> None:
        self._events = events
        self._config = config
        self._guardrails = Guardrails(refund_limit_usd=env_refund_limit_default())
        self._max_steps = int(os.getenv("MAX_WORKFLOW_STEPS", "8"))
        self._gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required")
        self._genai = genai.Client(api_key=api_key)
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self):
        """Instantiates agent classes, wires them as LangGraph nodes, and compiles."""
        agent_kwargs = dict(
            events=self._events,
            genai_client=self._genai,
            gemini_model=self._gemini_model,
        )

        # Escalation agent shared by all other agents for mid-flow escalation.
        escalation_agent = HumanEscalationAgent(
            **agent_kwargs,
            get_guardrails=lambda: self._guardrails,
        )
        # Store reference so handle() can call it for the loop-limit path.
        self._escalation_agent = escalation_agent

        order_tracking_agent = OrderTrackingAgent(
            **agent_kwargs,
            escalation_agent=escalation_agent,
        )
        refund_agent = RefundAgent(
            **agent_kwargs,
            escalation_agent=escalation_agent,
            get_guardrails=lambda: self._guardrails,
        )
        faq_agent = FAQAgent(
            **agent_kwargs,
            escalation_agent=escalation_agent,
        )

        g: StateGraph[FlowState] = StateGraph(FlowState)

        g.add_node("classify", self._classify)
        g.add_node("order_tracking", order_tracking_agent)
        g.add_node("refund", refund_agent)
        g.add_node("faq", faq_agent)
        g.add_node("escalate", escalation_agent)

        g.set_entry_point("classify")

        def route(state: FlowState) -> str:
            intent: Intent = state.get("intent", "escalate")
            if intent == "order_tracking":
                return "order_tracking"
            if intent == "refund":
                return "refund"
            if intent == "faq":
                return "faq"
            return "escalate"

        g.add_conditional_edges(
            "classify",
            route,
            {"order_tracking": "order_tracking", "refund": "refund", "faq": "faq", "escalate": "escalate"},
        )
        g.add_edge("order_tracking", END)
        g.add_edge("refund", END)
        g.add_edge("faq", END)
        g.add_edge("escalate", END)
        return g.compile()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle(
        self,
        *,
        session_id: str,
        customer_id: str,
        message: str,
        customer: Dict[str, Any],
    ) -> OrchestratorResult:
        # Dynamic refund limit — DB setting overrides env default.
        override = await self._config.get("refund_limit_usd")
        if override is not None:
            try:
                self._guardrails = Guardrails(refund_limit_usd=float(override))
            except Exception:  # noqa: BLE001
                self._guardrails = Guardrails(refund_limit_usd=env_refund_limit_default())

        state: FlowState = {
            "session_id": session_id,
            "customer_id": customer_id,
            "message": message,
            "customer": customer,
            "attempts": 0,
            "extracted": {},
            "escalated": False,
        }

        started = time.time()
        try:
            out: FlowState = await self._graph.ainvoke(state, config={"recursion_limit": self._max_steps})
        except GraphRecursionError as e:
            # Loop-limit guardrail: emit violation event then escalate to human.
            await self._events.emit(
                EventType.guardrail_violation,
                session_id=session_id,
                customer_id=customer_id,
                payload={"type": "loop_limit", "max_steps": self._max_steps, "detail": str(e)},
            )
            out = await self._escalation_agent.run(state, reason="loop_limit_reached")
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=session_id,
                customer_id=customer_id,
                payload={"error": type(e).__name__},
            )
            raise
        finally:
            elapsed_ms = int((time.time() - started) * 1000)
            await self._events.emit(
                EventType.metric,
                session_id=session_id,
                customer_id=customer_id,
                payload={"name": "request_latency_ms", "value": elapsed_ms},
            )

        return OrchestratorResult(
            resolution=out.get("resolution", "unknown"),
            response=out.get("response", "No response generated."),
            escalated=bool(out.get("escalated", False)),
            escalation_reason=out.get("escalation_reason"),
        )

    # ------------------------------------------------------------------
    # Classify node (routing only — stays in orchestrator)
    # ------------------------------------------------------------------

    async def _classify(self, state: FlowState) -> FlowState:
        intent, confidence, extracted = await self._gemini_route(state)

        await self._events.emit(
            EventType.orchestrator_decision,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={"intent": intent, "confidence": confidence, "source": "gemini"},
        )

        state["intent"] = intent
        state["intent_confidence"] = confidence
        state["extracted"] = extracted

        if confidence < float(os.getenv("ESCALATE_INTENT_CONFIDENCE_BELOW", "0.4")):
            state["intent"] = "escalate"
            state["escalation_reason"] = "low_intent_confidence"
        return state

    async def _gemini_route(self, state: FlowState) -> tuple[Intent, float, Dict[str, Any]]:
        """
        Mandatory router: calls Gemini to produce JSON {intent, confidence, extracted}.
        extracted may include: order_id, refund_amount_usd.
        """
        t0 = time.monotonic()
        await self._events.emit(
            EventType.tool_call,
            session_id=state["session_id"],
            customer_id=state["customer_id"],
            payload={
                "tool": "gemini_route",
                "args": redact_dict({"message": state["message"]}, extra_sensitive_keys={"message"}),
            },
        )
        try:
            check_tool_allowed("gemini_route")
            prompt = (
                "You are a routing agent for an e-commerce support system.\n"
                "Classify the user message into exactly one intent from:\n"
                "order_tracking, refund, faq, escalate.\n"
                "Return ONLY JSON with shape:\n"
                "{\"intent\":\"...\",\"confidence\":0.0,\"extracted\":{\"order_id\":null,\"refund_amount_usd\":null}}\n"
                "Rules:\n"
                "- Use escalate if unclear/ambiguous.\n"
                "- If message contains an order id, extract it.\n"
                "- If message requests a refund amount, extract it as a number.\n"
                f"Message: {state['message']}\n"
            )

            def _call() -> str:
                resp = self._genai.models.generate_content(model=self._gemini_model, contents=prompt)
                return (resp.text or "").strip()

            text = await anyio.to_thread.run_sync(_call)

            import json

            data = json.loads(text)
            intent_raw = str(data.get("intent", "")).strip()
            confidence = float(data.get("confidence", 0.0))
            extracted = data.get("extracted") or {}
            if intent_raw not in ("order_tracking", "refund", "faq", "escalate"):
                intent_raw = "escalate"
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            if not isinstance(extracted, dict):
                extracted = {}
            return intent_raw, confidence, extracted
        except Exception as e:  # noqa: BLE001
            await self._events.emit(
                EventType.error,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={"component": "gemini_route", "error": type(e).__name__},
            )
            return "escalate", 0.0, {}
        finally:
            await self._events.emit(
                EventType.metric,
                session_id=state["session_id"],
                customer_id=state["customer_id"],
                payload={
                    "name": "tool_latency_ms",
                    "tool": "gemini_route",
                    "value": int((time.monotonic() - t0) * 1000),
                },
            )
