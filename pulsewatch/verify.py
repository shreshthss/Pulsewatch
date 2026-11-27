"""PulseWatch Verification Engine: independent verification of reports, manifests, and reproduction.

Implements verification rules:
- R1..R5: Report structural and cryptographic integrity
- M1..M4: Sidecar manifest integrity and cross-artifact consistency
- I1: Input file byte integrity against recorded digest
- P1: Deterministic pipeline reproduction and hash parity
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pulsewatch.manifest import (
    MANIFEST_SCHEMA_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    compute_file_sha256,
)
from pulsewatch.schema_registry import (
    CURRENT_REPORT_REVISION,
    MAX_SUPPORTED_REVISION,
    MIN_SUPPORTED_REVISION,
    validate_report_schema,
)


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    SKIP = "SKIP"
    FAIL = "FAIL"


@dataclass
class RuleResult:
    rule_id: str
    name: str
    status: CheckStatus
    message: str


@dataclass
class VerificationSummary:
    report_path: str
    overall_status: str  # "VERIFIED", "WARNING", "FAILED"
    exit_code: int       # 0 = verified/warn, 1 = failed, 2 = unreadable
    results: List[RuleResult] = field(default_factory=list)

    def print_summary(self, stream=sys.stdout) -> None:
        stream.write(f"\n============================================================================\n")
        stream.write(f"PULSEWATCH VERIFICATION REPORT: {self.report_path}\n")
        stream.write(f"============================================================================\n")
        stream.write(f"ID   STATUS  RULE NAME                     DETAILS\n")
        stream.write(f"----------------------------------------------------------------------------\n")
        for r in self.results:
            stream.write(f"{r.rule_id:<4} {r.status.value:<7} {r.name:<28} {r.message}\n")
        stream.write(f"============================================================================\n")
        pass_cnt = sum(1 for r in self.results if r.status == CheckStatus.PASS)
        warn_cnt = sum(1 for r in self.results if r.status == CheckStatus.WARN)
        skip_cnt = sum(1 for r in self.results if r.status == CheckStatus.SKIP)
        fail_cnt = sum(1 for r in self.results if r.status == CheckStatus.FAIL)
        stream.write(
            f"RESULT: {self.overall_status} "
            f"({pass_cnt} pass, {warn_cnt} warn, {skip_cnt} skipped, {fail_cnt} fail)\n"
        )
        stream.write(f"============================================================================\n\n")


def verify_analysis(
    report_path: str,
    manifest_path: Optional[str] = None,
    input_path: Optional[str] = None,
    reproduce: bool = False,
) -> VerificationSummary:
    """Verify report, manifest, and optional reproduction according to rules R1..R5, M1..M4, I1, P1."""
    results: List[RuleResult] = []

    # Check report file existence
    if not os.path.exists(report_path):
        results.append(RuleResult("R1", "Report file exists", CheckStatus.FAIL, f"Report file not found: {report_path}"))
        return VerificationSummary(report_path, "FAILED", exit_code=2, results=results)

    # R1: Report parses
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)
        if not isinstance(report_data, dict):
            raise ValueError("Root JSON element is not an object")
        results.append(RuleResult("R1", "Report parses", CheckStatus.PASS, "Valid JSON object"))
    except Exception as e:
        results.append(RuleResult("R1", "Report parses", CheckStatus.FAIL, f"Parse error: {e}"))
        return VerificationSummary(report_path, "FAILED", exit_code=1, results=results)

    # R2: Schema and revision supported
    schema_ver = report_data.get("schema_version")
    rev = report_data.get("report_revision", 1)
    if schema_ver != "pulsewatch.report/1":
        results.append(RuleResult("R2", "Schema supported", CheckStatus.FAIL, f"Unsupported schema '{schema_ver}'"))
    elif not isinstance(rev, int) or rev < MIN_SUPPORTED_REVISION or rev > MAX_SUPPORTED_REVISION:
        results.append(RuleResult("R2", "Schema supported", CheckStatus.FAIL, f"Unsupported revision {rev}"))
    else:
        results.append(RuleResult("R2", "Schema supported", CheckStatus.PASS, f"Schema {schema_ver} (rev {rev})"))

    # R3: Required metadata present
    is_valid_schema, schema_errors = validate_report_schema(report_data)
    if not is_valid_schema:
        results.append(RuleResult("R3", "Metadata completeness", CheckStatus.FAIL, "; ".join(schema_errors)))
    else:
        results.append(RuleResult("R3", "Metadata completeness", CheckStatus.PASS, "All required schema fields present"))

    # R4: analysis_hash matches content
    recorded_hash = report_data.get("analysis_hash")
    if not recorded_hash:
        results.append(RuleResult("R4", "Analysis hash integrity", CheckStatus.FAIL, "Missing analysis_hash field"))
    else:
        # Canonical hash of report excluding analysis_hash
        computed_hash = canonical_sha256(report_data, exclude_keys={"analysis_hash"})
        if computed_hash == recorded_hash:
            results.append(RuleResult("R4", "Analysis hash integrity", CheckStatus.PASS, f"Hash verified: {computed_hash[:16]}..."))
        else:
            results.append(RuleResult("R4", "Analysis hash integrity", CheckStatus.FAIL, f"Hash mismatch: recorded {recorded_hash[:16]}... != computed {computed_hash[:16]}..."))

    # R5: Detector config matches digest
    cfg = report_data.get("analysis_config")
    cfg_digest = report_data.get("detector_config_digest")
    if cfg and cfg_digest:
        from pulsewatch.detector import compute_detector_config_fingerprint
        calc_digest = compute_detector_config_fingerprint(
            screening_z_threshold=cfg.get("screening_z_threshold", 4.0),
            min_elevation_ratio=cfg.get("min_elevation_ratio", 1.3),
            min_excess_counts=cfg.get("min_excess_counts"),
            scales=cfg.get("scales"),
        )
        if calc_digest == cfg_digest:
            results.append(RuleResult("R5", "Detector config digest", CheckStatus.PASS, "Config matches digest"))
        else:
            results.append(RuleResult("R5", "Detector config digest", CheckStatus.FAIL, f"Digest mismatch: {cfg_digest[:16]}... != {calc_digest[:16]}..."))
    elif rev >= 2:
        results.append(RuleResult("R5", "Detector config digest", CheckStatus.WARN, "No detector_config_digest recorded"))
    else:
        results.append(RuleResult("R5", "Detector config digest", CheckStatus.SKIP, "Revision 1 report; config digest optional"))

    # M1..M4: Sidecar Manifest checks
    resolved_manifest_path = manifest_path
    if not resolved_manifest_path:
        # Check default sidecar: <name>.manifest.json
        candidate_manifest = os.path.splitext(report_path)[0] + ".manifest.json"
        if os.path.exists(candidate_manifest):
            resolved_manifest_path = candidate_manifest

    manifest_data = None
    if resolved_manifest_path and os.path.exists(resolved_manifest_path):
        try:
            with open(resolved_manifest_path, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            results.append(RuleResult("M1", "Manifest presence", CheckStatus.PASS, f"Found {os.path.basename(resolved_manifest_path)}"))
        except Exception as e:
            results.append(RuleResult("M1", "Manifest presence", CheckStatus.FAIL, f"Unreadable manifest: {e}"))
    else:
        results.append(RuleResult("M1", "Manifest presence", CheckStatus.SKIP, "No sidecar manifest provided or found"))

    if manifest_data:
        # M2: Manifest self-digest
        m_rec_digest = manifest_data.get("manifest_sha256")
        if m_rec_digest:
            m_calc_digest = canonical_sha256(manifest_data, exclude_keys={"manifest_sha256"})
            if m_calc_digest == m_rec_digest:
                results.append(RuleResult("M2", "Manifest self-digest", CheckStatus.PASS, "Manifest self-digest verified"))
            else:
                results.append(RuleResult("M2", "Manifest self-digest", CheckStatus.FAIL, f"Self-digest mismatch: {m_rec_digest[:16]}... != {m_calc_digest[:16]}..."))
        else:
            results.append(RuleResult("M2", "Manifest self-digest", CheckStatus.FAIL, "Missing manifest_sha256 in manifest"))

        # M3: Manifest agrees with report
        m_rep = manifest_data.get("report", {})
        m_inp = manifest_data.get("input", {})
        m_ana = manifest_data.get("analysis", {})

        agrees = True
        discrepancies = []

        m_filename = m_rep.get("file_name")
        if m_filename:
            if "/" in m_filename or "\\" in m_filename or ".." in m_filename or m_filename != os.path.basename(report_path):
                agrees = False
                discrepancies.append("file_name")

        if m_rep.get("analysis_hash") != report_data.get("analysis_hash"):
            agrees = False
            discrepancies.append("analysis_hash")
        if m_inp.get("file_sha256") != report_data.get("input", {}).get("source_sha256"):
            agrees = False
            discrepancies.append("source_sha256")
        if m_ana.get("incident_count") != len(report_data.get("incidents", [])):
            agrees = False
            discrepancies.append("incident_count")
        if m_inp.get("lines_read") != report_data.get("input", {}).get("total_lines_read"):
            agrees = False
            discrepancies.append("lines_read")

        if agrees:
            results.append(RuleResult("M3", "Manifest-report agreement", CheckStatus.PASS, "Manifest agrees with report on all fields"))
        else:
            results.append(RuleResult("M3", "Manifest-report agreement", CheckStatus.FAIL, f"Discrepancies in: {', '.join(discrepancies)}"))

        # M4: Report file bytes match manifest
        actual_report_sha, _ = compute_file_sha256(report_path)
        expected_report_sha = m_rep.get("file_sha256")
        if actual_report_sha == expected_report_sha:
            results.append(RuleResult("M4", "Report byte integrity", CheckStatus.PASS, "Report file bytes match manifest"))
        else:
            results.append(RuleResult("M4", "Report byte integrity", CheckStatus.FAIL, f"Report file SHA-256 mismatch: {actual_report_sha[:16]}... != {expected_report_sha[:16]}..."))
    else:
        results.append(RuleResult("M2", "Manifest self-digest", CheckStatus.SKIP, "Skipped (no manifest)"))
        results.append(RuleResult("M3", "Manifest-report agreement", CheckStatus.SKIP, "Skipped (no manifest)"))
        results.append(RuleResult("M4", "Report byte integrity", CheckStatus.SKIP, "Skipped (no manifest)"))

    # I1: Input file byte integrity
    if input_path:
        if not os.path.exists(input_path):
            results.append(RuleResult("I1", "Input file integrity", CheckStatus.FAIL, f"Input file not found: {input_path}"))
        else:
            actual_inp_sha, _ = compute_file_sha256(input_path)
            expected_inp_sha = report_data.get("input", {}).get("source_sha256")
            if actual_inp_sha == expected_inp_sha:
                results.append(RuleResult("I1", "Input file integrity", CheckStatus.PASS, f"Input bytes match source_sha256: {actual_inp_sha[:16]}..."))
            else:
                results.append(RuleResult("I1", "Input file integrity", CheckStatus.FAIL, f"Input SHA-256 mismatch: {actual_inp_sha[:16]}... != {expected_inp_sha[:16]}..."))
    else:
        results.append(RuleResult("I1", "Input file integrity", CheckStatus.SKIP, "Skipped (no --input specified)"))

    # P1: Deterministic pipeline reproduction
    if reproduce:
        if not input_path or not os.path.exists(input_path):
            results.append(RuleResult("P1", "Deterministic reproduction", CheckStatus.SKIP, "Skipped (reproduction requires accessible --input file)"))
        else:
            try:
                from pulsewatch.cli import run_pipeline
                re_report, _, _, _, _ = run_pipeline(input_path)
                re_hash = re_report.get("analysis_hash")
                orig_hash = report_data.get("analysis_hash")
                if re_hash == orig_hash:
                    results.append(RuleResult("P1", "Deterministic reproduction", CheckStatus.PASS, f"Reproduced byte-identical analysis_hash: {re_hash[:16]}..."))
                else:
                    results.append(RuleResult("P1", "Deterministic reproduction", CheckStatus.FAIL, f"Reproduced hash differed: {re_hash[:16]}... != {orig_hash[:16]}..."))
            except Exception as e:
                results.append(RuleResult("P1", "Deterministic reproduction", CheckStatus.FAIL, f"Reproduction run failed with exception: {e}"))
    else:
        results.append(RuleResult("P1", "Deterministic reproduction", CheckStatus.SKIP, "Skipped (pass --reproduce to execute)"))

    # Evaluate overall summary
    any_fail = any(r.status == CheckStatus.FAIL for r in results)
    any_warn = any(r.status == CheckStatus.WARN for r in results)

    if any_fail:
        overall = "FAILED"
        exit_code = 1
    elif any_warn:
        overall = "VERIFIED_WITH_WARNINGS"
        exit_code = 0
    else:
        overall = "VERIFIED"
        exit_code = 0

    return VerificationSummary(report_path, overall, exit_code, results)
