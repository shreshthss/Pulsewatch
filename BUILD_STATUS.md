# PulseWatch — Build Status & Traceability

**Current Stage:** Final Competition Release Freeze Complete
**Last Updated:** 2026-11-21

## Requirements Traceability Matrix

| Requirement | Adopted Architecture Spec | Implementation Module | Test Verification | Status |
|---|---|---|---|---|
| **JSONL Ingestion & Integrity** | Nanosecond internal time, 8KB line cap, 5M record limit, `_pw: coverage` control records | `pulsewatch/ingest.py` | `tests/test_ingest.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **Identity Deduplication** | 16-byte BLAKE2b digests, 4-level hierarchy, LRU eviction set, explicit degradation & exactness reporting | `pulsewatch/dedup.py` | `tests/test_dedup.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **Sparse Temporal Engine** | 1s sparse bins, 900s gap default, declared coverage, distinct known/assumed zeros vs gaps | `pulsewatch/temporal.py` | `tests/test_temporal.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **PW-Scan: Baseline Model** | Adaptive initial block length {60, 300, 900, 3600}s with $E \ge 20$ | `pulsewatch/detector.py` | `tests/test_detector.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **PW-Scan: Contamination Masking** | Sticky masking with residual threshold $Z \ge 4.0$, iterative refitting (up to 5 iterations), excluded intervals | `pulsewatch/detector.py` | `tests/test_detector.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **PW-Scan: Dispersion Model** | Scale-dependent $\phi(w)$, Negative-Binomial $r = E/(\phi-1), q = E/(r+E)$ with Poisson lower bound | `pulsewatch/detector.py` | `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **PW-Scan: Tail Calculation** | Stable log-space Poisson/NB upper tail with geometric convergence error bounds / `tail_exact` flag | `pulsewatch/detector.py` | `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **PW-Scan: Multi-Scale Scan** | 10s, 60s, 300s overlapping scan; practical gates $X \ge 1.3E$ and $X - E \ge 5$ | `pulsewatch/detector.py` | `tests/test_detector.py`, `tests/test_traffic_lab.py` | **IMPLEMENTED** |
| **PW-Scan: Reliability States** | `HIGH`, `MEDIUM`, `LOW`, `ABSTAIN` with coverage, event count, and asymmetry ($[0.4, 2.5]$) rules | `pulsewatch/detector.py` | `tests/test_detector.py`, `tests/test_definitive_contracts.py` | **IMPLEMENTED** |
| **Incident Intelligence** | Facts, findings, patterns, unknown causes; boundary trimming; 6 canonical shapes | `pulsewatch/incident.py` | `tests/test_incident.py` | **IMPLEMENTED** |
| **Second-Pass Evidence** | Verified unchanged source file (SHA-256), client & route concentration tags (`single_client_dominated`, etc.) | `pulsewatch/incident.py` | `tests/test_incident.py`, `tests/test_cli.py` | **IMPLEMENTED** |
| **Canonical Report** | Schema `pulsewatch.report/1`, NaN/Inf sanitization, deterministic SHA-256 analysis hash | `pulsewatch/report.py` | `tests/test_report.py` | **IMPLEMENTED** |
| **Offline HTML Report** | Standalone zero-dependency HTML with Content Security Policy and escaped entities | `pulsewatch/html_report.py` | `tests/test_report.py`, `tests/test_cli.py` | **IMPLEMENTED** |
| **CLI Experience** | `summarize`, `analyze --json`, `report --html` independently executable | `pulsewatch/cli.py`, `bin/pulsewatch` | `tests/test_cli.py` | **IMPLEMENTED** |
| **Traffic Lab Benchmarks** | 13 scenario families (N0..N2, A1..A5, D1, L1, S1, G1, X1) vs 4 baselines on held-out seeds | `traffic_lab/benchmark_matrix.py` | `traffic_lab/benchmark_matrix.py` | **IMPLEMENTED** |
| **Modular Documentation** | Complete set of 6 technical documents + README | `docs/`, `README.md` | Verified | **IMPLEMENTED** |
