# PulseWatch Canonical Report Schema Specification

Schema Version: `pulsewatch.report/1`
Current Revision: 2

## Evidence Classification Tiers
- **observed**: Directly counted from retained records or telemetry.
- **derived**: Computed by arithmetic or aggregation over observed and fitted values.
- **statistical**: Model output; conditional on statistical assumptions (Poisson / Negative-Binomial).
- **context**: Configuration and environment metadata.
- **hypothesis**: Automated triage suggestion; not an empirical finding.
- **unknown**: Out-of-bounds or untracked phenomena.

## Registered Fields
| JSON Path | Evidence Class | Unit | Revision | Optional | Description |
|---|---|---|:---:|:---:|---|
| `schema_version` | `context` | `string` | 1 | No | Schema identifier: pulsewatch.report/1 |
| `report_revision` | `context` | `integer` | 2 | Yes | Report format revision (2 for extended evidence) |
| `pulsewatch_version` | `context` | `semver` | 1 | No | PulseWatch implementation version |
| `analysis_hash` | `context` | `sha256` | 1 | No | Cryptographic digest of canonical report contents |
| `generated_at_utc` | `context` | `iso8601` | 1 | Yes | UTC generation timestamp (omitted in deterministic runs) |
| `input.source_label` | `context` | `string` | 1 | No | Original file path or stream label |
| `input.source_sha256` | `observed` | `sha256` | 1 | No | SHA-256 digest of analyzed input file bytes |
| `input.file_size_bytes` | `observed` | `bytes` | 2 | Yes | Exact byte size of input source |
| `input.total_lines_read` | `observed` | `count` | 1 | No | Raw JSONL lines ingested |
| `input.valid_records` | `observed` | `count` | 1 | No | Syntactically valid JSON records |
| `input.duplicates_removed` | `derived` | `count` | 1 | No | Duplicate requests filtered by identity key |
| `input.retained_records` | `observed` | `count` | 1 | No | Deduplicated records forwarded to temporal engine |
| `input.exactness_status` | `statistical` | `enum` | 1 | No | Deduplication exactness status |
| `coverage.earliest_sec` | `observed` | `epoch_sec` | 1 | No | Earliest observed event timestamp |
| `coverage.latest_sec` | `observed` | `epoch_sec` | 1 | No | Latest observed event timestamp |
| `coverage.total_span_sec` | `derived` | `seconds` | 1 | No | Total observation span (latest - earliest + 1) |
| `coverage.active_seconds` | `observed` | `seconds` | 1 | No | Count of 1-second buckets containing >= 1 event |
| `coverage.coverage_ratio` | `derived` | `ratio` | 1 | No | Observed and known-zero seconds / total span |
| `coverage.unknown_gaps_count` | `observed` | `count` | 1 | No | Outages exceeding gap threshold (900s) |
| `baseline_profile.fitted_rate_1s` | `statistical` | `req/s` | 1 | No | Contamination-masked Poisson arrival rate |
| `baseline_profile.reliability` | `statistical` | `enum` | 1 | No | Reliability tier: high, medium, low, abstain |
| `baseline_profile.fraction_time_masked` | `derived` | `ratio` | 1 | No | Proportion of observation time masked |
| `baseline_profile.rate_stability` | `statistical` | `object` | 2 | Yes | Chi-squared score test for rate homogeneity |
| `incidents` | `observed` | `list` | 1 | No | Detected and merged incident clusters |
| `incidents[].incident_id` | `context` | `string` | 1 | No | Unique incident identifier within run |
| `incidents[].observed_facts.start_sec` | `observed` | `epoch_sec` | 1 | No | Incident interval start (inclusive) |
| `incidents[].observed_facts.end_sec` | `observed` | `epoch_sec` | 1 | No | Incident interval end (exclusive) |
| `incidents[].observed_facts.duration_sec` | `derived` | `seconds` | 1 | No | Duration of candidate interval |
| `incidents[].observed_facts.peak_1s_rate` | `observed` | `req/s` | 1 | No | Maximum 1-second request count |
| `incidents[].observed_facts.total_observed_requests` | `observed` | `count` | 1 | No | Total requests in interval |
| `incidents[].observed_facts.estimated_excess_requests` | `derived` | `count` | 1 | No | Observed requests above baseline expectation |
| `incidents[].statistical_findings.max_z_score` | `statistical` | `z_score` | 1 | No | Maximum standardized Poisson/NB score |
| `incidents[].statistical_findings.observed_over_expected` | `derived` | `ratio` | 2 | Yes | Observed count / expected count |
| `incidents[].statistical_findings.min_p_value` | `statistical` | `probability` | 2 | Yes | Minimum upper-tail p-value |
| `incidents[].statistical_findings.all_tails_exact` | `statistical` | `boolean` | 2 | Yes | True if tail bounds were exact |
| `incidents[].contributor_evidence` | `observed` | `object` | 1 | Yes | Breakdown of top routes, clients, and status codes |
| `incidents[].investigation_leads` | `hypothesis` | `list` | 1 | No | Actionable SRE investigation leads |
| `analysis_config` | `context` | `object` | 2 | Yes | Detector parameters and threshold settings |
| `calibration_status` | `context` | `object` | 2 | Yes | Empirical false-incident calibration metadata |
| `second_pass_status` | `context` | `string` | 2 | Yes | Verification status of contributor evidence replay |
