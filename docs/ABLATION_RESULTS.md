# PulseWatch Ablation Results

This document presents empirical ablation experiments evaluating the individual contributions of PulseWatch's architectural components across controlled synthetic scenarios from the ablation seed set (`51001`..`51005`).

Schema: `pulsewatch.ablation/1`  
Artifact: `data/ablation_results.json`

---

## A1: Deduplication (Hierarchical Fingerprinting)

**Hypothesis:** Filtering duplicate replays at the ingestion boundary prevents false incident alerts on proxy/client retransmissions while preserving genuine anomalies.

| Scenario ID | With Dedup (Control) | Without Dedup (Ablated) | $\Delta$ FP |
|---|---:|---:|---:|
| `NEG_dup_replay` | 0 FP (0.00/d) | 1 FP (2.88/d) | -1 |
| `NEG_dup_noise` | 0 FP (0.00/d) | 0 FP (0.00/d) | 0 |
| `POS_dup_burst` | 5 TP, F1=1.0000 | 5 TP, F1=1.0000 | 0 |
| `NEG_stable_mid` | 0 FP (0.00/d) | 0 FP (0.00/d) | 0 |
| `POS_spike_short` | 5 TP, F1=1.0000 | 5 TP, F1=1.0000 | 0 |

**Finding:** Deduplication effectively suppresses false spikes from proxy retry storms with zero loss of detection sensitivity on genuine anomalies.

---

## A2: Coverage-Aware Gap Isolation

**Hypothesis:** Isolating collector outages ($>900$ s) into explicit segment boundaries prevents the detector from averaging over unobserved intervals or falsely alarming across data discontinuities.

| Scenario ID | Coverage-Aware (Control) | Blind to Gaps (Ablated) | Finding |
|---|---|---|---|
| `NEG_long_gap` | 0 FP (Abstained) | 1 FP (False Alert across gap) | Gap boundary isolates missing telemetry |
| `NEG_short_dropout` | 0 FP (Assumed Zero) | 0 FP | Short dropouts absorb into segment |
| `POS_post_gap_spike` | 5 TP, IoU=0.82 | 0 TP (Baseline inflated) | Gap isolation enables recovery surge detection |
| `NEG_stable_mid` | 0 FP | 0 FP | Invariant on continuous traffic |

**Finding:** Coverage gap isolation prevents artificial edge alerts across telemetry discontinuities and protects baseline fitting from outage distortion.

---

## A3: Dispersion Scaling (Negative Binomial Profile)

**Hypothesis:** Fitting negative-binomial dispersion ($\phi > 1$) scales the detection variance to accommodate natural burstiness without sacrificing sensitivity.

| Scenario ID | Dispersion Scaling ($\phi \ge 1$) | Poisson-Only ($\phi = 1$) | Finding |
|---|---|---|---|
| `NEG_overdispersed_gamma` | 0 FP | 4 FP (False Alerts) | Overdispersion absorbed into $\phi$ |
| `NEG_overdispersed_ar1` | 0 FP | 3 FP (False Alerts) | Autocorrelation absorbed into $\phi$ |
| `NEG_stable_mid` | 0 FP | 0 FP | Stationary Poisson has $\phi \approx 1$ |
| `POS_spike_short` | 5 TP, F1=1.0000 | 5 TP, F1=1.0000 | Sensitivity maintained |

**Finding:** Forcing a pure Poisson variance assumption on overdispersed traffic causes severe false-alarm inflation. Dynamic dispersion scaling controls false alarms while maintaining detection power.

---

## A4: Sticky Contamination Masking

**Hypothesis:** Iterative screening and masking of potential surge windows prevents anomalies from inflating the fitted baseline rate.

| Scenario ID | With Masking (Control) | Without Masking (Ablated) | $\Delta$ F1 |
|---|---:|---:|---:|
| `POS_surge_long` | 5 TP, F1=1.0000 | 0 TP (Swallowed by baseline) | +1.0000 |
| `POS_level_shift` | 5 TP, F1=1.0000 | 0 TP (Adopts shifted level) | +1.0000 |
| `NEG_stable_mid` | 0 FP | 0 FP | 0.0000 |

**Finding:** Without masking, sustained surges and regime shifts inflate the baseline rate, causing the detector to miss the anomaly. Sticky masking preserves baseline integrity.

---

## A5: Multi-Scale Scanning ($10$ s, $60$ s, $300$ s)

**Hypothesis:** Multi-scale sliding window aggregation enables localized detection of micro-bursts ($10$ s) and broad surges ($300$ s) that single-scale scans miss.

| Scenario ID | Multi-Scale ($[10, 60, 300]$ s) | Single-Scale ($60$ s only) |
|---|---|---|
| `POS_spike_short` (10s) | 5 TP, Mean IoU=0.88 | 5 TP, Mean IoU=0.17 |
| `POS_surge_long` (90s) | 5 TP, Mean IoU=0.74 | 5 TP, Mean IoU=0.62 |
| `NEG_stable_mid` | 0 FP | 0 FP |

**Finding:** While a fixed 60s scan detects high-volume short spikes, its temporal localization is poor (IoU 0.17). Multi-scale scanning provides superior boundary localization across short and long anomaly durations.
