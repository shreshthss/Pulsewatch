# PulseWatch Judge Demonstration & Forensic Verification Runbook

## Overview

PulseWatch is a production-grade, zero-external-dependency (Python 3.9+ standard library only) API traffic anomaly detection and forensic investigation system. This document provides a step-by-step evaluation guide for judges, technical reviewers, and security investigators.

PulseWatch operates on raw streaming or batch JSONL proxy and API gateway logs. It performs single-pass identity deduplication, sparse temporal aggregation, multi-scale sliding Poisson/Negative-Binomial anomaly scanning, incident merging, and second-pass cryptographic evidence attribution.

---

## Quick Start & Self-Contained Verification

Execute the entire test suite and automated demonstration in seconds:

```bash
# 1. Run all unit and integration tests (133 tests, 0 failures, standard library only)
make test

# 2. Run the comprehensive automated 6-scene judge demonstration
python3 scripts/judge_demo.py

# 3. Run the interactive / visual demo suite generating offline HTML reports
make demo
```

---

## The Six Demonstration Scenes

The automated suite (`scripts/judge_demo.py`) exercises the core algorithmic guarantees across six scenes:

### Scene 1: Traffic Nominal (Poisson Baseline)
- **Workload:** `demo/1_normal_traffic.jsonl` (720 seconds of stationary Poisson traffic).
- **Guarantee:** Zero false alarms.
- **Verification:** Evaluates baseline stability test ($Q < 20$) and verifies fitted rate $\lambda \approx 3.0$ req/s with 0 candidate detections and 0 incidents.

### Scene 2: Network Outage (Coverage Gap Awareness)
- **Workload:** `demo/4_coverage_gap.jsonl` (240 active seconds surrounding a 1,001s unobserved collector outage).
- **Guarantee:** Missing intervals exceeding the gap threshold (900s) are classified as `unknown_gaps` rather than assumed zero.
- **Verification:** Gap boundaries $[1795237320, 1795238320)$ are isolated from exposure accounting. No false spikes or spurious recovery incidents are emitted.

### Scene 3: Genuine Traffic Surge (Multi-Scale Detection & Attribution)
- **Workload:** `demo/3_genuine_surge.jsonl` (2,790 records with a sustained 90s surge).
- **Guarantee:** Anomaly detected across multi-scale windows (10s, 60s, 300s); excess counts isolated; routes and client contributors attributed in a second streaming pass.
- **Verification:** Emits incident `INC-001`, calculates peak 1s rate (25 req/s), max z-score (125.98), labels concentration tags (`broad_based`, `route_concentrated`), and outputs investigator next steps.

### Scene 4: Replay Attack (Duplicate Storm Filtering)
- **Workload:** `demo/2_duplicate_replay.jsonl` (duplicate network proxy retransmissions).
- **Guarantee:** Bit-packed single-pass deduplication suppresses duplicate replay storms before temporal aggregation.
- **Verification:** Ingests 715 records, removes 235 exact replays, retains 480 valid unique records with `fully_verified_exact` status, emitting 0 false alarms.

### Scene 5: World Cup 1998 Real-World Workload Scaling
- **Workload:** Authentic binary request traces from the 1998 FIFA World Cup (`wc_day20_1.gz`, `wc_day46_1.gz`, `wc_day78_1.gz`).
- **Guarantee:** Production streaming throughput handling $> 210,000$ events per 15-minute slice with bounded memory and high baseline reliability.
- **Verification:** Reports sample counts, baseline rates (up to 237.58 req/s on tournament opening), zero invalid records injected, and cryptographic analysis digests matching recorded benchmarks.

### Scene 6: Component Ablation Studies
- **Dataset:** `data/ablation_results.json` (`pulsewatch.ablation/1`).
- **Guarantee:** Empirical justification of every architectural subsystem across the 51001–51010 seed cohort.
- **Verification:** Demonstrates the severe failure modes when components are removed:
  1. *Removing Deduplication:* Replays cause massive false alarms.
  2. *Removing Coverage Awareness:* Outages trigger false alarms upon restart.
  3. *Removing Dispersion Scaling:* Real overdispersed variance causes false positive rate to spike from 19.2/day to 136.8/day.
  4. *Removing Contamination Masking:* Unmasked spikes corrupt baseline estimation and destroy recall.
  5. *Removing Multi-Scale Scanning:* Single-scale models miss short bursts ($< 60$s) or diffuse long elevation ($> 300$s).

---

## Independent Cryptographic Verification & Reproduction

PulseWatch provides independent, tamper-evident verification. Every analysis outputs a canonical report and a sidecar manifest:

### 1. Execute Pipeline and Generate Canonical Artifacts

```bash
python3 -m pulsewatch.cli analyze demo/3_genuine_surge.jsonl --json /tmp/surge.json
```
Output:
- Canonical JSON Report: `/tmp/surge.json`
- Sidecar Manifest: `/tmp/surge.manifest.json`

### 2. Verify Cryptographic Integrity and Reproduce Analysis

```bash
python3 -m pulsewatch.cli verify /tmp/surge.json --input demo/3_genuine_surge.jsonl --reproduce
```

Expected verification matrix:
```
============================================================================
PULSEWATCH VERIFICATION REPORT: /tmp/surge.json
============================================================================
ID   STATUS  RULE NAME                     DETAILS
----------------------------------------------------------------------------
R1   PASS    Report parses                Valid JSON object
R2   PASS    Schema supported             Schema pulsewatch.report/1 (rev 2)
R3   PASS    Metadata completeness        All required schema fields present
R4   PASS    Analysis hash integrity      Hash verified: ...
R5   PASS    Detector config digest       Config matches digest
M1   PASS    Manifest presence            Found /tmp/surge.manifest.json
M2   PASS    Manifest self-digest         Manifest self-digest verified
M3   PASS    Manifest-report agreement    Manifest agrees with report on all fields
M4   PASS    Report byte integrity        Report file bytes match manifest
I1   PASS    Input file integrity         Input bytes match source_sha256: ...
P1   PASS    Deterministic reproduction   Reproduced byte-identical analysis_hash: ...
============================================================================
RESULT: VERIFIED (11 pass, 0 warn, 0 skipped, 0 fail)
============================================================================
```

### 3. Verify Tamper-Evidence

If an adversary modifies any byte in the report (e.g. changing an incident count or timestamp) without knowing the private canonical ordering, Rule R4 immediately flags a hash mismatch:
```bash
# Modify report content
sed -i 's/"valid_records": 2790/"valid_records": 2791/' /tmp/surge.json

# Verifier detects tampering
python3 -m pulsewatch.cli verify /tmp/surge.json
# Returns exit code 1: R4 FAIL: Hash mismatch
```

### 4. Render Offline Forensic HTML Report

```bash
python3 -m pulsewatch.cli report /tmp/surge.json --html /tmp/surge.html --ablation data/ablation_results.json
```
Open `/tmp/surge.html` in any web browser. It features:
- Complete 10-section layout optimized for printing and PDF export.
- Pure inline SVG interactive timeline (no JavaScript or external CDNs).
- Full contributor attribution, concentration tags, and investigation leads.
- Appendix A: Full component ablation matrix embedded.
- Appendix B: UTC 60-second evaluation windows.

---

## Statistical Rigor & Zero-Denominator Honesty

PulseWatch adheres to strict statistical principles:
1. **Undefined Precision/Recall in Negative Scenarios:** When evaluating scenarios with zero ground-truth incidents, precision and recall have a zero denominator ($0 / 0$). Rather than fabricating an artificial `1.0`, PulseWatch returns `None` (`null` in JSON), reporting exposure duration, false incidents per day, and Garwood Poisson exact upper bounds.
2. **Monotonic Calibration:** Threshold calibration across candidate grids $z \in [3.0, \dots, 10.0]$ requires that a selected threshold passes the target false-incident rate across *all five* synthetic null families (`poisson_low`, `poisson`, `nb_iid`, `modulated`, `gaps`) at that threshold and *every higher threshold*.
3. **Disjoint Seed Discipline:** Null simulation, benchmark evaluation, and ablation testing use strictly disjoint seed sets, preventing data snooping and synthetic overfitting.
