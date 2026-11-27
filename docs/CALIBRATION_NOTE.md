# PulseWatch — Statistical Calibration Note

This note documents the empirical false-alarm calibration methodology, the five synthetic null traffic families, the rate-stability diagnostic, and the practical tradeoffs of threshold selection in PulseWatch.

---

## 1. Background and Motivation

In earlier iterations of PulseWatch, `target_false_incidents_per_day` was stored on the detector class but not evaluated during candidate scanning; instead, a hardcoded screening threshold `z = 3.5` was applied.

On stationary and autocorrelated anomaly-free traffic, `z = 3.5` produces between 13 and 70 false incident alerts per day depending on overdispersion and traffic dynamics. To enforce defensible false-incident limits, PulseWatch provides an empirical calibration framework that evaluates candidate thresholds against whole null traces and verifies them on independent holdout traces.

---

## 2. Synthetic Null Traffic Families

Calibration evaluates the entire incident-producing pipeline:
$$\text{TemporalEngine} \longrightarrow \text{fit\_baseline\_profile} \longrightarrow \text{PWScanDetector.scan} \longrightarrow \text{IncidentMerger.merge\_candidates}$$

Traces are simulated under five distinct null families:

| Family | Generation Model | Parameter Space |
|---|---|---|
| `poisson_low` | Homogeneous Poisson | $\lambda \in \{0.2, 0.4, 0.6\}$ req/s |
| `poisson` | Homogeneous Poisson | $\lambda \in \{1.0, 3.0, 8.0, 20.0\}$ req/s |
| `nb_iid` | Negative Binomial (Gamma-mixed Poisson) | $\phi \in \{1.5, 3.0, 6.0\}$, i.i.d. per second |
| `modulated` | AR(1) log-rate modulated Poisson | $\sigma \in \{0.15, 0.30\}, \tau \in \{20, 60, 180\}$ s |
| `gaps` | Poisson with 1–2 unknown collector gaps | Gap length $> 900$ s |

Durations: 900 s, 3,600 s, and 10,800 s.

---

## 3. Statistical Bounds and Selection Rule

For each family and candidate $z \in \{3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0\}$, two 95% one-sided upper bounds on incidents per day are evaluated:

1. **Exact Poisson (Garwood) Bound:**
   $$\text{Rate}_{\text{UB}} = \frac{\chi^2_{2(k+1), 0.95}}{2 \cdot \text{Exposure (days)}}$$
   Valid when incidents arrive as an unclustered Poisson process.

2. **Percentile Bootstrap of Ratio Estimator:**
   Resampling 2,000 whole traces with replacement to construct a non-parametric 95% upper bound. When trace dispersion exceeds 1.0 (clustering), the bootstrap bound dominates.

**Selection Rule:**
The selected threshold is the smallest $z$ on the grid such that every family passes the target at $z$ and at all subsequent grid values.

**Holdout Verification:**
Holdout traces are generated using disjoint seeds (`blake2b(seed | "holdout" | family | index)`). A threshold claim is supported only when the selected threshold passes the independent holdout set.

---

## 4. Key Findings and Sensitivity Tradeoff

For a target of **1.0 false incidents per day**:
- The binding family is `nb_iid` (dispersion 2.43).
- The holdout-verified threshold is **$z = 8.0$**.
- At $z = 8.0$, false alarms on stationary null traffic are effectively eliminated, but sensitivity to gradual ramps and low-elevation level shifts is diminished.
- Users requiring sensitivity to low-magnitude elevations should select higher false-incident allowances (e.g. 4.0/day $\rightarrow z = 6.0$).

---

## 5. Rate-Stability Assessment

When traffic exhibits a persistent regime shift covering $>50\%$ of an observation window, baseline fitting risks adopting the shifted rate as the "new normal."

To flag this condition, PulseWatch evaluates the rate-homogeneity score statistic:
$$Q = \sum_{k=0}^3 \frac{(N_k - \bar{r} D_k)^2}{\phi \bar{r} D_k}$$
evaluated across 4 equal parts of the uncontaminated timeline.

- If $Q \ge 20.0$, the baseline is marked `heterogeneous` and baseline reliability is downgraded to `low` with an investigator warning.
