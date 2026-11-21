"""Canonical report generator and deterministic hash computation (pulsewatch.report/1)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional

from pulsewatch import __schema_version__, __version__
from pulsewatch.dedup import DedupAccounting
from pulsewatch.detector import BaselineProfile, DetectionCandidate
from pulsewatch.incident import Incident
from pulsewatch.ingest import IngestAccounting
from pulsewatch.temporal import CoverageReport, WindowSummary60s


def sanitize_for_json(obj: Any) -> Any:
    """Recursively sanitize data ensuring no NaN or Infinite floats exist in JSON output."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj]
    return obj


def compute_deterministic_hash(data: Dict[str, Any]) -> str:
    """Compute SHA-256 hash over canonical JSON representation of report content."""
    payload = {k: v for k, v in data.items() if k != "analysis_hash"}
    canonical_bytes = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def generate_canonical_report(
    input_file: str,
    ingest_acc: IngestAccounting,
    dedup_acc: DedupAccounting,
    coverage: CoverageReport,
    windows_60s: List[WindowSummary60s],
    baseline: BaselineProfile,
    candidates: List[DetectionCandidate],
    incidents: List[Incident],
    data_notices: Optional[List[str]] = None,
    known_limitations: Optional[List[str]] = None,
    input_sha256: Optional[str] = None
) -> Dict[str, Any]:
    """Assemble canonical pulsewatch.report/1 dictionary."""
    notices = list(data_notices or [])
    limitations = list(known_limitations or [
        "Offline synthetic monitoring cannot infer external causes, intent, or system failures.",
        "Deduplication is bounded in memory; high volumes may trigger LRU eviction.",
        "Windows overlapping unknown gaps are treated as unobserved rather than assumed zero.",
        "Tail probability calculations are empirical approximations under Poisson/Negative-Binomial dispersion models."
    ])

    if dedup_acc.cap_degraded:
        notices.append("Deduplication memory bound exceeded; some duplicate records may have escaped eviction.")
    if dedup_acc.identity_ineligible > 0:
        notices.append(f"{dedup_acc.identity_ineligible} records lacked usable identity fields and were retained unverified.")
    if dedup_acc.identity_conflicts > 0:
        notices.append(f"{dedup_acc.identity_conflicts} identity collision records with conflicting attributes were retained.")
    if ingest_acc.malformed_json_lines > 0 or ingest_acc.schema_violations > 0:
        notices.append(f"Ingestion encountered {ingest_acc.malformed_json_lines} malformed lines and {ingest_acc.schema_violations} schema violations.")
    if ingest_acc.out_of_order_records > 0:
        notices.append(f"{ingest_acc.out_of_order_records} records arrived with out-of-order timestamps.")
    if baseline.warning:
        notices.append(f"Baseline notice: {baseline.warning}")
    if baseline.reliability == "abstain":
        notices.append(f"Detector abstained from decision: {baseline.abstain_reason}")

    all_fired_scales = sorted(list(set(c.scale_sec for c in candidates)))

    report: Dict[str, Any] = {
        "schema_version": __schema_version__,
        "pulsewatch_version": __version__,
        "input_metadata": {
            "source_file": input_file,
            "source_file_sha256": input_sha256,
            "earliest_timestamp": coverage.earliest_sec,
            "latest_timestamp": coverage.latest_sec,
            "total_span_seconds": coverage.total_span_sec,
        },
        "ingestion_accounting": ingest_acc.to_dict(),
        "deduplication_accounting": dedup_acc.to_dict(),
        "coverage_and_gaps": coverage.to_dict(),
        "primary_60s_windows": [w.to_dict() for w in windows_60s],
        "baseline_status": baseline.to_dict(),
        "detector_summary": {
            "detector": "PW-Scan",
            "scales_evaluated": [10, 60, 300],
            "candidate_detections_count": len(candidates),
            "fired_scales": all_fired_scales,
            "dispersion_modeling": "poisson_negative_binomial",
            "abstention_state": baseline.reliability == "abstain",
            "abstention_reason": baseline.abstain_reason,
        },
        "incidents": [inc.to_dict() for inc in incidents],
        "data_notices": notices,
        "known_limitations": limitations,
    }

    sanitized = sanitize_for_json(report)
    sanitized["analysis_hash"] = compute_deterministic_hash(sanitized)
    return sanitized


def format_cli_summary(report: Dict[str, Any]) -> str:
    """Format human-readable CLI summary table escaping any untrusted characters."""
    lines: List[str] = []
    lines.append("=" * 76)
    lines.append(f"PULSEWATCH ANALYSIS SUMMARY — Schema {report.get('schema_version')}")
    lines.append("=" * 76)

    in_meta = report.get("input_metadata", {})
    lines.append(f"Source file:       {in_meta.get('source_file')}")
    if in_meta.get("source_file_sha256"):
        lines.append(f"Source SHA-256:    {in_meta.get('source_file_sha256')[:16]}...")
    lines.append(f"Total time span:   {in_meta.get('total_span_seconds', 0)} seconds")

    ing_acc = report.get("ingestion_accounting", {})
    ded_acc = report.get("deduplication_accounting", {})
    lines.append(f"Ingested records:  {ing_acc.get('valid_records', 0)} valid / {ing_acc.get('total_lines_read', 0)} lines")
    lines.append(f"Deduplication:     {ded_acc.get('duplicates_removed', 0)} duplicates removed ({ded_acc.get('retained_records', 0)} retained)")
    lines.append(f"Exactness status:  {ded_acc.get('exactness_status', 'unknown')}")
    if ded_acc.get("cap_degraded"):
        lines.append("                   [WARNING] Deduplication capacity exceeded (degraded)")

    cov = report.get("coverage_and_gaps", {})
    lines.append(f"Coverage ratio:    {cov.get('coverage_ratio', 0.0) * 100:.1f}% ({cov.get('unknown_gaps_count', 0)} unknown gaps, {cov.get('known_zero_seconds', 0)}s known zeros)")

    base = report.get("baseline_status", {})
    rate_val = base.get("fitted_rate_1s", base.get("median_rate_1s", 0))
    lines.append(f"Baseline status:   {base.get('reliability', 'unknown').upper()} (rate: {rate_val:.2f} req/s, masked time: {base.get('fraction_time_masked', 0.0)*100:.1f}%)")
    if base.get("abstain_reason"):
        lines.append(f"Abstention reason: {base.get('abstain_reason')}")

    incidents = report.get("incidents", [])
    lines.append("-" * 76)
    lines.append(f"INCIDENTS DETECTED: {len(incidents)}")
    lines.append("-" * 76)

    if not incidents:
        lines.append("No traffic anomalies flagged.")
    else:
        lines.append(f"{'ID':<8} {'SHAPE':<20} {'DURATION':<9} {'PEAK 1s':<9} {'EXCESS':<8} {'MAX Z':<8} {'TAGS'}")
        lines.append("-" * 76)
        for inc in incidents:
            inc_id = inc.get("incident_id", "")
            shape = inc.get("descriptive_pattern", {}).get("shape", "")
            obs = inc.get("observed_facts", {})
            findings = inc.get("statistical_findings", {})
            evidence = inc.get("contributor_evidence", {})
            dur = f"{obs.get('duration_sec', 0)}s"
            peak = f"{obs.get('peak_1s_rate', 0)} r/s"
            excess = str(obs.get("estimated_excess_requests", 0))
            z = f"{findings.get('max_z_score', 0):.2f}"
            tags = ",".join(evidence.get("concentration_tags", [])) if evidence else ""
            lines.append(f"{inc_id:<8} {shape:<20} {dur:<9} {peak:<9} {excess:<8} {z:<8} {tags}")

            leads = inc.get("investigation_leads", [])
            for lead in leads:
                lines.append(f"   ↳ Lead: {lead.get('suggested_action')}")

    notices = report.get("data_notices", [])
    if notices:
        lines.append("-" * 76)
        lines.append("DATA NOTICES & WARNINGS:")
        for n in notices:
            lines.append(f" • {n}")

    lines.append("=" * 76)
    lines.append(f"Deterministic Hash: {report.get('analysis_hash')}")
    lines.append("=" * 76)

    return "\n".join(lines)
