import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_audit import verify_audit_file


class GuardianAuditTests(unittest.TestCase):
    def test_valid_jsonl_with_unique_event_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(
                json.dumps({"event_id": "one", "state": "normal"}) + "\n"
                + json.dumps({"event_id": "two", "state": "normal"}) + "\n",
                encoding="utf-8",
            )
            result = verify_audit_file(path, max_bytes=1024)
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["valid_json_records"], 2)

    def test_malformed_and_missing_identity_are_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text('{"event_id":"one"}\nnot-json\n{"state":"normal"}\n', encoding="utf-8")
            result = verify_audit_file(path)
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(result["invalid_json_records"], 1)
            self.assertEqual(result["missing_event_ids"], 1)

    def test_duplicate_event_id_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            payload = json.dumps({"event_id": "same", "state": "normal"}) + "\n"
            path.write_text(payload + payload, encoding="utf-8")
            result = verify_audit_file(path)
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(result["duplicate_event_ids"], 1)

    def test_capacity_overrun_is_invalid_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(json.dumps({"event_id": "one"}) + "\n", encoding="utf-8")
            before = path.read_bytes()
            result = verify_audit_file(path, max_bytes=len(before) - 1)
            self.assertEqual(result["status"], "invalid")
            self.assertFalse(result["capacity_ok"])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
