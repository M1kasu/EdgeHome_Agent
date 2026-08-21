from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from spacebutler.home_assistant import HomeAssistantClient, HomeAssistantRuntime
from spacebutler.models import ExecutionStatus, PlanAction, PlanPriority, ServicePlan
from spacebutler.runtime import execute_and_verify


class FakeLightClient:
    def __init__(self, brightness_pct: int) -> None:
        self._brightness_pct = brightness_pct
        self._called = False
        self.service_calls: list[tuple[str, str, dict[str, object]]] = []

    def state(self, entity_id: str) -> dict[str, object]:
        return {
            "entity_id": entity_id,
            "state": "on" if self._called else "off",
            "attributes": {
                "brightness": round(self._brightness_pct * 255 / 100) if self._called else 0,
            },
        }

    def call_service(self, domain: str, service: str, data: dict[str, object]) -> None:
        self.service_calls.append((domain, service, data))
        self._called = True


class FakeProtocolFeedbackClient:
    def __init__(self, feedback_status: str, reason: str = "", *, change_feedback: bool = True) -> None:
        self._called = False
        self._feedback_status = feedback_status
        self._reason = reason
        self._change_feedback = change_feedback

    def state(self, entity_id: str) -> dict[str, object]:
        if entity_id.startswith("sensor."):
            changed = self._called and self._change_feedback
            status = self._feedback_status if changed else "ok"
            attributes = {"updated_at": "2" if changed else "1"}
            if changed and self._reason:
                attributes["reason"] = self._reason
            return {"entity_id": entity_id, "state": status, "attributes": attributes}
        return {
            "entity_id": entity_id,
            "state": "cool",
            "attributes": {"temperature": 24},
        }

    def call_service(self, _domain: str, _service: str, _data: dict[str, object]) -> None:
        self._called = True


class HomeAssistantAuthenticationTests(unittest.TestCase):
    def test_unauthorized_request_refreshes_token_and_retries_once(self) -> None:
        requests = []
        refresh_calls = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    BytesIO(b"401: Unauthorized"),
                )
            return BytesIO(b'{"message":"API running."}')

        def refresh_token() -> str:
            refresh_calls.append(True)
            return "new-token"

        client = HomeAssistantClient(
            "http://home-assistant.local",
            "old-token",
            token_refresher=refresh_token,
        )

        with patch("spacebutler.home_assistant.urlopen", side_effect=fake_urlopen):
            result = client._request("GET", "/api/")

        self.assertEqual(result, {"message": "API running."})
        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer old-token")
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer new-token")

    def test_light_brightness_readback_is_verified_in_percent(self) -> None:
        for brightness_pct in (25, 55, 100):
            with self.subTest(brightness_pct=brightness_pct):
                client = FakeLightClient(brightness_pct)
                runtime = HomeAssistantRuntime(client)
                action = PlanAction(
                    entity_id="light.spacebutler_bedroom_reading_light",
                    capability="set_brightness",
                    value=brightness_pct,
                    reason="test percentage normalization",
                )
                plan = ServicePlan(
                    plan_id=f"brightness-{brightness_pct}",
                    title="Brightness verification",
                    priority=PlanPriority.COMFORT,
                    proactive=False,
                    target_members=(),
                    actions=(action,),
                    explanation="Verify Home Assistant's 0-255 brightness as a percentage.",
                )

                report = execute_and_verify(runtime, plan)

                self.assertTrue(report.verified)
                self.assertEqual(report.status.value, "success")
                self.assertEqual(report.results[0].after.attributes["brightness_pct"], brightness_pct)
                self.assertEqual(
                    client.service_calls,
                    [
                        (
                            "light",
                            "turn_on",
                            {
                                "entity_id": "light.spacebutler_bedroom_reading_light",
                                "brightness_pct": brightness_pct,
                            },
                        )
                    ],
                )

    def test_rejected_protocol_feedback_is_execution_failed(self) -> None:
        report = _execute_fake_climate(FakeProtocolFeedbackClient("rejected", "fault_mode=reject"))

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.EXECUTION_FAILED)
        self.assertIn("fault_mode=reject", report.results[0].message)

    def test_ack_without_state_change_protocol_feedback_is_validation_failed(self) -> None:
        report = _execute_fake_climate(
            FakeProtocolFeedbackClient("ack_without_state_change", "fault_mode=ack_without_state_change")
        )

        self.assertTrue(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.VALIDATION_FAILED)
        self.assertIn("ack_without_state_change", report.results[0].message)

    def test_missing_protocol_feedback_change_is_timeout(self) -> None:
        report = _execute_fake_climate(FakeProtocolFeedbackClient("ok", change_feedback=False))

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.TIMEOUT)
        self.assertIn("did not reach target", report.results[0].message)

    def test_invalid_state_protocol_feedback_is_validation_failed(self) -> None:
        report = _execute_fake_climate(FakeProtocolFeedbackClient("invalid_state", "fault_mode=invalid_state"))

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.VALIDATION_FAILED)
        self.assertIn("invalid_state", report.results[0].message)


def _execute_fake_climate(client: FakeProtocolFeedbackClient):
    action = PlanAction(
        entity_id="climate.spacebutler_living_room_ac",
        capability="turn_off",
        value=True,
        reason="test protocol feedback",
    )
    plan = ServicePlan(
        plan_id="protocol-feedback",
        title="Protocol feedback",
        priority=PlanPriority.ENERGY,
        proactive=False,
        target_members=(),
        actions=(action,),
        explanation="Verify protocol feedback status mapping.",
    )
    return execute_and_verify(
        HomeAssistantRuntime(client, verification_timeout_seconds=0.05, poll_interval_seconds=0.01),
        plan,
    )


if __name__ == "__main__":
    unittest.main()
