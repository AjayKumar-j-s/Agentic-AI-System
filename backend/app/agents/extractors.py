from __future__ import annotations

from typing import Any, Dict, Optional


def extract_order_id(text: str) -> Optional[str]:
    """Heuristic: finds ORDER-prefixed tokens (≥8 chars) or #number patterns."""
    tokens = text.replace("#", " #").split()
    for t in tokens:
        tt = t.strip().strip(",.()[]{}?!:;\"'")
        if tt.upper().startswith("ORDER") and len(tt) >= 8:
            return tt.upper()
        if tt.startswith("#") and tt[1:].isdigit():
            return f"ORDER{tt[1:]}"
    return None


def extract_order_id_from_customer(customer: Dict[str, Any]) -> Optional[str]:
    """Falls back to order_id stored in customer metadata fields."""
    for key in ("last_order_id", "order_id"):
        v = customer.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def extract_amount_usd(text: str) -> Optional[float]:
    """Heuristic: parses $75, USD 75, or '75 dollars' patterns."""
    cleaned = text.replace(",", "")
    parts = cleaned.split()
    for i, p in enumerate(parts):
        if p.startswith("$") and p[1:].replace(".", "", 1).isdigit():
            return float(p[1:])
        if p.upper() == "USD" and i + 1 < len(parts) and parts[i + 1].replace(".", "", 1).isdigit():
            return float(parts[i + 1])
        if p.isdigit() and i + 1 < len(parts) and parts[i + 1].lower().startswith("dollar"):
            return float(p)
    return None
