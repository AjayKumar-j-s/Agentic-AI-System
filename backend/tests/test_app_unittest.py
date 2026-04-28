import os
import unittest
import uuid

from fastapi.testclient import TestClient


class _FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeGeminiModels:
    def __init__(self):
        self._last_prompt = None

    def generate_content(self, model: str, contents: str):
        self._last_prompt = contents

        # Route calls are identified by the prompt content containing the router JSON shape.
        if "You are a routing agent for an e-commerce support system." in contents:
            if "Refund" in contents or "refund" in contents:
                return _FakeGeminiResponse(
                    '{"intent":"refund","confidence":0.9,"extracted":{"order_id":"ORDER12345","refund_amount_usd":100}}'
                )
            if "order" in contents.lower() or "track" in contents.lower():
                return _FakeGeminiResponse(
                    '{"intent":"order_tracking","confidence":0.9,"extracted":{"order_id":"ORDER11111","refund_amount_usd":null}}'
                )
            return _FakeGeminiResponse(
                '{"intent":"faq","confidence":0.8,"extracted":{"order_id":null,"refund_amount_usd":null}}'
            )

        # Otherwise it's a response-generation prompt.
        if "Task: escalation_response" in contents:
            return _FakeGeminiResponse("Escalated to a human support agent.")
        if "Task: refund_approved_response" in contents:
            return _FakeGeminiResponse("Refund approved.")
        if "Task: refund_denied_response" in contents:
            return _FakeGeminiResponse("Refund is not eligible.")
        if "Task: order_tracking_response" in contents:
            return _FakeGeminiResponse("Your order is on the way.")
        return _FakeGeminiResponse("OK")


class _FakeGeminiClient:
    def __init__(self, api_key: str):
        self.models = _FakeGeminiModels()


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Set required env vars before importing the app.
        os.environ["GEMINI_API_KEY"] = "test_dummy_key"
        os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
        os.environ["REFUND_LIMIT_USD"] = "75"
        os.environ["ADMIN_API_KEY"] = "admin_test_key"

        # Monkeypatch google.genai.Client before the app module is imported.
        import google.genai  # type: ignore

        google.genai.Client = _FakeGeminiClient  # type: ignore[attr-defined]

        from backend.app.main import app

        cls.client = TestClient(app)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_refund_limit(self, limit: float = 75.0) -> None:
        """Resets the DB-backed refund limit to `limit` between tests."""
        r = self.client.put(
            "/config/refund-limit",
            json={"refund_limit_usd": limit},
            headers={"x-admin-key": "admin_test_key"},
        )
        self.assertEqual(r.status_code, 200)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_refund_over_default_limit_escalates(self):
        """Refund of $100 with the default $75 limit must trigger a guardrail_violation
        and escalate — this is the primary guardrail correctness test."""
        self._reset_refund_limit(75.0)  # ensure default limit is in DB

        session_id = f"s_test_refund_over_{uuid.uuid4().hex[:8]}"
        payload = {
            "session_id": session_id,
            "customer_id": "c_test",
            "message": "Refund USD 100 for ORDER12345",
            "customer": {"email": "user@example.com"},
        }
        r = self.client.post("/query", json=payload)
        self.assertEqual(r.status_code, 200)
        body = r.json()

        # Must escalate because $100 > $75.
        self.assertTrue(body["escalated"], "Expected escalation when refund exceeds limit")
        self.assertIn("refund_requires_human_signoff", body.get("escalation_reason", ""))

        ev = self.client.get("/events", params={"session_id": session_id, "limit": 200}).json()["events"]
        types = [e["type"] for e in ev]
        self.assertIn("guardrail_violation", types, "Expected guardrail_violation event in audit log")

    def test_refund_dynamic_limit_allows_approval(self):
        """When the refund limit is raised to $150, a $100 refund should be approved
        (no escalation, no guardrail_violation)."""
        self._reset_refund_limit(150.0)

        r0g = self.client.get("/config/refund-limit", headers={"x-admin-key": "admin_test_key"})
        self.assertEqual(r0g.status_code, 200)
        self.assertEqual(r0g.json()["refund_limit_usd"], 150.0)

        session_id = f"s_test_refund_dyn_{uuid.uuid4().hex[:8]}"
        payload = {
            "session_id": session_id,
            "customer_id": "c_test",
            "message": "Refund USD 100 for ORDER12345",
            "customer": {"email": "user@example.com"},
        }
        r = self.client.post("/query", json=payload)
        self.assertEqual(r.status_code, 200)
        body = r.json()

        # Must NOT escalate because $100 ≤ $150.
        self.assertFalse(body["escalated"], "Expected approval when refund is within raised limit")

        ev = self.client.get("/events", params={"session_id": session_id, "limit": 200}).json()["events"]
        types = [e["type"] for e in ev]
        self.assertNotIn("guardrail_violation", types, "Expected no guardrail_violation when within limit")

        # Clean up — restore default so other tests are unaffected.
        self._reset_refund_limit(75.0)

    def test_events_persisted(self):
        """Events produced during a query must be readable via GET /events."""
        self._reset_refund_limit(75.0)
        session_id = f"s_test_events_{uuid.uuid4().hex[:8]}"
        payload = {
            "session_id": session_id,
            "customer_id": "c_test",
            "message": "Where is my order ORDER99999?",
        }
        r = self.client.post("/query", json=payload)
        self.assertEqual(r.status_code, 200)

        ev_resp = self.client.get("/events", params={"session_id": session_id, "limit": 200}).json()
        self.assertIn("events", ev_resp)
        self.assertGreater(len(ev_resp["events"]), 0, "Expected at least one event in the audit log")


if __name__ == "__main__":
    unittest.main()
