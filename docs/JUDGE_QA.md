# PulseWatch — Judge Q&A Technical Defense

Comprehensive, technically grounded answers based on the actual PulseWatch codebase.

---

### 1. What problem does PulseWatch solve?
PulseWatch solves the problem of distinguishing genuine API traffic anomalies from misleading telemetry in offline request logs. It prevents false alarms caused by duplicate proxy logs or telemetry collection outages, models discrete count dispersion across multiple timescales, and provides evidence-backed investigation leads without hallucinating root causes.

### 2. Who would use it?
Backend developers, SREs, platform engineers, and performance test engineers analyzing synthetic API benchmark runs, load-test telemetry, or offline post-incident logs.

### 3. Why is this different from a basic threshold-based spike detector?
Basic detectors use static request counts (e.g., alert if $> 150$ req/min) or continuous moving averages (Gaussian mean $\pm 3\sigma$). They fail because:
1. Static thresholds cannot adapt to varying traffic rates across different API routes or times of day.
2. Moving averages get contaminated by the surge itself, inflating the baseline and masking subsequent anomalies.
3. They lack multi-scale scanning, missing short 3–5-second microbursts or slow ramps.
4. They do not deduplicate logs, mistaking replayed telemetry for genuine traffic spikes.

### 4. Why is deduplication performed before detection?
If deduplication is deferred or omitted, duplicate proxy delivery, logging retries, or replayed logs will inflate arrival counts, causing detectors to fire false alarms on phantom traffic. Deduplicating first ensures that statistical baselines and detection windows reflect actual incoming request volume.

### 5. How do you distinguish duplicate log records from legitimate identical requests?
PulseWatch enforces a strict 4-level identity hierarchy:
1. `(source, seq)`: Monotonic sequence number per emitting source.
2. `(source, trace_id, span_id)`: Distributed tracing context.
3. `(source, request_id)`: Unique request UUID.
4. Records lacking usable identity fields.

An event is **only** deduplicated if both its identity key and its normalized event attributes (`timestamp_sec`, `method`, `route`, `client`, `status`) match an earlier record.
Distinct requests (e.g. repeated polling by the same client with unique IDs) are **always retained**. Records without identity are retained and disclosed as `identity_ineligible`.

### 6. How does the system treat missing data?
PulseWatch distinguishes between three zero states:
- **Known Zero**: Periods within explicitly declared coverage intervals (`_pw: coverage`) where zero requests were observed.
- **Assumed Zero**: Unobserved intervals $\le 900$ seconds between active events within an active continuous block.
- **Unknown Coverage Gaps**: Intervals $> 900$ seconds without events (e.g. collector crashes).

PulseWatch **never** scans across unknown gaps and never treats missing data as zero. Windows overlapping unknown gaps are labeled `is_complete: false`, and the detector abstains if coverage drops below defensible thresholds.

### 7. How does PW-Scan establish its baseline?
1. **Adaptive Block Selection**: Selects an initial block size $L \in \{60, 300, 900, 3600\}$s, choosing the smallest block where expected count $E \ge 20$.
2. **Robust Velocity**: Estimates initial rate $\tilde{r}$ using the median of block counts.
3. **Sticky Contamination Masking**: Screens initial candidate windows using standardized residual $Z \ge 4.0$. Elevated windows are added to a sticky exclusion mask, and the baseline is refit on clean coverage across up to 5 iterations.

### 8. Why use Poisson and Negative-Binomial models?
API request arrivals are discrete integer point processes. Continuous Gaussian assumptions fail for low arrival rates (predicting negative counts) and ignore overdispersion.
PW-Scan measures scale-dependent dispersion $\phi(w) = \frac{\operatorname{Var}(S_w)}{\operatorname{E}(S_w)}$:
- When $\phi \approx 1.0$: Arrival counts follow a Poisson distribution.
- When $\phi > 1.05$: Traffic exhibits burstiness and overdispersion, modeled via Negative-Binomial parameters $r = \frac{E}{\phi - 1}$ and $q = \frac{E}{r + E}$.

### 9. How do you control false alarms?
1. **Practical Elevation Gates**: Requires both an absolute count excess ($X - E \ge 4$ for 10s, $\ge 12$ for 60s, $\ge 25$ for 300s) and a relative elevation ratio ($X \ge 1.3E$).
2. **False-Alarm Budget Calibration**: Calibrates standardized threshold $c$ using incident-level false alarm budget $r_w(c) = \frac{86400}{w}\kappa c^2\Psi(c)$ with parameter `target_false_incidents_per_day`.
3. **Incident Merging**: Contiguous and overlapping detections across all 3 timescales are merged into a single localized incident rather than firing multiple alerts.

### 10. What happens when the model cannot trust its baseline?
PW-Scan outputs one of four explicit reliability states:
- `HIGH`: $\ge 600$s continuous coverage, $\ge 85\%$ coverage ratio, verified deduplication.
- `MEDIUM`: $\ge 180$s continuous coverage, $\ge 60\%$ coverage ratio.
- `LOW`: Emits explicit warnings when contamination $>40\%$, deduplication memory capacity is degraded, or regime asymmetry is outside $[0.4, 2.5]$.
- `ABSTAIN`: Total observation $< 60$s, coverage $< 40\%$, or total events $< 20$. When abstaining, no alerts are triggered.

### 11. How was the detector evaluated?
Evaluated using the Traffic Lab suite across 13 diverse scenario families:
- Stationary Poisson (N0), Sparse traffic (N1), Overdispersed traffic (N2).
- Isolated spikes (A1), Microbursts (A2), Sustained surges (A3), Ramps (A4), Distinct ID floods (A5).
- Duplicate storms (D1), ID-stripped repetition (L1), Level shifts (S1), Long gaps (G1), Hostile strings (X1).

### 12. Are the benchmark results independently validated?
Yes. Benchmark ground truth is generated separately and is never exposed to detectors. The evaluation was run against 4 independent reference baselines (Fixed threshold, Trailing mean+std, EWMA, and single-scale Median/MAD) using held-out evaluation seeds (`20261121` and `999`) completely separate from development seeds.

### 13. Why is there no LLM or autonomous agent?
1. **Auditable Correctness**: Statistical count models provide mathematically provable, deterministic results with zero risk of hallucination.
2. **Security & Privacy**: Telemetry logs often contain sensitive client identifiers and internal routes. Processing them 100% offline with standard Python libraries ensures zero data leakage.
3. **Human-in-the-Loop**: PulseWatch provides evidence, telemetry accounting, and actionable investigation leads, keeping human engineers in control of operational decisions.

### 14. How does the system protect data?
- 100% offline execution; zero external network requests or socket calls.
- Enforces a strict Content Security Policy (`default-src 'none'; style-src 'unsafe-inline';`) on HTML reports.
- All user-supplied strings are escaped (`html.escape()`) to prevent XSS and terminal ANSI escape-code injection.
- Memory and line lengths are strictly bounded (8KB line cap, 200,000-key LRU cache).

### 15. What are the current limitations?
- Analyzes offline logs; does not perform live streaming capture or packet sniffing.
- Deduplication relies on identity fields; logs stripped of sequence numbers or trace IDs cannot be deduplicated with mathematical certainty.
- Does not attribute malicious intent or external attacker identities (which cannot be verified from offline synthetic counts alone).

### 16. How could this evolve into a production tool?
Future roadmap includes:
- Read-only Model Context Protocol (MCP) server integration to allow human-supervised engineering agents to query canonical reports.
- Pre-aggregated parquet log support for large-scale historical analysis.
- Streaming continuous sliding-window sidecar deployment next to synthetic load test runners.
