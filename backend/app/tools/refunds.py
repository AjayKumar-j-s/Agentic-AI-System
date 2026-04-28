from __future__ import annotations

import uuid
from typing import Dict


def calculate_refund_eligibility(order_id: str) -> Dict[str, object]:
    # Mock eligibility: always eligible up to $120.
    return {"order_id": order_id, "eligible": True, "reason": None, "max_refund_usd": 120.0}


def issue_refund(*, order_id: str, amount_usd: float) -> Dict[str, object]:
    # Mock issuance: returns a generated refund id.
    return {"order_id": order_id, "amount_usd": amount_usd, "refund_id": f"rf_{uuid.uuid4().hex[:12]}"}

