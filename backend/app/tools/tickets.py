from __future__ import annotations

import time
import uuid
from typing import Any, Dict


def create_human_ticket(*, summary: str, reason: str, redacted_context: Dict[str, Any]) -> Dict[str, Any]:
    # Mock ticket creation; replace with Zendesk/Freshdesk/etc.
    ticket_id = f"tkt_{uuid.uuid4().hex[:10]}"
    return {"ticket_id": ticket_id, "created_at": int(time.time()), "reason": reason, "summary": summary, "context": redacted_context}

