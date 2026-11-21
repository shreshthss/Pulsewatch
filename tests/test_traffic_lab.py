"""Tests for traffic_lab generator and evaluate."""

import os
import tempfile
import unittest
from traffic_lab.evaluate import run_benchmark_comparison
from traffic_lab.generator import SyntheticTrafficGenerator, write_synthetic_dataset


class TestTrafficLab(unittest.TestCase):
    def test_deterministic_generator(self):
        gen1 = SyntheticTrafficGenerator(seed=123)
        gen2 = SyntheticTrafficGenerator(seed=123)

        recs1, gt1 = gen1.generate_benchmark_suite()
        recs2, gt2 = gen2.generate_benchmark_suite()

        self.assertEqual(len(recs1), len(recs2))
        self.assertEqual(gt1.expected_duplicates, gt2.expected_duplicates)
        self.assertEqual(recs1[0], recs2[0])
        self.assertEqual(recs1[-1], recs2[-1])

    def test_benchmark_comparison_execution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jsonl_path = os.path.join(tmp_dir, "benchmark.jsonl")
            gt_path = os.path.join(tmp_dir, "ground_truth.json")

            gt = write_synthetic_dataset(jsonl_path, gt_path, seed=42)
            results = run_benchmark_comparison(jsonl_path, gt)

            self.assertIn("pw_scan", results)
            self.assertIn("naive_baseline", results)
            pw_res = results["pw_scan"]
            # PW-Scan should achieve high recall on known anomalies
            self.assertTrue(pw_res["recall"] >= 0.5)


if __name__ == "__main__":
    unittest.main()
