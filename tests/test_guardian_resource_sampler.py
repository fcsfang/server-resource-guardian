import tempfile
import unittest
from pathlib import Path

from scripts.guardian_resource_sampler import parse_proc_stat, parse_proc_status, sample_process


class GuardianResourceSamplerTests(unittest.TestCase):
    def test_parse_proc_status_reads_memory_and_threads(self):
        parsed = parse_proc_status("VmRSS: 1234 kB\nThreads: 2\nState: R (running)\n")
        self.assertEqual(parsed["VmRSS"], 1234)
        self.assertEqual(parsed["Threads"], 2)

    def test_parse_proc_stat_handles_spaces_in_process_name(self):
        fields = ["R"] + ["0"] * 19
        fields[11] = "7"
        fields[12] = "3"
        fields[19] = "99"
        parsed = parse_proc_stat("42 (guardian observer) " + " ".join(fields))
        self.assertEqual(parsed["utime_ticks"], 7)
        self.assertEqual(parsed["stime_ticks"], 3)
        self.assertEqual(parsed["starttime_ticks"], 99)

    def test_sampler_stops_when_target_fixture_is_missing_and_keeps_header_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "resources.tsv"
            result = sample_process(
                999999,
                output,
                interval_seconds=0.01,
                duration_seconds=0.1,
                max_bytes=128,
                proc_root=Path(temp),
                sleep=lambda _seconds: None,
            )
            self.assertEqual(result, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "epoch_s\trss_kib\tcpu_percent\tfds\tthreads\n")
            self.assertLessEqual(output.stat().st_size, 128)


if __name__ == "__main__":
    unittest.main()
