import json
import os
import tempfile
import unittest

from app.services.log_export import emit_structured_log, recent_logs


class LogExportTests(unittest.TestCase):
    def test_recent_logs_returns_emitted_event(self):
        emit_structured_log({"event_type": "test.event", "mission_id": "mission-a"})
        events = recent_logs(limit=5)
        self.assertTrue(any(event.get("event_type") == "test.event" for event in events))

    def test_export_writes_jsonl_when_path_configured(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            with unittest.mock.patch.dict(os.environ, {"MC_LOG_EXPORT_PATH": path}, clear=False):
                emit_structured_log({"event_type": "test.file", "mission_id": "mission-b"})
            with open(path, "r", encoding="utf-8") as fh:
                lines = [line.strip() for line in fh.readlines() if line.strip()]
            self.assertTrue(lines)
            parsed = json.loads(lines[-1])
            self.assertEqual(parsed.get("event_type"), "test.file")
            self.assertEqual(parsed.get("mission_id"), "mission-b")
            self.assertTrue(parsed.get("timestamp"))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
