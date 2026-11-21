# PulseWatch — Judge Presentation Outline (5 Slides)

---

## Slide 1: Problem & Target User
* **Title**: PulseWatch: Evidence-Driven Offline API Traffic Anomaly Investigation
* **The Problem**:
  - Telemetry is noisy: Proxy retries create phantom traffic spikes; logging outages look like service dropouts.
  - Existing detectors either use crude static thresholds (high false alarms) or black-box AI agents (hallucinated root causes).
* **Target Audience**: Backend developers, Platform Engineers, and SREs validating synthetic load tests and diagnosing traffic anomalies.
* **Core Value**: Distinguishes genuine traffic surges from telemetry artifacts with mathematical rigor.

---

## Slide 2: The Solution & Architectural Differentiators
* **One-Directional Pipeline**:
  `JSONL Ingest -> 16-byte BLAKE2b Dedup -> Sparse 1s Temporal Engine -> Multi-scale PW-Scan -> Operator Leads -> Canonical Report`
* **Four Fundamental Differentiators**:
  1. **Identity Hierarchy Deduplication**: Deduplicates only when `(source, seq/trace/req_id)` and normalized event attributes agree. Never drops distinct requests.
  2. **Sparse 1-Second Gap Tracking**: Distinguishes Known Zero (verified quiescence) from Unknown Gaps (collector outages). Never scans across gaps.
  3. **PW-Scan Statistical Engine**: Discrete Poisson / Negative-Binomial count modeling across 10s, 60s, and 300s.
  4. **Sticky Contamination Masking**: Prevents surges from contaminating the baseline and blinding subsequent detections.

---

## Slide 3: Intelligent Statistical Technique (PW-Scan)
* **Adaptive Baseline**: Selects block duration from $\{60, 300, 900, 3600\}$s where expected arrivals $E \ge 20$.
* **Dispersion Modeling**:
  - Computes scale-dependent dispersion $\phi(w) = \frac{\operatorname{Var}(S_w)}{\operatorname{E}(S_w)}$.
  - Uses discrete Negative-Binomial distribution parameters when traffic is overdispersed ($\phi > 1.0$).
* **Numerically Defensible Tail Evaluation**: Stable log-space probabilities with geometric series truncation error bounds.
* **Explicit Reliability States**: Outputs `HIGH`, `MEDIUM`, `LOW`, or `ABSTAIN` (refuses to guess on insufficient data).

---

## Slide 4: Validation Evidence & Live Demonstration
* **Single Command Execution**: `make demo` runs 5 curated scenarios in under 3 seconds.
  - Scenario 1 (Normal): 0 false positives on stationary arrivals.
  - Scenario 2 (Duplicate Storm): Filters 235 replayed proxy logs; 0 false alarms.
  - Scenario 3 (Genuine Surge): Detects 90s surge, localizes boundaries (IoU = 1.0), tags `broad_based` and `route_concentrated`.
  - Scenario 4 (Coverage Gap): Identifies 1,000s outage gap; abstains cleanly without false alarms.
  - Scenario 5 (Regime Shift): Emits baseline asymmetry warning and flags `level_shift_candidate`.
* **Benchmark Matrix**: 13 scenario families tested against 4 reference baselines (Fixed threshold, Trailing mean+std, EWMA, Median/MAD).

---

## Slide 5: Responsible AI, Security & Future Roadmap
* **Zero-Leakage Security Guarantee**:
  - 100% offline; zero external network requests, zero socket connections, zero subprocesses.
  - Strict Content Security Policy on static HTML reports; all strings escaped.
* **Why No LLM?**:
  - Anomaly detection on discrete count processes is a statistical problem, not a text generation problem.
  - Statistical models are 100% deterministic, auditable, and reproducible.
* **Human-in-the-Loop Philosophy**:
  - PulseWatch presents evidence and suggests deterministic investigation leads.
  - The human engineer remains in full operational control.
* **Roadmap**: Read-only Model Context Protocol (MCP) server for human-supervised agent queries.
