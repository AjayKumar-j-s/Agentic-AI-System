import os
import unittest

from fastapi.testclient import TestClient


@unittest.skipUnless(os.getenv("GEMINI_API_KEY"), "Set GEMINI_API_KEY to run Gemini integration tests")
class GeminiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure we use the intended model.
        os.environ.setdefault("GEMINI_MODEL", "gemini-2.5-flash")
        os.environ.setdefault("REFUND_LIMIT_USD", "75")

        from backend.app.main import app

        cls.client = TestClient(app)

    def test_query_calls_gemini_and_emits_events(self):
        session_id = "s_it_gemini_1"
        payload = {
            "session_id": session_id,
            "customer_id": "c_it",
            "message": "What is your return policy?",
            "customer": {"email": "user@example.com"},
        }

        r = self.client.post("/query", json=payload)
        self.assertEqual(r.status_code, 200)

        ev = self.client.get("/events", params={"session_id": session_id, "limit": 500}).json()["events"]
        types = [e["type"] for e in ev]
        self.assertIn("orchestrator_decision", types)

        # We should see Gemini calls recorded as tool_call events.
        tool_calls = [e for e in ev if e["type"] == "tool_call"]
        tools = [tc.get("payload", {}).get("tool") for tc in tool_calls]
        self.assertIn("gemini_route", tools)
        self.assertIn("gemini_respond", tools)


if __name__ == "__main__":
    unittest.main()

