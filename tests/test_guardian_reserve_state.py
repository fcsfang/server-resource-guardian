import os
import tempfile
import threading
import unittest
from pathlib import Path

from src.guardian_reserve_state import ReserveBrokerStateStore


class ReserveBrokerStateTests(unittest.TestCase):
    def test_approval_is_consumed_once_and_unknown_keeps_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReserveBrokerStateStore(Path(temp) / "reserve-broker" / "state.db")
            store.register_capability(
                approval_id="approval-1",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
            )
            first = store.claim_execution(
                incident_id="incident-1",
                approval_id="approval-1",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
                target="/var/lib/guardian/reserve",
                now=100,
            )
            self.assertTrue(first.claimed)
            store.finish_execution(
                incident_id="incident-1",
                state="UNKNOWN",
                result={"reason": "helper_timeout"},
                now=101,
            )
            second = store.claim_execution(
                incident_id="incident-2",
                approval_id="approval-1",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
                target="/var/lib/guardian/reserve",
                now=102,
            )
            self.assertFalse(second.claimed)
            self.assertEqual(second.reason, "reserve_approval_already_consumed")

    def test_concurrent_claims_have_one_winner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reserve-broker" / "state.db"
            store = ReserveBrokerStateStore(path)
            store.register_capability(
                approval_id="approval-2",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
            )
            results = []
            barrier = threading.Barrier(2)

            def claim(incident_id):
                barrier.wait()
                results.append(
                    store.claim_execution(
                        incident_id=incident_id,
                        approval_id="approval-2",
                        environment="local-disposable",
                        target_id="/var/lib/guardian/reserve",
                        action="release_emergency_reserve",
                        expires_at=4102444800,
                        target="/var/lib/guardian/reserve",
                        now=200,
                    )
                )

            threads = [threading.Thread(target=claim, args=(f"incident-{index}",)) for index in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(result.claimed for result in results), 1)

    def test_different_approvals_cannot_bypass_the_global_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReserveBrokerStateStore(Path(temp) / "reserve-broker" / "state.db")
            for approval_id in ("approval-a", "approval-b"):
                store.register_capability(
                    approval_id=approval_id,
                    environment="local-disposable",
                    target_id="/var/lib/guardian/reserve",
                    action="release_emergency_reserve",
                    expires_at=4102444800,
                )
            first = store.claim_execution(
                incident_id="incident-a",
                approval_id="approval-a",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
                target="/var/lib/guardian/reserve",
                now=300,
            )
            second = store.claim_execution(
                incident_id="incident-b",
                approval_id="approval-b",
                environment="local-disposable",
                target_id="/var/lib/guardian/reserve",
                action="release_emergency_reserve",
                expires_at=4102444800,
                target="/var/lib/guardian/reserve",
                now=301,
            )
            self.assertTrue(first.claimed)
            self.assertFalse(second.claimed)
            self.assertEqual(second.reason, "reserve_active_slot_occupied")

    def test_state_directory_is_root_only_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "reserve-broker"
            ReserveBrokerStateStore(directory / "state.db")
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(directory / "state.db").st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
