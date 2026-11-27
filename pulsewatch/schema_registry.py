"""PulseWatch Schema Registry: definitive contract for report revisions, field definitions, and evidence types.

Defines the fields, evidence classes, units, optionality, and revisions for:
- pulsewatch.report/1 (Revision 1 and Revision 2)
- pulsewatch.manifest/1
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

CURRENT_REPORT_REVISION = 2
MIN_SUPPORTED_REVISION = 1
MAX_SUPPORTED_REVISION = 2


class EvidenceKind(str, enum.Enum):
    OBSERVED = "observed"        # Counted from retained records or declared telemetry
    DERIVED = "derived"          # Arithmetic on observed or fitted values
    STATISTICAL = "statistical"  # Output of the PW-Scan model; depends on its assumptions
    CONTEXT = "context"          # Run or configuration metadata
    HYPOTHESIS = "hypothesis"    # Suggested place to look; not an empirical finding
    UNKNOWN = "unknown"          # What the analysis cannot establish


@dataclass(frozen=True)
class FieldDefinition:
    path: str
    kind: EvidenceKind
    unit: str
    optional: bool
    since_revision: int
    description: str


# Canonical field catalog for pulsewatch.report/1
SCHEMA_CATALOG: List[FieldDefinition] = [
    # Top-level Metadata
    FieldDefinition("schema_version", EvidenceKind.CONTEXT, "string", False, 1, "Schema identifier: pulsewatch.report/1"),
    FieldDefinition("report_revision", EvidenceKind.CONTEXT, "integer", True, 2, "Report format revision (2 for extended evidence)"),
    FieldDefinition("pulsewatch_version", EvidenceKind.CONTEXT, "semver", False, 1, "PulseWatch implementation version"),
    FieldDefinition("analysis_hash", EvidenceKind.CONTEXT, "sha256", False, 1, "Cryptographic digest of canonical report contents"),
    FieldDefinition("generated_at_utc", EvidenceKind.CONTEXT, "iso8601", True, 1, "UTC generation timestamp (omitted in deterministic runs)"),

    # Input and Pipeline Integrity
    FieldDefinition("input.source_label", EvidenceKind.CONTEXT, "string", False, 1, "Original file path or stream label"),
    FieldDefinition("input.source_sha256", EvidenceKind.OBSERVED, "sha256", False, 1, "SHA-256 digest of analyzed input file bytes"),
    FieldDefinition("input.file_size_bytes", EvidenceKind.OBSERVED, "bytes", True, 2, "Exact byte size of input source"),
    FieldDefinition("input.total_lines_read", EvidenceKind.OBSERVED, "count", False, 1, "Raw JSONL lines ingested"),
    FieldDefinition("input.valid_records", EvidenceKind.OBSERVED, "count", False, 1, "Syntactically valid JSON records"),
    FieldDefinition("input.duplicates_removed", EvidenceKind.DERIVED, "count", False, 1, "Duplicate requests filtered by identity key"),
    FieldDefinition("input.retained_records", EvidenceKind.OBSERVED, "count", False, 1, "Deduplicated records forwarded to temporal engine"),
    FieldDefinition("input.exactness_status", EvidenceKind.STATISTICAL, "enum", False, 1, "Deduplication exactness status"),

    # Coverage Quality
    FieldDefinition("coverage.earliest_sec", EvidenceKind.OBSERVED, "epoch_sec", False, 1, "Earliest observed event timestamp"),
    FieldDefinition("coverage.latest_sec", EvidenceKind.OBSERVED, "epoch_sec", False, 1, "Latest observed event timestamp"),
    FieldDefinition("coverage.total_span_sec", EvidenceKind.DERIVED, "seconds", False, 1, "Total observation span (latest - earliest + 1)"),
    FieldDefinition("coverage.active_seconds", EvidenceKind.OBSERVED, "seconds", False, 1, "Count of 1-second buckets containing >= 1 event"),
    FieldDefinition("coverage.coverage_ratio", EvidenceKind.DERIVED, "ratio", False, 1, "Observed and known-zero seconds / total span"),
    FieldDefinition("coverage.unknown_gaps_count", EvidenceKind.OBSERVED, "count", False, 1, "Outages exceeding gap threshold (900s)"),

    # Baseline Profile
    FieldDefinition("baseline_profile.fitted_rate_1s", EvidenceKind.STATISTICAL, "req/s", False, 1, "Contamination-masked Poisson arrival rate"),
    FieldDefinition("baseline_profile.reliability", EvidenceKind.STATISTICAL, "enum", False, 1, "Reliability tier: high, medium, low, abstain"),
    FieldDefinition("baseline_profile.fraction_time_masked", EvidenceKind.DERIVED, "ratio", False, 1, "Proportion of observation time masked"),
    FieldDefinition("baseline_profile.rate_stability", EvidenceKind.STATISTICAL, "object", True, 2, "Chi-squared score test for rate homogeneity"),

    # Incidents List
    FieldDefinition("incidents", EvidenceKind.OBSERVED, "list", False, 1, "Detected and merged incident clusters"),
    FieldDefinition("incidents[].incident_id", EvidenceKind.CONTEXT, "string", False, 1, "Unique incident identifier within run"),
    FieldDefinition("incidents[].observed_facts.start_sec", EvidenceKind.OBSERVED, "epoch_sec", False, 1, "Incident interval start (inclusive)"),
    FieldDefinition("incidents[].observed_facts.end_sec", EvidenceKind.OBSERVED, "epoch_sec", False, 1, "Incident interval end (exclusive)"),
    FieldDefinition("incidents[].observed_facts.duration_sec", EvidenceKind.DERIVED, "seconds", False, 1, "Duration of candidate interval"),
    FieldDefinition("incidents[].observed_facts.peak_1s_rate", EvidenceKind.OBSERVED, "req/s", False, 1, "Maximum 1-second request count"),
    FieldDefinition("incidents[].observed_facts.total_observed_requests", EvidenceKind.OBSERVED, "count", False, 1, "Total requests in interval"),
    FieldDefinition("incidents[].observed_facts.estimated_excess_requests", EvidenceKind.DERIVED, "count", False, 1, "Observed requests above baseline expectation"),
    FieldDefinition("incidents[].statistical_findings.max_z_score", EvidenceKind.STATISTICAL, "z_score", False, 1, "Maximum standardized Poisson/NB score"),
    FieldDefinition("incidents[].statistical_findings.observed_over_expected", EvidenceKind.DERIVED, "ratio", True, 2, "Observed count / expected count"),
    FieldDefinition("incidents[].statistical_findings.min_p_value", EvidenceKind.STATISTICAL, "probability", True, 2, "Minimum upper-tail p-value"),
    FieldDefinition("incidents[].statistical_findings.all_tails_exact", EvidenceKind.STATISTICAL, "boolean", True, 2, "True if tail bounds were exact"),
    FieldDefinition("incidents[].contributor_evidence", EvidenceKind.OBSERVED, "object", True, 1, "Breakdown of top routes, clients, and status codes"),
    FieldDefinition("incidents[].investigation_leads", EvidenceKind.HYPOTHESIS, "list", False, 1, "Actionable SRE investigation leads"),

    # Analysis Configuration & Calibration
    FieldDefinition("analysis_config", EvidenceKind.CONTEXT, "object", True, 2, "Detector parameters and threshold settings"),
    FieldDefinition("calibration_status", EvidenceKind.CONTEXT, "object", True, 2, "Empirical false-incident calibration metadata"),
    FieldDefinition("second_pass_status", EvidenceKind.CONTEXT, "string", True, 2, "Verification status of contributor evidence replay"),
]


def validate_report_schema(report: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate report structure and revision rules against Schema Registry.

    Returns:
        (is_valid, list_of_errors)
    """
    errors: List[str] = []

    # 1. Top-level schema version check
    schema_ver = report.get("schema_version")
    if schema_ver != "pulsewatch.report/1":
        errors.append(f"Unsupported schema_version: '{schema_ver}'. Expected 'pulsewatch.report/1'")

    # 2. Revision check
    rev = report.get("report_revision", 1)
    if not isinstance(rev, int) or rev < MIN_SUPPORTED_REVISION or rev > MAX_SUPPORTED_REVISION:
        errors.append(f"Unsupported report_revision: {rev}. Supported revisions: {MIN_SUPPORTED_REVISION}..{MAX_SUPPORTED_REVISION}")

    # 3. Required top-level fields
    for req_field in ["pulsewatch_version", "analysis_hash", "input", "coverage", "baseline_profile", "incidents"]:
        if req_field not in report:
            errors.append(f"Missing required root field: '{req_field}'")

    # 4. Input section checks
    inp = report.get("input", {})
    if isinstance(inp, dict):
        for req_inp in ["source_label", "source_sha256", "total_lines_read", "valid_records", "retained_records"]:
            if req_inp not in inp:
                errors.append(f"Missing required input field: 'input.{req_inp}'")
    else:
        errors.append("Field 'input' must be a dictionary")

    # 5. Coverage section checks
    cov = report.get("coverage", {})
    if isinstance(cov, dict):
        for req_cov in ["total_span_sec", "active_seconds", "coverage_ratio"]:
            if req_cov not in cov:
                errors.append(f"Missing required coverage field: 'coverage.{req_cov}'")
    else:
        errors.append("Field 'coverage' must be a dictionary")

    # 6. Baseline profile checks
    base = report.get("baseline_profile", {})
    if isinstance(base, dict):
        if "fitted_rate_1s" not in base or "reliability" not in base:
            errors.append("Field 'baseline_profile' missing 'fitted_rate_1s' or 'reliability'")
    else:
        errors.append("Field 'baseline_profile' must be a dictionary")

    # 7. Incidents check
    incs = report.get("incidents", [])
    if not isinstance(incs, list):
        errors.append("Field 'incidents' must be a list")

    return (len(errors) == 0, errors)


def generate_schema_markdown_table() -> str:
    """Generate a clean markdown table of registered fields and evidence kinds."""
    lines = [
        "| JSON Path | Evidence Class | Unit | Revision | Optional | Description |",
        "|---|---|---|:---:|:---:|---|",
    ]
    for f in SCHEMA_CATALOG:
        opt_str = "Yes" if f.optional else "No"
        lines.append(f"| `{f.path}` | `{f.kind.value}` | `{f.unit}` | {f.since_revision} | {opt_str} | {f.description} |")
    return "\n".join(lines)


def generate_markdown_schema_doc() -> str:
    """Generate comprehensive documentation of schema pulsewatch.report/1 and evidence classes."""
    table = generate_schema_markdown_table()
    return f"""# PulseWatch Canonical Report Schema Specification

Schema Version: `pulsewatch.report/1`
Current Revision: {CURRENT_REPORT_REVISION}

## Evidence Classification Tiers
- **observed**: Directly counted from retained records or telemetry.
- **derived**: Computed by arithmetic or aggregation over observed and fitted values.
- **statistical**: Model output; conditional on statistical assumptions (Poisson / Negative-Binomial).
- **context**: Configuration and environment metadata.
- **hypothesis**: Automated triage suggestion; not an empirical finding.
- **unknown**: Out-of-bounds or untracked phenomena.

## Registered Fields
{table}
"""

