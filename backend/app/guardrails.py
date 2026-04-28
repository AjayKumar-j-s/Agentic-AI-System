from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


class GuardrailBlocked(RuntimeError):
    pass


# Tool allowlist guardrail — every tool callable by any agent must appear here.
# No code path may invoke a tool outside this set without a deliberate code change.
ALLOWED_TOOLS: FrozenSet[str] = frozenset(
    {
        "get_order_status",
        "get_shipment_tracking",
        "calculate_refund_eligibility",
        "issue_refund",
        "faq_search",
        "create_human_ticket",
        "gemini_route",
        "gemini_respond",
    }
)

def check_tool_allowed(tool_name: str) -> None:
    if tool_name not in ALLOWED_TOOLS:
        raise GuardrailBlocked(f"Tool {tool_name} is not in the ALLOWED_TOOLS list.")



@dataclass(frozen=True)
class Guardrails:
    refund_limit_usd: float

    def check_refund_amount(self, amount_usd: float) -> None:
        if amount_usd > self.refund_limit_usd:
            raise GuardrailBlocked(f"Refund amount {amount_usd} exceeds limit {self.refund_limit_usd}")
