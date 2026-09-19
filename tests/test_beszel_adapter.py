import datetime as dt
import unittest
from unittest.mock import patch

from src.beszel_adapter import (
    AdapterError,
    AdapterTransportError,
    BeszelEventWindow,
    BeszelHttpClient,
    is_actionable_observation,
    normalize_beszel_event,
)


UTC = dt.timezone.utc
RECEIVED = dt.datetime(2026, 9, 19, 14, 0, 2, tzinfo=UTC)


class BeszelAdapterTests(unittest.TestCase):
    def fixture(self):
        return {
            "id": "alert-1",
            "observed_at": "2026-09-19T14:00:00Z",
            "hub_id": "local-poc",
            "system_id": "system-1",
            "system": {
                "name": "guardian-ubuntu",
                "host": "local-vm",
                "architecture": "aarch64",
            },
            "object": {
                "kind": "container",
                "id": "container-123",
                "name": "discardable-test-target",
            },
            "signal": {
                "resource": "memory",
                "metric": "available_ratio_percent",
                "value": 8.2,
                "unit": "percent",
                "severity": "critical",
                "reason_codes": ["host_memory_available_critical"],
            },
            "source_url": "http://127.0.0.1:8090/api/health",
            "password": "must-not-be-copied",
        }

    def test_normalizes_whitelisted_event_and_redacts_unknown_fields(self):
        event = normalize_beszel_event(
            self.fixture(),
            received_at=RECEIVED,
            now=RECEIVED,
            ttl_seconds=30,
        )
        self.assertEqual(event["schema"], "guardian.beszel.v1")
        self.assertEqual(event["source"]["record_id"], "alert-1")
        self.assertEqual(event["object"]["stable_id"], "container-123")
        self.assertTrue(event["integrity"]["redacted"])
        self.assertNotIn("password", event)
        self.assertEqual(event["evidence"]["source_url"], "http://127.0.0.1:8090/api/health")
        self.assertTrue(is_actionable_observation(event))

    def test_rejects_missing_or_reversed_timestamps(self):
        missing = self.fixture()
        del missing["observed_at"]
        with self.assertRaisesRegex(AdapterError, "observed_at_required"):
            normalize_beszel_event(missing, received_at=RECEIVED)

        with self.assertRaisesRegex(AdapterError, "received_before_observed"):
            normalize_beszel_event(
                self.fixture(),
                received_at=dt.datetime(2026, 9, 19, 13, 59, 59, tzinfo=UTC),
            )

    def test_stale_event_is_not_actionable(self):
        event = normalize_beszel_event(
            self.fixture(),
            received_at=RECEIVED,
            now=RECEIVED + dt.timedelta(seconds=31),
            ttl_seconds=30,
        )
        self.assertTrue(event["integrity"]["stale"])
        self.assertFalse(is_actionable_observation(event))

    def test_low_confidence_identity_is_not_actionable(self):
        payload = self.fixture()
        payload["object"].pop("id")
        event = normalize_beszel_event(
            payload,
            received_at=RECEIVED,
            now=RECEIVED,
        )
        self.assertEqual(event["object"]["identity_confidence"], "low")
        self.assertFalse(is_actionable_observation(event))

    def test_event_window_deduplicates_and_rejects_out_of_order_events(self):
        window = BeszelEventWindow(dedupe_seconds=60)
        first = normalize_beszel_event(
            self.fixture(),
            received_at=RECEIVED,
            now=RECEIVED,
        )
        newer_payload = self.fixture()
        newer_payload["id"] = "alert-2"
        newer_payload["observed_at"] = "2026-09-19T14:00:10Z"
        newer = normalize_beszel_event(
            newer_payload,
            received_at=RECEIVED + dt.timedelta(seconds=10),
            now=RECEIVED + dt.timedelta(seconds=10),
        )
        older_payload = self.fixture()
        older_payload["id"] = "alert-3"
        older = normalize_beszel_event(
            older_payload,
            received_at=RECEIVED + dt.timedelta(seconds=11),
            now=RECEIVED + dt.timedelta(seconds=11),
        )
        self.assertEqual(window.classify(first, now=RECEIVED), "accepted")
        self.assertEqual(window.classify(first, now=RECEIVED), "duplicate_event")
        self.assertEqual(
            window.classify(newer, now=RECEIVED + dt.timedelta(seconds=10)),
            "accepted",
        )
        self.assertEqual(
            window.classify(older, now=RECEIVED + dt.timedelta(seconds=11)),
            "out_of_order_event",
        )

    def test_http_client_sanitizes_transport_failures(self):
        with patch(
            "src.beszel_adapter.urllib.request.urlopen",
            side_effect=OSError("token=test-secret"),
        ):
            client = BeszelHttpClient("http://127.0.0.1:8090")
            with self.assertRaisesRegex(AdapterTransportError, "beszel_get_failed"):
                client.get_json("/api/health")

    @patch("src.beszel_adapter.urllib.request.urlopen")
    def test_http_client_is_get_only_and_does_not_leak_token(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"message":"API is healthy."}'
        client = BeszelHttpClient(
            "http://127.0.0.1:8090",
            token="test-token-that-must-not-be-logged",
        )
        result = client.get_json("/api/health")
        request = urlopen.call_args.args[0]
        self.assertEqual(result["message"], "API is healthy.")
        self.assertEqual(request.method, "GET")
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer test-token-that-must-not-be-logged",
        )
        with self.assertRaisesRegex(AdapterError, "api_path_required"):
            client.get_json("/health")


if __name__ == "__main__":
    unittest.main()
