# PulseWatch

> **Evidence-Driven Offline API Traffic Anomaly Investigation**  
> *Built for the API Traffic Watch Challenge — Aegython 2026*  
> 
> 🌐 **Live Web Experience & Interactive Demo**: [https://shreshthss.github.io/Pulsewatch/](https://shreshthss.github.io/Pulsewatch/)  
> 📦 **GitHub Repository**: [https://github.com/shreshthss/Pulsewatch](https://github.com/shreshthss/Pulsewatch)

---

To set up **PulseWatch on a new system**, you need Python, Git, and the project repository. The application runs locally and does not require external Python packages, a database, Docker, or an API key.

Below is the installation procedure for Linux (including Arch Linux).

# **PulseWatch — System Setup Guide**

## **1. Install prerequisites**

You need:

| Requirement     | Purpose                          |
| --------------- | -------------------------------- |
| Python 3.9+     | Runs the detection engine        |
| Git             | Downloads the repository         |
| Make (optional) | Runs predefined project commands |
| Web browser     | Opens generated HTML reports     |

On Arch Linux:

```
sudo pacman -Syu
sudo pacman -S git python make
```

On Ubuntu/Debian:

```
sudo apt update
sudo apt install git python3 make
```

Verify installations:

```
python3 --version
git --version
make --version
```

## **2. Download the repository**

Clone the GitHub repository:

```
git clone https://github.com/shreshthss/Pulsewatch.git
```

Enter the project directory:

```
cd Pulsewatch
```

Your working directory should now contain the project files, including:

- `demo.py`
- `bin/`
- `data/`
- `docs/`
- `tests/`
- `traffic_lab/`
- `Makefile`

## **3. Run the demonstration**

No dependency installation required

Execute:

```
make demo
```

Alternatively:

```
python3 demo.py
```

This executes five predefined scenarios:

1. Normal traffic
2. Duplicate request replay
3. Genuine traffic surge
4. Coverage gap
5. Ambiguous traffic regime

The terminal displays a summary of retained requests, duplicates removed, coverage, baseline reliability and detected incidents.

## **4. Open the generated investigation report**

After the demonstration, open one of the generated HTML reports:

```
xdg-open demo/3_genuine_surge_report.html
```

This opens the report in your default browser.

You can inspect the incident information and investigation interface without running a server.

## **5. Run PulseWatch on actual log data**

PulseWatch accepts JSONL (JSON Lines) traffic logs.

The standard workflow consists of three commands:

**A. Summarize traffic**

```
./bin/pulsewatch summarize data/sample.jsonl
```

**B. Analyse anomalies**

```
./bin/pulsewatch analyze data/sample.jsonl \
    --json data/analysis.json
```

**C. Generate an HTML investigation report**

```
./bin/pulsewatch report data/analysis.json \
    --html data/report.html
```

Open the generated report:

```
xdg-open data/report.html
```

## **6. Run the automated tests**

To verify that the installation is working:

```
make test
```

Alternatively:

```
python3 -m unittest discover tests -v
```

The repository documents 29 automated tests covering ingestion, deduplication, temporal gaps, statistical detection, reporting and CLI functionality.

## **7. Run the benchmark suite (optional)**

To evaluate the detector against the synthetic scenario matrix and comparison baselines:

```
make matrix
```

Alternatively:

```
python3 -m traffic_lab.benchmark_matrix
```

## **Complete installation sequence**

If you want the shortest possible installation procedure, execute:

```

# 1. Install prerequisites (Arch Linux)
sudo pacman -S git python make

# 2. Clone repository
git clone https://github.com/shreshthss/Pulsewatch.git

# 3. Enter directory
cd Pulsewatch

# 4. Run demonstration
make demo

# 5. Open generated report
xdg-open demo/3_genuine_surge_report.html

# 6. Verify installation
make test

```

## **What runs locally and what runs online?**

**Local installation**

Full engine

- Python-based ingestion and analysis
- Deduplication and statistical detection
- PW-Scan anomaly detection
- JSON output and HTML report generation
- Offline execution

  **GitHub Pages**

  Web demonstration
- Interactive scenario explorer
- Pre-generated reports
- Benchmark tables
- No local installation required to explore the demonstration

  The GitHub Pages website does not run the full Python detection engine.

**Official resources**

- [GitHub Repository](https://github.com/shreshthss/Pulsewatch?utm_source=chatgpt.com)
- [README and Setup Instructions](https://github.com/shreshthss/Pulsewatch?utm_source=chatgpt.com#2-quick-start--curated-judge-demonstration)
- [Live Demonstration](https://shreshthss.github.io/Pulsewatch/?utm_source=chatgpt.com)

**Important:** For a fresh-system demonstration, `git clone → make demo → open HTML report` is the simplest complete workflow.


## 1. 15-Second Executive Summary

API performance telemetry is messy: proxy retries create phantom traffic spikes, while telemetry collector outages masquerade as service quiescence. Most monitoring tools either use crude static thresholds (causing false-alarm fatigue) or opaque AI agents that hallucinate causes.

**PulseWatch** solves this. It is a strictly offline, zero-dependency Python tool that helps API engineers and SREs distinguish genuine traffic anomalies from misleading telemetry:
1. **Deduplicates Replayed Logs** using a 4-level identity hierarchy and 16-byte BLAKE2b digests before running detection.
2. **Tracks Coverage & Gaps** using a 1-second sparse temporal engine, explicitly distinguishing verified zero traffic from unknown telemetry outages.
3. **Detects Multi-Scale Anomalies** with **PW-Scan**, modeling discrete Poisson and Negative-Binomial count dispersion across 10s, 60s, and 300s timescales.
4. **Resists Baseline Contamination** via iterative sticky masking ($Z \ge 4.0$).
5. **Provides Operator-Centric Evidence** with deterministic investigation leads, route/caller concentration tags, and a self-contained offline HTML report.

---

## 2. Quick Start & Curated Judge Demonstration

PulseWatch runs on **standard Python 3.9+** with **zero external package dependencies**.

### Run the Curated 5-Scenario Demo Suite
```bash
make demo
# Or directly with Python:
python3 demo.py
```

This single command generates and executes 5 curated scenarios demonstrating key differentiators:
```
==============================================================================
PULSEWATCH — AEGYTHON 2026 JUDGE DEMONSTRATION SUITE
==============================================================================
SCENARIO                 RETAINED   DUPS REMOVED   COVERAGE   BASELINE   INCIDENTS
------------------------------------------------------------------------------
1_normal_traffic         2183       0              100.0%     HIGH       0
2_duplicate_replay       480        235            100.0%     MEDIUM     0
3_genuine_surge          2790       0              100.0%     LOW        1
4_coverage_gap           480        0              19.3%      ABSTAIN    0
5_ambiguous_regime       2280       0              100.0%     LOW        1
==============================================================================
```
* **Baseline Reliability**: `1_normal_traffic` runs for 720 seconds (12 min $\ge 600$s) to demonstrate `HIGH` baseline reliability with 0 false alarms. Shorter runs (e.g. 360s in `2_duplicate_replay`) conservatively yield `MEDIUM` confidence because shorter windows provide a smaller statistical sample.
Open any generated standalone report (e.g. `demo/3_genuine_surge_report.html`) in your browser to inspect the operator investigation interface.

### Standard CLI Workflow
```bash
# 1. Summarize deduplicated traffic windows
./bin/pulsewatch summarize data/sample.jsonl

# 2. Analyze traffic anomalies and output canonical JSON
./bin/pulsewatch analyze data/sample.jsonl --json data/analysis.json

# 3. Render self-contained offline HTML report from JSON
./bin/pulsewatch report data/analysis.json --html data/report.html
```

---

## 3. Core Architecture & Differentiators

```
JSONL Ingest -> BLAKE2b Dedup -> Sparse 1s Engine -> Multi-Scale PW-Scan -> Operator Leads -> Canonical Report & HTML
```

### A. Identity-Aware Deduplication (BLAKE2b)
PulseWatch enforces a strict 4-level identity hierarchy:
1. `(source, seq)`: Sequence number per reporting gateway.
2. `(source, trace_id, span_id)`: Distributed tracing context.
3. `(source, request_id)`: Unique request UUID.
4. Records lacking usable identity fields.

* **Exactness Guarantee**: Deduplication only occurs when an identity match also matches normalized event attributes (`timestamp_sec`, `method`, `route`, `client`, `status`).
* **No Content-Only Dropping**: Distinct requests (e.g. valid polling with unique IDs) are never deduplicated even if their contents look identical.
* **Degradation Transparency**: Emits explicit exactness ledgers (`fully_verified_exact`, `partial_identity_coverage`, `degraded_due_to_capacity_eviction`).

### B. Sparse 1-Second Temporal Engine
* Memory allocation is strictly $O(\text{active events})$, never dense arrays proportional to elapsed wall-clock time.
* **Coverage Semantics**:
  - *Declared Coverage*: Specified via `{"_pw": "coverage", "start": ..., "end": ...}` control records.
  - *Known Zero*: Verified zero-traffic seconds within declared coverage.
  - *Assumed Zero*: Intermediate gaps $\le 900$s between active records.
  - *Unknown Gaps*: Outages $> 900$s. The detector **never** scans across unknown gaps or assumes missing data is zero.

### C. PW-Scan Statistical Detection Engine
* **Discrete Count Modeling**: Models integer request counts using scale-dependent dispersion $\phi(w) = \frac{\operatorname{Var}(S_w)}{\operatorname{E}(S_w)}$. Overdispersed traffic ($\phi > 1.0$) is modeled using Negative-Binomial distribution parameters ($r = \frac{E}{\phi-1}$, $q = \frac{E}{r+E}$).
* **Sticky Contamination Masking**: Screens candidate windows ($Z \ge 4.0$) across fine timescales, masks elevated intervals, and refits a clean baseline across up to 5 iterations.
* **Multi-Scale Scanning**: Evaluates overlapping windows at 10s, 60s, and 300s, gated by relative ($\ge 1.3\times$) and absolute ($\Delta \ge 4, 12, 25$) elevation thresholds.
* **Defensible Tail Probabilities**: Stable log-space upper tail probabilities with rigorous geometric series truncation error bounds.
* **Baseline Reliability States**: `HIGH`, `MEDIUM`, `LOW`, or `ABSTAIN`.

### D. Operator-Centric Evidence & Leads
Each incident includes:
1. **Overview & Traffic Deviation**: Start/end timestamps, duration, peak 1s rate, observed volume, and estimated excess requests.
2. **Statistical Finding**: Maximum Z-score, minimum log p-value, fired scales.
3. **Geometric Shape**: `isolated_spike`, `micro_burst`, `sustained_surge`, `ramp`, `level_shift_candidate`, `unclassified`.
4. **Contributor Evidence**: Leading routes, leading callers, and status distribution with concentration tags (`single_client_dominated`, `broad_based`, `route_concentrated`, `status_shifted`).
5. **Deterministic Investigation Leads**: Actionable next-check guidance grounded in observed evidence (e.g. check client retry loops, backend database indexes, or gateway timeout logs).

---

## 4. Intelligent Technique & Responsible AI Rationale

### Why Statistical Count Modeling Over Generative LLMs?
1. **Auditable & Deterministic**: Anomaly detection on discrete point processes is fundamentally a statistical problem. PW-Scan produces mathematically rigorous, reproducible results with zero hallucination risk.
2. **Privacy & Zero Data Leakage**: API telemetry contains sensitive client IPs, authorization routes, and internal topology. PulseWatch operates **100% offline**; telemetry never leaves the host machine.
3. **Human-in-the-Loop Philosophy**: PulseWatch does not make autonomous operational changes or claim unsupported root causes. It surfaces verifiable evidence and structured investigation leads, keeping the engineer firmly in control.

---

## 5. Benchmark & Validation Evidence

PulseWatch includes a dedicated `traffic_lab` evaluation suite with **separate development and held-out evaluation seeds**.

### 13-Scenario Benchmark Matrix vs. 4 Baselines (Evaluation Seed: 20261121)
```bash
make matrix
# Or:
python3 -m traffic_lab.benchmark_matrix
```

Detailed metrics across all 13 scenarios, 5 detectors, and 3 held-out evaluation seeds are documented in **[Benchmark Evidence](docs/BENCHMARK_RESULTS.md)** and stored in [`data/benchmark_results.json`](data/benchmark_results.json).

| Scenario Family | PW-Scan F1 (IoU) | Fixed Threshold | Trailing Mean + Std | EWMA | Simple Median/MAD |
|---|---|---|---|---|---|
| `N0_poisson` (Stationary) | **1.000 (0.00)** | 0.000 (FP=1) | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) |
| `N1_sparse` (Low arrival) | **1.000 (0.00)** | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) |
| `N2_overdispersed` (Burst) | **1.000 (0.00)** | 0.000 (FP=1) | 1.000 (0.00) | 0.000 (FP=1) | 1.000 (0.00) |
| `A1_isolated_spike` (12s) | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) |
| `A2_micro_burst` (4s) | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) |
| `A3_sustained_surge` (90s) | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (0.67) | 1.000 (0.67) | 1.000 (1.00) |
| `A4_ramp` (60s) | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) |
| `A5_distinct_flood` (30s) | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) | 1.000 (1.00) |
| `D1_duplicate_storm` | **1.000 (0.00)** | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) |
| `L1_identity_stripped` | **1.000 (0.00)** | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) |
| `S1_level_shift` | **1.000 (1.00)** | 1.000 (1.00) | 1.000 (0.50) | 1.000 (0.50) | 1.000 (1.00) |
| `G1_long_gap` (>900s) | **1.000 (0.00)** | 1.000 (0.00) | 0.000 (FP=1) | 0.000 (FP=1) | 0.000 (FP=2) |
| `X1_hostile_strings` | **1.000 (0.00)** | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) | 1.000 (0.00) |

* **Zero Gap False Positives**: PW-Scan isolates continuous observation segments, preventing spurious alerts after telemetry collection resumes.
* **Exact Boundary Localization**: Sparse 1-second boundary trimming achieves mean temporal IoU = 1.00 on anomalies, eliminating moving-average lag.

---

## 6. Running the Test Suite

Execute the full automated test suite (29 tests across ingestion, BLAKE2b deduplication, temporal gaps, PW-Scan, incident intelligence, report contracts, CLI, and traffic lab):

```bash
make test
# Or:
python3 -m unittest discover tests -v
```
All tests execute completely offline and finish in under 0.3 seconds.

---

## 7. Documentation & Judge Package

- **[Benchmark Evidence](docs/BENCHMARK_RESULTS.md)**: Full reproducible metrics table across all 13 scenarios and 4 comparison baselines over multiple evaluation seeds.
- **[Competitive Positioning](docs/COMPETITIVE_POSITIONING.md)**: Cross-track comparison against AI agent, authentication, and cybersecurity archetypes.
- **[Demonstration Script](docs/DEMO_SCRIPT.md)**: 2.5-minute video presentation script with exact commands and visual transitions.
- **[Judge Q&A Defense](docs/JUDGE_QA.md)**: 16 technically grounded answers to common competition questions.
- **[Presentation Outline](docs/SLIDES.md)**: 5-slide pitch outline.
- **[Architecture Deep-Dive](docs/ARCHITECTURE.md)**: Component boundaries and data-flow specifications.
- **[Statistical Methodology](docs/STATISTICAL_METHOD.md)**: Full mathematical treatment of PW-Scan, dispersion, and tail estimation.
- **[Canonical Report Contract](docs/REPORT_SCHEMA.md)**: Structure and fields of `pulsewatch.report/1`.
- **[Validation Evidence](docs/VALIDATION.md)**: Detailed benchmark scenario methodology and metrics.
- **[Security & Limitations](docs/SECURITY_AND_LIMITATIONS.md)**: Sandbox boundaries, resource limits, and disclaimers.

---

## 8. Status of Capabilities & Rigor Disclosures

To preserve scientific integrity, capabilities are explicitly partitioned by verification status:

| Capability Tier | Scope & Current Implementation Status |
|---|---|
| **Implemented** | Offline streaming ingestion, 4-level BLAKE2b deduplication, sparse 1s temporal engine, PW-Scan multi-scale detector (10s/60s/300s), sticky contamination masking ($Z \ge 4.0$), Poisson/Negative-Binomial tail modeling, 6 geometric incident shapes, contributor concentration tags, canonical JSON schema (`pulsewatch.report/1`), and self-contained HTML reports. |
| **Tested** | 29 automated unit and contract tests executing offline in `tests/` covering input validation, eviction, nanosecond time parsing, tail convergence bounds, CLI workflows, and XSS sanitization. |
| **Evaluated Synthetically** | 13 scenario families benchmarked against 4 baseline detectors across 3 fixed held-out evaluation seeds (`20261121`, `20261122`, `20261123`) with ground-truth label isolation. |
| **Future Work** | Online streaming window ingestion, multi-node distributed trace aggregation, cross-datacenter clock-drift synchronization, and long-range diurnality modeling (>7 days). |

### Disclaimers & Known Limitations
1. **Offline Retrospective Focus**: PulseWatch processes offline logs; it does not capture live streaming packets or perform active mitigation.
2. **Identity Dependent Deduplication**: Telemetry stripped of request IDs or monotonic sequence numbers cannot be deduplicated with mathematical certainty; PulseWatch retains these records unverified and explicitly discloses the exactness status.
3. **No External Threat Attribution**: Offline synthetic monitoring cannot identify threat-actor intent or physical infrastructure faults without underlying telemetry evidence.

---

## 9. Web Demonstration & GitHub Pages Deployment

The static web console is deployed and hosted on GitHub Pages:  
**[https://shreshthss.github.io/Pulsewatch/](https://shreshthss.github.io/Pulsewatch/)**

### Important Architectural Distinction
* **Static Web Demonstration**: GitHub Pages hosts the static console, interactive scenario explorer, pre-generated scenario reports, and benchmark tables. It provides zero-install access to explore the outputs, but does not execute live traffic captures or browser-side detection.
* **Local Python Engine**: The full analysis engine (`pulsewatch`) runs locally and completely offline using Python 3.9+ standard library. It processes user-supplied JSONL logs without any external network access.
* **Automated CI/CD**: The repository includes `.github/workflows/pages.yml` (official GitHub Pages deployment action) and `.github/workflows/test.yml` (automated unit tests on Python 3.11).
