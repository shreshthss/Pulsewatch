# PulseWatch Architecture

## 1. Overview and Core Pipeline
PulseWatch is a strictly offline synthetic API traffic monitoring and anomaly detection system. It processes offline API request logs in JSONL format, validates and deduplicates them using an explicit identity hierarchy, tracks temporal coverage using a sparse 1-second representation, detects multi-scale traffic anomalies via PW-Scan, and produces a canonical structured report.

The workflow follows a one-directional pipeline:
```
Load JSONL -> Validate & Normalize -> Deduplicate (BLAKE2b) -> Sparse 1s Temporal Engine -> PW-Scan Multi-Scale Detector -> Incident Merger & 2nd-Pass Evidence -> Canonical Report & HTML Export
```

## 2. Module Responsibilities
- `pulsewatch/ingest.py`: Parses JSONL records, normalizes timestamps into integer UTC nanoseconds, parses declared coverage control records (`_pw: coverage`), tracks record and line limits, and records ingestion accounting.
- `pulsewatch/dedup.py`: Implements 16-byte BLAKE2b identity hashing, enforces the 4-level identity hierarchy, maintains bounded LRU state, records exactness status, and tracks identity conflicts and capacity degradation.
- `pulsewatch/temporal.py`: Sparse 1-second time engine. Distinguishes known zeros, assumed zeros, and unknown gaps (>900s default). Produces UTC-aligned 60s windows and partitions timeline into continuous segments.
- `pulsewatch/detector.py`: PW-Scan statistical detector. Selects initial baseline block length, performs sticky contamination masking, refits clean baseline, models scale dispersion $\phi(w)$, evaluates Poisson / Negative-Binomial tail probabilities, and evaluates baseline reliability (`HIGH`, `MEDIUM`, `LOW`, `ABSTAIN`).
- `pulsewatch/incident.py`: Merges cross-scale detections, trims incident boundaries to active excess, classifies geometric shapes, and executes bounded second-pass evidence collection over unchanged inputs.
- `pulsewatch/report.py`: Assembles canonical `pulsewatch.report/1` contract, computes deterministic SHA-256 analysis hash, sanitizes non-finite floats, and formats CLI output.
- `pulsewatch/html_report.py`: Standalone offline HTML report generator with strict Content Security Policy and HTML entity escaping.
- `pulsewatch/cli.py`: Command-line interface providing `summarize`, `analyze`, and `report` subcommands.
- `traffic_lab/`: Independent benchmarking and evaluation suite (isolated from core).
