from __future__ import annotations

import unittest
import sys
from types import SimpleNamespace

from unittest.mock import patch

from garmin_activity_adapter import classify_error, interpret_import_result, probe, search


class ImportDispositionTests(unittest.TestCase):
    def test_explicit_success_with_activity_id_is_confirmed(self) -> None:
        result = interpret_import_result({"detailedImportResult": {"successes": [{"activityId": 12345}], "failures": []}})
        self.assertEqual("confirmed", result["state"])
        self.assertEqual("12345", result["remoteId"])

    def test_documented_failure_is_known_rejection(self) -> None:
        result = interpret_import_result({"detailedImportResult": {"successes": [], "failures": [{"messages": ["invalid FIT"]}]}})
        self.assertEqual({"state": "failed", "kind": "rejected", "message": "Garmin rejected the imported activity."}, result)

    def test_duplicate_is_terminal_known_failure(self) -> None:
        result = classify_error(RuntimeError("API Error 409 - duplicate"), upload_started=True)
        self.assertEqual("duplicate", result["kind"])
        self.assertNotIn("API Error", result["message"])

    def test_library_success_status_without_remote_id_is_confirmed(self) -> None:
        self.assertEqual({"state": "confirmed"}, interpret_import_result({"status": "uploaded"}))

    def test_fallback_empty_and_malformed_results_are_unknown(self) -> None:
        for payload in ({}, None, {"detailedImportResult": {"successes": [{}]}}):
            with self.subTest(payload=payload):
                self.assertEqual("unknown", interpret_import_result(payload)["state"])

    def test_transport_failure_after_send_is_unknown_and_redacted(self) -> None:
        result = classify_error(RuntimeError("socket failed token=secret"), upload_started=True)
        self.assertEqual("unknown", result["state"])
        self.assertNotIn("secret", result["message"])

    def test_missing_pinned_dependency_is_provider_unavailable(self) -> None:
        error = ModuleNotFoundError("No module named garminconnect", name="garminconnect")
        result = classify_error(error, upload_started=False)
        self.assertEqual("provider-unavailable", result["kind"])
        transitive = classify_error(ModuleNotFoundError("No module named curl_cffi", name="curl_cffi"), upload_started=False)
        self.assertEqual("provider-unavailable", transitive["kind"])

    @patch("garmin_activity_adapter.emit")
    def test_probe_imports_dependency_without_contacting_provider(self, emit) -> None:
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=object)}):
            probe()
        emit.assert_called_once_with({"state": "ready"})

    @patch("garmin_activity_adapter.emit")
    def test_watch_search_returns_bounded_treadmill_summary_and_heart_rate_curve(self, emit) -> None:
        class TokenClient:
            def dumps(self):
                return "x" * 128

        class FakeGarmin:
            def __init__(self, retry_attempts=0):
                self.client = TokenClient()
            def login(self, token_store):
                self.token_store = token_store
            def get_activities(self, start, limit, activitytype):
                return [{"activityId": 123, "activityType": {"typeKey": "treadmill_running"}, "startTimeGMT": "2026-08-05T08:00:20", "duration": 1200, "distance": 2500, "averageHR": 130, "maxHR": 155}]
            def get_activity_details(self, activity_id, maxchart, maxpoly):
                return {"samples": True}

        def parse_details(details):
            return [{"sumElapsedDuration": second, "directHeartRate": 120 + second} for second in range(20)]

        module = SimpleNamespace(Garmin=FakeGarmin, parse_activity_detail_metrics=parse_details)
        with patch.dict(sys.modules, {"garminconnect": module}):
            search({"tokenStore": "t" * 128, "startedAtUtc": "2026-08-05T08:00:00Z"})

        payload = emit.call_args.args[0]
        self.assertEqual("confirmed", payload["state"])
        self.assertEqual("123", payload["candidates"][0]["remoteId"])
        self.assertEqual(20, len(payload["candidates"][0]["heartRateSamples"]))


if __name__ == "__main__":
    unittest.main()
