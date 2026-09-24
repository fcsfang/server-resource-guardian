"""Tests for gateway v3 message depth (production message content)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.feishu_alert_gateway import (  # noqa: E402
    CRITICAL,
    OK,
    WARNING,
    EarlyoomWatcher,
    Evaluator,
    message_text,
)


def base_event():
    return {
        "event_id": "12345678-aaaa-bbbb-cccc-dddddddddddd",
        "audit_fidelity": "full",
        "signals": {
            "memory": {
                "available_ratio_percent": 11.0,
                "available_bytes": 361 * 1024 * 1024,
                "total_bytes": 3812 * 1024 * 1024,
                "swap_used_ratio_percent": 25.8,
                "swap_free_bytes": 1518 * 1024 * 1024,
                "swap_total_bytes": 2048 * 1024 * 1024,
            },
            "psi": {"memory": {"some": {"avg10": 19.5}, "full": {"avg10": 9.9}}},
        },
        "risk": {"oom_events_delta": 2},
    }


class RichAlertMessageTests(unittest.TestCase):
    def test_memory_alert_carries_absolute_bytes_psi_and_oom(self):
        ev = Evaluator(confirm=1)
        fired = ev.evaluate(base_event())
        # memory warning should fire (avail 11% < 15%)
        fired_rows = [f for f in fired if f[0] == "memory"]
        self.assertTrue(fired_rows, "memory warning should fire")
        _key, old, new, rk, eid = fired_rows[0]
        text = message_text(rk, old, new, eid)
        self.assertIn("[Guardian 告警]", text)
        self.assertIn("可用 11.0%", text)
        self.assertIn("361", text)          # absolute available MiB value
        self.assertIn("3.7GiB", text)       # total rendered in GiB
        self.assertIn("swap 已用 25.8%", text)
        self.assertIn("PSI-mem", text)
        self.assertIn("OOM 事件 2", text)
        self.assertIn("[12345678]", text)   # event id tag

    def test_swap_alert_cross_references_memory(self):
        ev = Evaluator(confirm=1)
        fired = ev.evaluate(base_event())
        swap_rows = [f for f in fired if f[0] == "swap"]
        self.assertTrue(swap_rows)
        _key, old, new, rk, eid = swap_rows[0]
        text = message_text(rk, old, new, eid)
        self.assertIn("剩 1.5GiB/2.0GiB", text)
        self.assertIn("内存可用 11.0%", text)

    def test_single_sample_spike_still_alerts_fast_alarm_slow_clear(self):
        # regression for the 15:08 review: a real earlyoom episode lasted one
        # sample per level; confirm=2 swallowed it entirely. Alarm must fire
        # on the first sample; recovery still requires 2 consecutive ok.
        ev = Evaluator(confirm=2)
        ev.evaluate(base_event())
        rk = ev.keys["memory"]
        spike = base_event()
        spike["signals"]["memory"]["available_ratio_percent"] = 9.0  # one-sample critical
        fired = ev.evaluate(spike)
        rows = [f for f in fired if f[0] == "memory"]
        self.assertTrue(rows, "single-sample critical must alert (fast alarm)")
        _key, old, new, _rk, _eid = rows[0]
        self.assertEqual(new, CRITICAL)
        # recovery still debounced
        ok1 = base_event()
        ok1["signals"]["memory"]["available_ratio_percent"] = 79.5
        fired1 = ev.evaluate(ok1)
        self.assertFalse([f for f in fired1 if f[0] == "memory"], "one ok sample must not clear")
        fired2 = ev.evaluate(ok1)
        self.assertTrue([f for f in fired2 if f[0] == "memory"], "second ok sample clears")

    def test_recovery_message_has_duration_and_kill_summary(self):
        ev = Evaluator(confirm=1)
        ev.evaluate(base_event())  # engage WARNING
        rk = ev.keys["memory"]
        rk.became_level_at = __import__("time").time() - 10
        recovered = base_event()
        recovered["signals"]["memory"]["available_ratio_percent"] = 79.5
        recovered["signals"]["memory"]["swap_used_ratio_percent"] = 4.3
        fired = ev.evaluate(recovered)
        rows = [f for f in fired if f[0] == "memory"]
        self.assertTrue(rows, "recovery should fire")
        _key, old, new, rk, eid = rows[0]
        self.assertEqual(new, OK)
        with patch.object(EarlyoomWatcher, "kill_summary", return_value="earlyoom 击杀 2×: pytest(TERM) 2948 MiB / pytest(TERM) 2863 MiB"):
            text = message_text(rk, old, new, eid, "earlyoom 击杀 2×: pytest(TERM) 2948 MiB / pytest(TERM) 2863 MiB")
        self.assertIn("[Guardian 恢复]", text)
        self.assertIn("earlyoom 击杀 2×", text)
        self.assertIn("pytest", text)
        self.assertIn("~10s", text)  # duration line

    def test_recovery_without_kill_data_stays_clean(self):
        ev = Evaluator(confirm=1)
        ev.evaluate(base_event())
        rk = ev.keys["memory"]
        recovered = base_event()
        recovered["signals"]["memory"]["available_ratio_percent"] = 79.5
        fired = ev.evaluate(recovered)
        rows = [f for f in fired if f[0] == "memory"]
        _key, old, new, rk, eid = rows[0]
        text = message_text(rk, old, new, eid, "")
        self.assertNotIn("earlyoom", text)  # no fabricated explanation

    def test_earlyoom_watcher_parses_journal_line(self):
        watcher = EarlyoomWatcher(unit="earlyoom")
        fake = (
            "2026-09-24T14:02:30+08:00 HCT sh[123]: sending SIGTERM to process 344101 "
            'uid 0 "pytest": oom_score 1052, oom_score_adj 0, VmRSS 2948 MiB, '
            'cmdline "python3 -m pytest -q --tb=no"'
        )
        with patch("subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": fake, "returncode": 0})()
            summary = watcher.kill_summary(None)
        self.assertIn("earlyoom 击杀 1×", summary)
        self.assertIn("pytest", summary)
        self.assertIn("2948", summary)

    def test_earlyoom_watcher_empty_on_no_kills(self):
        watcher = EarlyoomWatcher(unit="earlyoom")
        with patch("subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": "", "returncode": 0})()
            self.assertEqual(watcher.kill_summary(None), "")

    def test_poll_kill_alerts_dedupes_and_formats(self):
        watcher = EarlyoomWatcher(unit="earlyoom", poll_seconds=0)
        fake = (
            '2026-09-24T15:16:01+08:00 HCT sh[1]: sending SIGTERM to process 395353 '
            'uid 0 "python3": oom_score 984, oom_score_adj 0, VmRSS 2851 MiB, cmdline "python3 -"'
        )
        with patch("subprocess.run") as run:
            run.return_value = type("R", (), {"stdout": fake, "returncode": 0})()
            first = watcher.poll_kill_alerts()
            second = watcher.poll_kill_alerts()
        self.assertEqual(len(first), 1)
        self.assertIn("earlyoom 内存保护触发", first[0])
        self.assertIn("SIGTERM 击杀 python3", first[0])
        self.assertIn("2851", first[0])
        self.assertEqual(second, [], "same kill must not alert twice")

    def test_poll_kill_alerts_respects_poll_interval(self):
        import time as _time
        watcher = EarlyoomWatcher(unit="earlyoom", poll_seconds=3600)
        watcher._last_poll = _time.time()  # just polled
        with patch("subprocess.run") as run:
            alerts = watcher.poll_kill_alerts()
        self.assertEqual(alerts, [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
