"""Tests for pulsewatch.report and pulsewatch.html_report."""

import json
import unittest
from pulsewatch.dedup import DedupAccounting
from pulsewatch.detector import BaselineProfile, DetectionCandidate
from pulsewatch.html_report import render_html_report
from pulsewatch.incident import IncidentMerger
from pulsewatch.ingest import IngestAccounting
from pulsewatch.report import (
    compute_deterministic_hash,
    format_cli_summary,
    generate_canonical_report,
    sanitize_for_json,
)
from pulsewatch.temporal import CoverageReport, TemporalEngine


class TestReport(unittest.TestCase):
    def test_sanitize_and_hash_determinism(self):
        data = {
            "val": float("nan"),
            "inf": float("inf"),
            "normal": 42.5,
            "nested": {"key": "test"}
        }
        sanitized = sanitize_for_json(data)
        self.assertIsNone(sanitized["val"])
        self.assertIsNone(sanitized["inf"])
        self.assertEqual(sanitized["normal"], 42.5)

        # Hash determinism
        hash1 = compute_deterministic_hash(sanitized)
        hash2 = compute_deterministic_hash(sanitized)
        self.assertEqual(hash1, hash2)

    def test_canonical_report_generation(self):
        ing_acc = IngestAccounting(total_lines_read=100, valid_records=95)
        ded_acc = DedupAccounting(total_input_records=95, retained_records=90, duplicate_records_removed=5)
        cov = CoverageReport(
            earliest_sec=1000,
            latest_sec=2000,
            total_span_sec=1001,
            active_seconds=500,
            assumed_zero_seconds=501,
            unknown_gap_seconds=0,
            coverage_ratio=1.0,
            unknown_gaps=[]
        )
        base = BaselineProfile(
            median_rate_1s=2.0,
            mad_1s=0.5,
            dispersion_1s=1.0,
            reliability="high"
        )
        report = generate_canonical_report(
            input_file="test_input.jsonl",
            ingest_acc=ing_acc,
            dedup_acc=ded_acc,
            coverage=cov,
            windows_60s=[],
            baseline=base,
            candidates=[],
            incidents=[]
        )

        self.assertEqual(report["schema_version"], "pulsewatch.report/1")
        self.assertIn("analysis_hash", report)
        self.assertEqual(len(report["analysis_hash"]), 64)

        # Verify JSON serializability without errors
        serialized = json.dumps(report)
        self.assertIn("pulsewatch.report/1", serialized)

    def test_hostile_strings_escaped(self):
        # Ensure malicious script tags or control characters are safely escaped
        hostile_file = "<script>alert('xss')</script>"
        ing_acc = IngestAccounting()
        ded_acc = DedupAccounting()
        cov = CoverageReport(None, None, 0, 0, 0, 0, 0.0)
        base = BaselineProfile(0.0, 0.0, 0.0, "abstain", abstain_reason="<img src=x onerror=alert(1)>")

        report = generate_canonical_report(
            input_file=hostile_file,
            ingest_acc=ing_acc,
            dedup_acc=ded_acc,
            coverage=cov,
            windows_60s=[],
            baseline=base,
            candidates=[],
            incidents=[]
        )

        cli_out = format_cli_summary(report)
        self.assertIn("<script>", cli_out)  # Plain text CLI displays string without interpreting

        html_out = render_html_report(report)
        # In HTML, raw tags must be escaped as &lt;script&gt;
        self.assertNotIn("<script>alert('xss')</script>", html_out)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_out)
        self.assertNotIn("<img src=x onerror=alert(1)>", html_out)


if __name__ == "__main__":
    unittest.main()
