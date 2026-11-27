"""Tests for Agent 4 deliverables: schema registry, run manifests, verifier, and forensic HTML reporting."""

import json
import os
import shutil
import tempfile
import unittest

from pulsewatch.cli import main, run_pipeline
from pulsewatch.html_report import render_html_report
from pulsewatch.manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_run_manifest,
    canonical_json_bytes,
    canonical_sha256,
    write_manifest_file,
)
from pulsewatch.schema_registry import (
    CURRENT_REPORT_REVISION,
    EvidenceKind,
    generate_markdown_schema_doc,
    validate_report_schema,
)
from pulsewatch.verify import CheckStatus, verify_analysis


class TestSchemaRegistry(unittest.TestCase):
    def test_markdown_doc_generation(self):
        doc = generate_markdown_schema_doc()
        self.assertIn("# PulseWatch Canonical Report Schema Specification", doc)
        self.assertIn("pulsewatch.report/1", doc)
        self.assertIn("Evidence Classification Tiers", doc)
        self.assertIn("input.source_label", doc)

    def test_schema_validation_passes_valid_report(self):
        valid = {
            "schema_version": "pulsewatch.report/1",
            "report_revision": 2,
            "pulsewatch_version": "0.2.0",
            "input": {
                "source_label": "sample.jsonl",
                "source_sha256": "abc",
                "total_lines_read": 10,
                "valid_records": 10,
                "retained_records": 10,
                "duplicates_removed": 0,
                "exactness_status": "exact",
            },
            "coverage": {
                "total_span_sec": 100,
                "active_seconds": 100,
                "coverage_ratio": 1.0,
                "unknown_gaps_count": 0,
            },
            "baseline_profile": {
                "fitted_rate_1s": 1.0,
                "reliability": "high",
            },
            "incidents": [],
            "analysis_hash": "deadbeef",
        }
        ok, errors = validate_report_schema(valid)
        self.assertTrue(ok, f"Validation failed with errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_schema_validation_fails_missing_fields(self):
        invalid = {
            "schema_version": "pulsewatch.report/1",
            "report_revision": 2,
        }
        ok, errors = validate_report_schema(invalid)
        self.assertFalse(ok)
        self.assertTrue(any("Missing required root field" in err for err in errors))


class TestManifests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_canonical_json_bytes_and_sha256(self):
        d1 = {"b": 2, "a": 1, "c": [3, 2]}
        d2 = {"a": 1, "c": [3, 2], "b": 2}
        self.assertEqual(canonical_json_bytes(d1), canonical_json_bytes(d2))
        self.assertEqual(canonical_sha256(d1), canonical_sha256(d2))

    def test_build_run_manifest_structure(self):
        rep_path = os.path.join(self.tmpdir, "report.json")
        inp_path = os.path.join(self.tmpdir, "input.jsonl")
        with open(inp_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": 1000, "method": "GET", "route": "/api", "client": "c1", "status": 200}\n')

        rep_dict, *_ = run_pipeline(inp_path)
        with open(rep_path, "w", encoding="utf-8") as f:
            json.dump(rep_dict, f, indent=2)

        manifest = build_run_manifest(
            report_dict=rep_dict,
            report_file_path=rep_path,
            input_file_path=inp_path,
            run_id="test-run-123",
        )
        self.assertEqual(manifest["manifest_schema"], MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["run_id"], "test-run-123")
        self.assertIn("manifest_sha256", manifest)
        self.assertEqual(manifest["report"]["analysis_hash"], rep_dict["analysis_hash"])
        self.assertEqual(manifest["input"]["lines_read"], 1)

        # Write and check reading back
        m_path = os.path.join(self.tmpdir, "report.manifest.json")
        write_manifest_file(manifest, m_path)
        self.assertTrue(os.path.exists(m_path))


class TestVerifierRules(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.input_path = os.path.join(self.tmpdir, "traffic.jsonl")
        with open(self.input_path, "w", encoding="utf-8") as f:
            for t in range(1000, 1060):
                f.write(json.dumps({
                    "timestamp": t,
                    "method": "GET",
                    "route": "/test",
                    "client": "127.0.0.1",
                    "status": 200,
                    "seq": t,
                    "source": "s1",
                }) + "\n")
        self.report_path = os.path.join(self.tmpdir, "report.json")
        self.manifest_path = os.path.join(self.tmpdir, "report.manifest.json")

        # Run pipeline and save report + manifest
        rep, *_ = run_pipeline(self.input_path)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)

        m = build_run_manifest(rep, self.report_path, self.input_path)
        write_manifest_file(m, self.manifest_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_verify_all_pass(self):
        summary = verify_analysis(
            report_path=self.report_path,
            manifest_path=self.manifest_path,
            input_path=self.input_path,
            reproduce=True,
        )
        self.assertEqual(summary.exit_code, 0)
        self.assertEqual(summary.overall_status, "VERIFIED")

        statuses = {r.rule_id: r.status for r in summary.results}
        self.assertEqual(statuses.get("R1"), CheckStatus.PASS)
        self.assertEqual(statuses.get("R2"), CheckStatus.PASS)
        self.assertEqual(statuses.get("R3"), CheckStatus.PASS)
        self.assertEqual(statuses.get("R4"), CheckStatus.PASS)
        self.assertEqual(statuses.get("R5"), CheckStatus.PASS)
        self.assertEqual(statuses.get("M1"), CheckStatus.PASS)
        self.assertEqual(statuses.get("M2"), CheckStatus.PASS)
        self.assertEqual(statuses.get("M3"), CheckStatus.PASS)
        self.assertEqual(statuses.get("M4"), CheckStatus.PASS)
        self.assertEqual(statuses.get("I1"), CheckStatus.PASS)
        self.assertEqual(statuses.get("P1"), CheckStatus.PASS)

    def test_adversarial_tamper_report_hash_fails_r4(self):
        with open(self.report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["input"]["valid_records"] += 999  # Tamper without updating analysis_hash
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        summary = verify_analysis(report_path=self.report_path)
        self.assertEqual(summary.exit_code, 1)
        r4 = next(r for r in summary.results if r.rule_id == "R4")
        self.assertEqual(r4.status, CheckStatus.FAIL)
        self.assertIn("Hash mismatch", r4.message)

    def test_adversarial_tamper_manifest_fails_m2(self):
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            m_data = json.load(f)
        m_data["run_id"] = "tampered-run-id"  # Tamper without updating manifest_sha256
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(m_data, f, indent=2)

        summary = verify_analysis(report_path=self.report_path, manifest_path=self.manifest_path)
        self.assertEqual(summary.exit_code, 1)
        m2 = next(r for r in summary.results if r.rule_id == "M2")
        self.assertEqual(m2.status, CheckStatus.FAIL)

    def test_adversarial_tamper_input_fails_i1_and_p1(self):
        # Tamper input file
        with open(self.input_path, "a", encoding="utf-8") as f:
            f.write('{"timestamp": 2000, "status": 500}\n')

        summary = verify_analysis(
            report_path=self.report_path,
            input_path=self.input_path,
            reproduce=True,
        )
        self.assertEqual(summary.exit_code, 1)
        i1 = next(r for r in summary.results if r.rule_id == "I1")
        p1 = next(r for r in summary.results if r.rule_id == "P1")
        self.assertEqual(i1.status, CheckStatus.FAIL)
        self.assertEqual(p1.status, CheckStatus.FAIL)

    def test_missing_report_file_returns_code_2(self):
        summary = verify_analysis(report_path=os.path.join(self.tmpdir, "nonexistent.json"))
        self.assertEqual(summary.exit_code, 2)
        self.assertEqual(summary.overall_status, "FAILED")

    def test_cli_verify_subcommand(self):
        ret = main(["verify", self.report_path, "--input", self.input_path, "--reproduce"])
        self.assertEqual(ret, 0)

    def test_negative_path_traversal_in_manifest_fails_m3(self):
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            m_data = json.load(f)
        m_data["report"]["file_name"] = "../../etc/shadow"
        # Recompute self digest so M2 passes but M3 catches path traversal
        m_data["manifest_sha256"] = canonical_sha256(m_data, exclude_keys={"manifest_sha256"})
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(m_data, f, indent=2)

        summary = verify_analysis(self.report_path, manifest_path=self.manifest_path)
        self.assertEqual(summary.exit_code, 1)
        m3 = next(r for r in summary.results if r.rule_id == "M3")
        self.assertEqual(m3.status, CheckStatus.FAIL)
        self.assertIn("file_name", m3.message)

    def test_negative_symlink_escape_broken_link(self):
        link_path = os.path.join(self.tmpdir, "broken_link.json")
        os.symlink(os.path.join(self.tmpdir, "missing_target.json"), link_path)
        summary = verify_analysis(report_path=link_path)
        self.assertEqual(summary.exit_code, 2)
        self.assertEqual(summary.overall_status, "FAILED")

    def test_negative_incorrect_config_fingerprint_fails_r5(self):
        with open(self.report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["detector_config_digest"] = "f" * 64
        data["analysis_hash"] = canonical_sha256(data, exclude_keys={"analysis_hash"})
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        summary = verify_analysis(self.report_path)
        self.assertEqual(summary.exit_code, 1)
        r5 = next(r for r in summary.results if r.rule_id == "R5")
        self.assertEqual(r5.status, CheckStatus.FAIL)

    def test_negative_invalid_revision_fails_r2(self):
        with open(self.report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["report_revision"] = 999
        data["analysis_hash"] = canonical_sha256(data, exclude_keys={"analysis_hash"})
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        summary = verify_analysis(self.report_path)
        self.assertEqual(summary.exit_code, 1)
        r2 = next(r for r in summary.results if r.rule_id == "R2")
        self.assertEqual(r2.status, CheckStatus.FAIL)

    def test_negative_malformed_manifest_fails_m1(self):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write("{corrupted_json_syntax: 123")
        summary = verify_analysis(self.report_path, manifest_path=self.manifest_path)
        self.assertEqual(summary.exit_code, 1)
        m1 = next(r for r in summary.results if r.rule_id == "M1")
        self.assertEqual(m1.status, CheckStatus.FAIL)

    def test_negative_interrupted_artifact_generation_fails_r1(self):
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write('{"schema_version": "pulsewatch.report/1", "input": {"total_lines"')
        summary = verify_analysis(self.report_path)
        self.assertEqual(summary.exit_code, 1)
        r1 = next(r for r in summary.results if r.rule_id == "R1")
        self.assertEqual(r1.status, CheckStatus.FAIL)


class TestHTMLForensicReport(unittest.TestCase):
    def test_render_html_report_sections(self):
        rep = {
            "schema_version": "pulsewatch.report/1",
            "report_revision": 2,
            "pulsewatch_version": "0.2.0",
            "analysis_hash": "a1b2c3d4",
            "input": {
                "source_label": "sample.jsonl",
                "source_sha256": "abcdef123456",
                "total_lines_read": 100,
                "valid_records": 100,
                "retained_records": 90,
                "duplicates_removed": 10,
                "exactness_status": "exact",
            },
            "coverage": {
                "total_span_sec": 300,
                "active_seconds": 290,
                "coverage_ratio": 0.967,
                "unknown_gaps_count": 0,
            },
            "baseline_profile": {
                "fitted_rate_1s": 0.3,
                "reliability": "high",
                "rate_stability": {"label": "stable", "statistic": 1.2, "phi": 1.05},
            },
            "incidents": [
                {
                    "incident_id": "INC-001",
                    "observed_facts": {
                        "start_sec": 100,
                        "end_sec": 160,
                        "duration_sec": 60,
                        "peak_1s_rate": 15,
                        "total_observed_requests": 200,
                        "estimated_excess_requests": 180,
                        "start_iso": "1970-01-01T00:01:40+00:00",
                        "end_iso": "1970-01-01T00:02:40+00:00",
                    },
                    "statistical_findings": {
                        "max_z_score": 8.5,
                        "scales_fired": [60],
                        "observed_over_expected": 11.1,
                        "min_p_value": 1e-12,
                        "all_tails_exact": True,
                        "max_omitted_mass_bound": 1e-15,
                        "detection_windows": [],
                    },
                    "descriptive_pattern": {"shape": "sudden_spike", "rationale": "Sudden rate elevation"},
                    "contributor_evidence": {
                        "top_routes": {"/login": 150},
                        "top_clients": {"10.0.0.1": 150},
                        "concentration_tags": ["single_endpoint_concentration"],
                    },
                    "investigation_leads": [
                        {"trigger": "Route /login dominated", "suggested_action": "Check auth service"}
                    ],
                }
            ],
            "primary_60s_windows": [
                {
                    "start_sec": 60,
                    "end_sec": 120,
                    "start_iso": "1970-01-01T00:01:00+00:00",
                    "total_requests": 50,
                    "active_seconds_count": 50,
                    "status_2xx": 50,
                    "status_4xx": 0,
                    "status_5xx": 0,
                    "is_complete": True,
                }
            ],
            "data_notices": ["Notice A"],
            "known_limitations": ["Limitation B"],
        }
        html_text = render_html_report(rep)
        self.assertIn("PulseWatch Incident &amp; Traffic Report", html_text)
        self.assertIn("INC-001", html_text)
        self.assertIn("Executive Status Panel", html_text)
        self.assertIn("Identity &amp; Deduplication Accounting", html_text)
        self.assertIn("Statistical Baseline &amp; Rate Stability", html_text)
        self.assertIn("Cryptographic Integrity &amp; Reproduction", html_text)
        self.assertIn("Appendix B: Primary 60-Second Windows", html_text)
        self.assertIn("pulsewatch verify", html_text)


if __name__ == "__main__":
    unittest.main()
