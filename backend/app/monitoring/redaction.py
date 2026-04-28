from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Set


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(\+?\d[\d -]{7,}\d)\b")


def redact_text(value: str) -> str:
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = _PHONE_RE.sub("[REDACTED_PHONE]", value)
    return value


def redact_dict(d: Dict[str, Any], *, extra_sensitive_keys: Iterable[str] = ()) -> Dict[str, Any]:
    sensitive: Set[str] = {
        "email",
        "phone",
        "address",
        "name",
        "full_name",
        "customer",
        "payment",
        "card",
        "order_id",
        "tracking_number",
    } | set(extra_sensitive_keys)

    def _redact(v: Any, key: str | None = None) -> Any:
        if key is not None and key.lower() in sensitive:
            return "[REDACTED]"
        if isinstance(v, str):
            return redact_text(v)
        if isinstance(v, dict):
            return {kk: _redact(vv, kk) for kk, vv in v.items()}
        if isinstance(v, list):
            return [_redact(x, key) for x in v]
        return v

    return {k: _redact(v, k) for k, v in d.items()}

