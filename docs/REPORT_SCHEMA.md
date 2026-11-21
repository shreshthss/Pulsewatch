# Canonical Report Contract: pulsewatch.report/1

All PulseWatch outputs adhere to the canonical schema `pulsewatch.report/1`.

## Structure
1. `schema_version`: String `"pulsewatch.report/1"`.
2. `pulsewatch_version`: Semantic version string.
3. `input_metadata`:
   - `source_file`: Path of ingested file.
   - `source_file_sha256`: Content digest of source file.
   - `earliest_timestamp`, `latest_timestamp`, `total_span_seconds`.
4. `ingestion_accounting`:
   - `total_lines_read`, `valid_records`, `control_records`, `empty_lines`, `oversized_lines`, `malformed_json_lines`, `schema_violations`, `out_of_order_records`.
5. `deduplication_accounting`:
   - `exactness_status`: `"fully_verified_exact"`, `"partial_identity_coverage"`, `"degraded_due_to_capacity_eviction"`, or `"conflicts_detected"`.
   - `duplicates_removed`, `identity_ineligible`, `identity_conflicts`, `unverifiable_due_to_eviction`, `cap_degraded`.
6. `coverage_and_gaps`:
   - `coverage_ratio`, `known_zero_seconds`, `assumed_zero_seconds`, `unknown_gap_seconds`, `unknown_gaps`.
7. `primary_60s_windows`: UTC-aligned 60-second window records with counts, status breakdown, and completeness flag.
8. `baseline_status`:
   - `fitted_rate_1s`, `reliability` (`HIGH`, `MEDIUM`, `LOW`, `ABSTAIN`), `fraction_time_masked`, `dispersion_by_scale`.
9. `detector_summary`: Evaluated scales, candidate count, fired scales, calibration parameters.
10. `incidents`: Merged incident records containing:
   - `incident_id`: e.g. `"INC-001"`.
   - `observed_facts`: Boundaries, duration, peak rate, excess counts, status breakdown.
   - `statistical_findings`: Fired scales, max z-score, min log p-value.
   - `descriptive_pattern`: Geometric shape (`isolated_spike`, `micro_burst`, `sustained_surge`, `ramp`, `level_shift_candidate`, `unclassified`) and rationale.
   - `contributor_evidence`: Second-pass client and route concentration tags (`single_client_dominated`, `broad_based`, `route_concentrated`, `status_shifted`).
   - `unknown_causes`: Explicit disclaimer regarding limits of offline inference.
11. `data_notices` & `known_limitations`.
12. `analysis_hash`: Deterministic SHA-256 computed over sorted canonical JSON keys.
