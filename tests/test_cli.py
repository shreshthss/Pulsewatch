"""End-to-end tests for PulseWatch CLI."""

import json
import os
import tempfile
import unittest
from pulsewatch.cli import main


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.input_file = os.path.join(self.temp_dir.name, "sample.jsonl")
        self.json_out = os.path.join(self.temp_dir.name, "analysis.json")
        self.html_out = os.path.join(self.temp_dir.name, "report.html")

        # Generate 120 seconds of synthetic data with normal traffic and one burst
        records = []
        for t in range(1000, 1120):
            # Normal baseline
            records.append({
                "timestamp": t,
                "method": "GET",
                "route": "/api/v1/users",
                "client": "10.0.0.1",
                "status": 200,
                "source": "proxy-1",
                "seq": t,
            })
            # Add duplicate of this record
            records.append({
                "timestamp": t,
                "method": "GET",
                "route": "/api/v1/users",
                "client": "10.0.0.1",
                "status": 200,
                "source": "proxy-1",
                "seq": t,
            })

        # Inject burst at t=1050
        for i in range(40):
            records.append({
                "timestamp": 1050,
                "method": "POST",
                "route": "/api/v1/login",
                "client": f"192.168.1.{i}",
                "status": 401,
                "source": "proxy-1",
                "seq": 20000 + i,
            })

        with open(self.input_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_summarize(self):
        exit_code = main(["summarize", self.input_file])
        self.assertEqual(exit_code, 0)

    def test_cli_analyze_and_report_workflow(self):
        # 1. Analyze and produce analysis.json
        exit_code = main(["analyze", self.input_file, "--json", self.json_out])
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(self.json_out))

        with open(self.json_out, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        self.assertEqual(report_data["schema_version"], "pulsewatch.report/1")
        self.assertEqual(report_data["deduplication_accounting"]["duplicate_records_removed"], 120)
        self.assertTrue(len(report_data["incidents"]) >= 1)

        # 2. Report and produce report.html
        exit_code_rep = main(["report", self.json_out, "--html", self.html_out])
        self.assertEqual(exit_code_rep, 0)
        self.assertTrue(os.path.exists(self.html_out))

        with open(self.html_out, "r", encoding="utf-8") as f:
            html_content = f.read()

        self.assertIn("PulseWatch Incident &amp; Traffic Report", html_content)
        self.assertIn(report_data["analysis_hash"], html_content)


if __name__ == "__main__":
    unittest.main()
