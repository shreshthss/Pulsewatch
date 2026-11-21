# PulseWatch — 2.5-Minute Demonstration & Video Script

**Target Video Duration:** 2 minutes 30 seconds  
**Objective:** Convince competition judges that PulseWatch reliably distinguishes genuine API traffic anomalies from misleading telemetry, duplicate logs, and coverage gaps.

---

## Act 1: The Problem & 15-Second Hook (0:00 – 0:30)

**Visual:** Terminal split screen with a noisy API telemetry log stream on the left and an alert dashboard on the right.  
**Voiceover:**
> "API engineers and SREs face a constant dilemma when investigating traffic surges: Is the API under heavy load, or did an upstream proxy just duplicate log entries? Did traffic drop to zero, or did the log shipper crash?
> Most anomaly detection tools blindly feed raw logs into static thresholds or general-purpose AI agents that hallucinate causes.
> PulseWatch solves this. It is an offline, evidence-driven traffic anomaly investigator built specifically for API request point-processes."

---

## Act 2: Running the Curated Demo Suite (0:30 – 1:00)

**Action on Screen:**
```bash
make demo
```
**Voiceover:**
> "Let’s run the PulseWatch demonstration suite. In seconds, PulseWatch processes five curated scenarios entirely offline with zero external dependencies."

**Screen Shows Terminal Output:**
The demo summary matrix appears:
```
SCENARIO                 RETAINED   DUPS REMOVED   COVERAGE   BASELINE   INCIDENTS
1_normal_traffic         2183       0              100.0%     HIGH       0
2_duplicate_replay       480        235            100.0%     MEDIUM     0
3_genuine_surge          2790       0              100.0%     LOW        1
4_coverage_gap           480        0              19.3%      ABSTAIN    0
5_ambiguous_regime       2280       0              100.0%     LOW        1
```
**Voiceover:**
> "Notice the first key distinction: Scenario 2 had a massive log burst, but PulseWatch removed 235 duplicate records using 16-byte BLAKE2b identity hashing, resulting in zero false alarms.
> In Scenario 4, when a collector outage created a 1,000-second gap, PulseWatch recognized the unknown gap and deliberately abstained rather than hallucinating an anomaly."

---

## Act 3: Inspecting a Genuine Incident (1:00 – 1:45)

**Action on Screen:**
Open `demo/3_genuine_surge_report.html` in the browser.

**Visual Focus:**
1. **Executive Summary Grid**: Shows retained records, zero duplicates, coverage ratio, and 1 detected incident.
2. **Telemetry Integrity & Coverage Ledger**: Explains exactness status and active seconds.
3. **Incident Card (INC-001)**:
   - Shape: `sustained_surge`
   - Tags: `broad_based`, `route_concentrated`
   - Traffic metrics: Observed 2,790 reqs, Estimated Excess +2,070, Peak 25 req/s.
   - Statistical finding: Max Z = 125.98 under Negative-Binomial dispersion.
   - Contributor breakdown: 100% concentrated on `/api/v1/items/search` across multiple callers.
4. **Operator Investigation Leads Callout**:
   - `↳ Check distributed load-test orchestration or multi-client test harness.`
   - `↳ Investigate endpoint '/api/v1/items/search' capacity, database query efficiency, and upstream dependency health.`

**Voiceover:**
> "Here is the offline static HTML report generated directly from canonical JSON.
> PulseWatch doesn't just give a score; it localizes the anomaly's exact boundaries using a sparse one-second temporal engine.
> It breaks down leading routes and callers, verifies that the surge is concentrated on the search endpoint, and generates deterministic, rule-based investigation leads for the engineer."

---

## Act 4: The Intelligent Technique & Architecture (1:45 – 2:15)

**Visual:** Slide or diagram illustrating PW-Scan:
`Sparse 1s -> Adaptive Baseline Block -> Sticky Masking (Residual Z >= 4.0) -> Negative-Binomial Dispersion -> Multi-scale Scanning (10s, 60s, 300s) -> Boundary Trimming`

**Voiceover:**
> "Why does PulseWatch succeed where basic detectors fail?
> Our PW-Scan detector uses discrete Poisson and Negative-Binomial count models rather than continuous approximations.
> When traffic surges, sticky contamination masking excludes the anomalous interval and refits a clean baseline, preventing the surge from blinding the detector.
> And in our held-out 13-scenario benchmark against moving averages and EWMA, PulseWatch achieved 100% precision and exact 1.0 temporal IoU, while moving averages suffered from lag and false alarms."

---

## Act 5: Responsible AI & Wrap-Up (2:15 – 2:30)

**Visual:** Show clean terminal test execution:
```bash
python3 -m unittest discover tests -v
```
All 29 tests pass in 0.2s.

**Voiceover:**
> "PulseWatch uses no external LLMs or cloud APIs. Your telemetry never leaves your machine. It provides mathematically sound, explainable evidence while keeping the engineer firmly in control.
> PulseWatch: Evidence-driven offline API traffic anomaly investigation. Thank you."
