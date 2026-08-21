from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_and_chat_endpoints(self) -> None:
        health = self.client.get("/healthz")
        chat = self.client.post("/api/agent/chat", json={"session_id": "api-test", "text": "把客厅灯调到一半"})

        self.assertEqual(200, health.status_code)
        self.assertEqual({"status": "ok"}, health.json())
        self.assertEqual(200, chat.status_code)
        self.assertTrue(chat.json()["execution_results"][0]["verified"])

    def test_task_endpoint_after_delayed_command(self) -> None:
        created = self.client.post("/api/agent/chat", json={"session_id": "api-delay", "text": "半小时后关闭客厅空调"}).json()
        task = self.client.get(f"/api/tasks/{created['task_id']}")

        self.assertIsNotNone(created["task_id"])
        self.assertEqual(200, task.status_code)
        self.assertEqual("scheduled", task.json()["status"])
