import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.guardian_disk import (
    CapacityAttributionEvaluator,
    DiskCapacityPolicy,
    DiskCapacityRiskEvaluator,
    DiskIoAttributionEvaluator,
    IoPolicy,
    IoRiskEvaluator,
    build_disk_simulation_decision,
    collect_disk_sample,
    parse_diskstats,
    parse_io_stat,
    parse_mountinfo,
)


MOUNTINFO = (
    "36 25 8:1 / / rw,relatime - ext4 /dev/vda1 rw\n"
    "37 36 0:42 / /mnt/test\\040data rw,relatime - tmpfs tmpfs rw\n"
)


def statvfs(*, free_blocks: int, free_inodes: int, read_only: bool = False):
    return SimpleNamespace(
        f_frsize=1024,
        f_blocks=100,
        f_bavail=free_blocks,
        f_bfree=free_blocks,
        f_files=1000,
        f_ffree=free_inodes,
        f_flag=os.ST_RDONLY if read_only else 0,
    )


def disk_sample(*, free_blocks=20, free_inodes=100, psi="some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=0 avg60=0 avg300=0 total=0\n", device_io_ticks=0, device_weighted=0, object_bytes=0, observed_ns=1_000_000_000):
    return {
        "observed_monotonic_ns": observed_ns,
        "mounts": [
            {
                "configured_path": "/",
                "mount_point": "/",
                "mount_id": 36,
                "device": "8:1",
                "filesystem_type": "ext4",
                "source": "/dev/vda1",
                "read_only": False,
                "stats": {
                    "total_bytes": 100 * 1024,
                    "free_bytes": free_blocks * 1024,
                    "free_ratio_percent": free_blocks,
                    "total_inodes": 1000,
                    "free_inodes": free_inodes,
                    "free_inode_ratio_percent": free_inodes / 10,
                },
                "quality": {"status": "ok", "flags": []},
            }
        ],
        "io": {
            "psi": {
                "some": {"avg10": 0.0},
                "full": {"avg10": 0.0},
            },
            "devices": {
                "8:1": {
                    "name": "vda1",
                    "reads_completed": 10,
                    "writes_completed": 10,
                    "sectors_read": 100,
                    "sectors_written": 100,
                    "io_ticks_ms": device_io_ticks,
                    "weighted_io_ms": device_weighted,
                    "ios_in_progress": 0,
                }
            },
            "cgroup": {"io_stat": {"8:1": {"rbytes": object_bytes // 2, "wbytes": object_bytes // 2, "rios": 1, "wios": 1}}},
            "quality": {"status": "ok", "flags": []},
            "objects": [
                {
                    "id": "writer-a",
                    "name": "writer",
                    "cgroup_path": "/sys/fs/cgroup/writer-a",
                    "mapping_confidence": "high",
                    "io_stat": {"8:1": {"rbytes": object_bytes // 2, "wbytes": object_bytes // 2, "rios": 1, "wios": 1}},
                    "quality": {"status": "ok", "flags": []},
                }
            ],
        },
        "capacity_quality": {"status": "ok", "flags": []},
        "quality": {"status": "ok", "flags": []},
    }


class GuardianDiskTests(unittest.TestCase):
    def test_parse_mountinfo_unescapes_mount_point_and_preserves_device(self):
        parsed = parse_mountinfo(MOUNTINFO)
        self.assertEqual(parsed[1]["mount_point"], "/mnt/test data")
        self.assertEqual(parsed[0]["device"], "8:1")
        self.assertEqual(parsed[0]["filesystem_type"], "ext4")

    def test_parse_diskstats_and_cgroup_io_stat(self):
        diskstats = parse_diskstats(
            "8 1 vda1 10 2 100 3 20 4 200 5 0 6 7\n"
        )
        self.assertEqual(diskstats["8:1"]["sectors_written"], 200)
        self.assertEqual(diskstats["8:1"]["io_ticks_ms"], 6)
        io_stat = parse_io_stat("8:1 rbytes=100 wbytes=200 rios=3 wios=4\n")
        self.assertEqual(io_stat["8:1"]["wbytes"], 200)
        self.assertEqual(io_stat["8:1"]["wios"], 4)

    def test_collect_disk_sample_separates_capacity_and_io_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            (proc / "pressure").mkdir()
            (proc / "self").mkdir()
            (proc / "self" / "mountinfo").write_text(MOUNTINFO)
            (proc / "diskstats").write_text("8 1 vda1 10 2 100 3 20 4 200 5 0 6 7\n")
            (proc / "pressure" / "io").write_text("some avg10=2 avg60=1 avg300=0 total=3\nfull avg10=0.5 avg60=0 avg300=0 total=1\n")
            cgroup = proc / "cgroup"
            cgroup.mkdir()
            (cgroup / "io.stat").write_text("8:1 rbytes=100 wbytes=200 rios=3 wios=4\n")
            (cgroup / "io.pressure").write_text("some avg10=1 avg60=0 avg300=0 total=1\n")
            sample = collect_disk_sample(
                proc_root=proc,
                cgroup_root=cgroup,
                mount_points=("/",),
                statvfs_reader=lambda _path: statvfs(free_blocks=20, free_inodes=100),
            )
            self.assertEqual(sample["mounts"][0]["stats"]["free_bytes"], 20 * 1024)
            self.assertEqual(sample["io"]["psi"]["some"]["avg10"], 2.0)
            self.assertEqual(sample["io"]["cgroup"]["io_stat"]["8:1"]["wbytes"], 200)
            self.assertEqual(sample["capacity_quality"]["status"], "ok")
            self.assertEqual(sample["io"]["quality"]["status"], "ok")

    def test_capacity_evaluator_requires_dwell_and_emits_recovery(self):
        policy = DiskCapacityPolicy(
            warning_free_percent=20,
            critical_free_percent=10,
            warning_inode_free_percent=20,
            critical_inode_free_percent=10,
            warning_for_seconds=2,
            critical_for_seconds=3,
            required_samples=2,
        )
        evaluator = DiskCapacityRiskEvaluator(policy)
        first = evaluator.evaluate(disk_sample(free_blocks=30, free_inodes=300), now=0)
        self.assertEqual(first["state"], "degraded_observability")
        second = evaluator.evaluate(disk_sample(free_blocks=5, free_inodes=50, observed_ns=2_000_000_000), now=1)
        self.assertEqual(second["candidate_state"], "critical")
        self.assertEqual(second["state"], "normal")
        third = evaluator.evaluate(disk_sample(free_blocks=5, free_inodes=50, observed_ns=5_000_000_000), now=4)
        self.assertEqual(third["state"], "critical")
        recovered = evaluator.evaluate(disk_sample(free_blocks=50, free_inodes=500, observed_ns=6_000_000_000), now=5)
        self.assertEqual(recovered["state"], "recovered")

    def test_capacity_missing_mount_data_fails_closed(self):
        sample = disk_sample()
        sample["mounts"][0]["quality"] = {"status": "degraded", "flags": ["statvfs_missing"]}
        sample["mounts"][0]["stats"] = None
        result = DiskCapacityRiskEvaluator().evaluate(sample, now=0)
        self.assertEqual(result["state"], "degraded_observability")
        self.assertIn("capacity_mount_statvfs_missing", result["quality_flags"])

    def test_io_evaluator_separates_pressure_from_capacity_and_requires_dwell(self):
        policy = IoPolicy(
            warning_psi_some_avg10=1,
            critical_psi_full_avg10=0.5,
            warning_device_utilization_percent=50,
            critical_device_utilization_percent=80,
            warning_for_seconds=2,
            critical_for_seconds=3,
            required_samples=2,
        )
        evaluator = IoRiskEvaluator(policy)
        first = evaluator.evaluate(disk_sample(observed_ns=1_000_000_000), now=0)
        self.assertEqual(first["state"], "degraded_observability")
        second_sample = disk_sample(observed_ns=2_000_000_000, device_io_ticks=900, device_weighted=9000)
        second_sample["io"]["psi"]["some"]["avg10"] = 2
        second_sample["io"]["psi"]["full"]["avg10"] = 1
        second = evaluator.evaluate(second_sample, now=1)
        self.assertEqual(second["candidate_state"], "critical")
        self.assertEqual(second["state"], "normal")
        third_sample = disk_sample(observed_ns=5_000_000_000, device_io_ticks=1800, device_weighted=18000)
        third_sample["io"]["psi"]["some"]["avg10"] = 2
        third_sample["io"]["psi"]["full"]["avg10"] = 1
        third = evaluator.evaluate(third_sample, now=4)
        self.assertEqual(third["state"], "critical")
        self.assertNotIn("capacity", third["resource_kind"])

    def test_io_attribution_confirms_single_stable_writer_and_rejects_empty_registry(self):
        evaluator = DiskIoAttributionEvaluator()
        first = evaluator.evaluate(disk_sample(object_bytes=100, observed_ns=1_000_000_000), now=0)
        self.assertEqual(first["state"], "DEGRADED_OBSERVABILITY")
        second = evaluator.evaluate(disk_sample(object_bytes=10_100, observed_ns=2_000_000_000), now=1)
        self.assertEqual(second["state"], "TARGET_CONFIRMED")
        empty = disk_sample(object_bytes=10_100, observed_ns=3_000_000_000)
        empty["io"]["objects"] = []
        empty_result = DiskIoAttributionEvaluator().evaluate(empty, now=0)
        self.assertEqual(empty_result["state"], "DEGRADED_OBSERVABILITY")
        self.assertIn("io_object_registry_empty", empty_result["reason_codes"])

    def test_capacity_attribution_explicitly_abandons_without_writer_ownership(self):
        result = CapacityAttributionEvaluator().evaluate(disk_sample(), now=0)
        self.assertEqual(result["state"], "DEGRADED_OBSERVABILITY")
        self.assertIn("capacity_writer_ownership_unavailable", result["reason_codes"])

    def test_disk_simulation_never_executes_and_fail_closes(self):
        plan = build_disk_simulation_decision(
            {"state": "critical", "resource_kind": "io"},
            {"state": "TARGET_CONFIRMED"},
            resource_kind="io",
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(plan["action"], "graceful_stop")
        self.assertEqual(plan["execution"], "not_executed")
        degraded = build_disk_simulation_decision(
            {"state": "degraded_observability", "resource_kind": "disk_capacity"},
            {"state": "TARGET_CONFIRMED"},
            resource_kind="disk_capacity",
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(degraded["action"], "escalate")
        self.assertEqual(degraded["execution"], "not_executed")


if __name__ == "__main__":
    unittest.main()
