import tempfile
import unittest
from pathlib import Path

from scripts.guardian_resource_summary import summarize_tsv
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

    def test_resource_summary_uses_nearest_rank_percentiles_and_accepts_pcpu(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "resources.tsv"
            path.write_text(
                "epoch_s\trss_kib\tpcpu\tfds\tthreads\n"
                "1\t10\t0.0\t3\t1\n"
                "2\t20\t1.0\t4\t1\n"
                "3\t30\t2.0\t5\t2\n"
                "4\t40\t\t6\t2\n",
                encoding="utf-8",
            )
            summary = summarize_tsv(path)
            self.assertEqual(summary["rows_read"], 4)
            self.assertEqual(summary["epoch"]["duration_seconds"], 3.0)
            self.assertEqual(summary["metrics"]["rss_kib"]["p95"], 40.0)
            self.assertEqual(summary["metrics"]["rss_kib"]["p99"], 40.0)
            self.assertEqual(summary["metrics"]["cpu_percent"]["samples"], 3)

    def test_resource_summary_bounds_input_and_drops_partial_last_row(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "resources.tsv"
            path.write_bytes(
                b"epoch_s\trss_kib\tcpu_percent\tfds\tthreads\n"
                b"1\t10\t0\t3\t1\n"
                b"2\t20\t0\t4\t1\n"
                b"3\t30\t0\t5\t1\n"
            )
            summary = summarize_tsv(path, max_bytes=58)
            self.assertTrue(summary["truncated"])
            self.assertLessEqual(summary["bytes_considered"], 58)
            self.assertLess(summary["rows_read"], 3)


if __name__ == "__main__":
    unittest.main()
