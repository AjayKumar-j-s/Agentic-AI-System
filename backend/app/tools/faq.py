from __future__ import annotations


_FAQ = {
    "shipping policy": "Shipping usually takes 3–5 business days after dispatch. You’ll receive a tracking link once shipped.",
    "return policy": "Returns are accepted within 30 days if the item is unused and in original packaging. Refunds may take 3–5 business days to process.",
    "refund policy": "Refunds are processed after eligibility checks. Amounts above the auto-approval limit require human review.",
}


def faq_search(query: str) -> str:
    q = query.lower()
    for k, v in _FAQ.items():
        if k in q:
            return v
    return "I can help, but I need a bit more detail. Are you asking about shipping, returns, or refunds?"

