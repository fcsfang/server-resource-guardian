from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.guardian_pressure_gate import DEGRADED, NORMAL, SUSPENDED, PressureGate


class GuardianPressureGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "pressure").mkdir()
        self.meminfo = self.root / "meminfo"
        self.psi = self.root / "pressure" / "memory"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_signals(self, available_kib: int, total_kib: int = 4096000, psi: float = 0.0) -> None:
        self.meminfo.write_text(
            f"MemTotal:       {total_kib} kB\nMemAvailable:   {available_kib} kB\n",
            encoding="utf-8",
        )
        self.psi.write_text(f"some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10={psi:.2f} avg60=0.00 avg300=0.00 total=0\n", encoding="utf-8")

    def test_normal_degraded_and_suspended_states(self):
        self.write_signals(2048000)
        gate = PressureGate(proc_root=self.root)
        self.assertEqual(gate.evaluate().state, NORMAL)

        self.write_signals(300000)
        self.assertEqual(gate.evaluate().state, DEGRADED)

        self.write_signals(50000, psi=30.0)
        decision = gate.evaluate()
        self.assertEqual(decision.state, SUSPENDED)
        self.assertFalse(decision.allow_container_collection)
        self.assertIn("memory_psi_critical", decision.reason_codes)

    def test_recovery_requires_three_normal_samples(self):
        self.write_signals(50000)
        gate = PressureGate(proc_root=self.root)
        self.assertEqual(gate.evaluate().state, SUSPENDED)
        self.write_signals(2048000)
        self.assertEqual(gate.evaluate().state, SUSPENDED)
        self.assertEqual(gate.evaluate().state, SUSPENDED)
        decision = gate.evaluate()
        self.assertEqual(decision.state, NORMAL)
        self.assertEqual(decision.normal_samples, 3)

    def test_missing_memory_signal_fails_closed_for_container_collection(self):
        gate = PressureGate(proc_root=self.root)
        decision = gate.evaluate()
        self.assertEqual(decision.state, DEGRADED)
        self.assertIn("memory_signal_unavailable", decision.reason_codes)


if __name__ == "__main__":
    unittest.main()
