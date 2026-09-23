from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_pressure_gate import (
    DEGRADED,
    NORMAL,
    SUSPENDED,
    PressureDecision,
    PressureGate,
    slim_audit_event,
)


def _decision(state: str) -> PressureDecision:
    return PressureDecision(
        state=state,
        available_bytes=2048000000 if state == NORMAL else 50000000,
        total_bytes=4096000000,
        available_ratio=0.5 if state == NORMAL else 0.012,
        psi_full_avg10=0.0 if state == NORMAL else 30.0,
        reason_codes=() if state == NORMAL else ("memory_psi_critical",),
        transition=f"normal->{state}" if state != NORMAL else None,
        normal_samples=0,
    )


def _full_event() -> dict:
    return {
        "schema": "guardian.risk.event.v1",
        "event_id": "e-1",
        "sample_id": "sample-1",
        "observed_at": "2026-09-23T00:00:00Z",
        "observed_monotonic_ns": 1000,
        "host_id": "lab",
        "state": "escalated",
        "signals": {"memory": {"available_ratio_percent": 1.2}, "collector": {"stale": True}},
        "risk": {"state": "critical"},
        "object_candidates": ["a"],
        "object_attribution": {"state": "TARGET_CONFIRMED"},
        "resource_evaluations": {"memory": {"risk": {"state": "critical"}}},
        "joint_evaluation": {"state": "escalated"},
        "decision": {"action": "none"},
        "emergency_shedding": {"decision": {"action": "none"}},
        "target_attribution": None,
        "presentation": {"text": "x"},
        "evidence": {"sample_id": "sample-1"},
    }


class SlimAuditEventTests(unittest.TestCase):
    def test_normal_keeps_full_event(self):
        event = _full_event()
        out = slim_audit_event(event, _decision(NORMAL))
        self.assertEqual(out["audit_fidelity"], "full")
        self.assertIn("signals", out)
        self.assertIn("resource_evaluations", out)

    def test_degraded_writes_summary_with_declared_fidelity(self):
        out = slim_audit_event(_full_event(), _decision(DEGRADED))
        self.assertEqual(out["audit_fidelity"], "summary")
        self.assertNotIn("signals", out)
        self.assertNotIn("resource_evaluations", out)
        self.assertIn("risk", out)
        self.assertIn("decision", out)
        self.assertIn("signals", out["slimmed"]["dropped"])
        self.assertEqual(out["pressure_gate"]["state"], DEGRADED)

    def test_suspended_writes_minimal_payload(self):
        out = slim_audit_event(_full_event(), _decision(SUSPENDED))
        self.assertEqual(out["audit_fidelity"], "minimal")
        self.assertNotIn("signals", out)
        self.assertNotIn("resource_evaluations", out)
        # the alerting-path fields survive even the minimal form
        self.assertIn("state", out)
        self.assertIn("risk", out)
        self.assertIn("decision", out)
        self.assertIn("pressure_gate", out)

    def test_slimmed_event_is_materially_smaller(self):
        # realistic proportions: signals + evaluations dominate a production
        # event (~46KB); the fixture scales them up to match
        event = _full_event()
        event["signals"] = {"memory": {"available_ratio_percent": 1.2}, "detail": "x" * 20000}
        event["resource_evaluations"] = {"memory": {"risk": {"state": "critical"}, "detail": "y" * 20000}}
        full = json.dumps(event, ensure_ascii=False)
        slim = json.dumps(slim_audit_event(event, _decision(SUSPENDED)), ensure_ascii=False)
        self.assertLess(len(slim), len(full) / 5)

    def test_original_event_not_mutated(self):
        event = _full_event()
        slim_audit_event(event, _decision(DEGRADED))
        self.assertIn("signals", event)
        self.assertNotIn("audit_fidelity", event)


if __name__ == "__main__":
    unittest.main()
