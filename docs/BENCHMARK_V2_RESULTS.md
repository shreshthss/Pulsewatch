# PulseWatch Benchmark v2 Results

- **Scenario Fingerprint:** `046adaa4298fe120e599e0708504e03c50738dba4c2c4a4410df750843122c16`
- **Seeds Evaluated:** [41001, 41002, 41003]
- **Pooled F1 Score:** 0.5904
- **Macro-mean F1 Score:** 0.6545

## Scenario Results

| Scenario ID | Category | TP | FP | FN | Precision | Recall | F1 Score | Mean IoU |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `BORD_d10_m1p2` | borderline | 0 | 2 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `BORD_d10_m1p35` | borderline | 0 | 1 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `BORD_d10_m1p5` | borderline | 0 | 1 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `BORD_d10_m2p0` | borderline | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.7270 |
| `BORD_d10_m3p0` | borderline | 3 | 2 | 0 | 0.6000 | 1.0000 | 0.7500 | 0.1056 |
| `BORD_d300_m1p2` | borderline | 1 | 1 | 2 | 0.5000 | 0.3333 | 0.4000 | 0.0833 |
| `BORD_d300_m1p35` | borderline | 2 | 0 | 1 | 1.0000 | 0.6667 | 0.8000 | 0.9143 |
| `BORD_d300_m1p5` | borderline | 3 | 1 | 0 | 0.7500 | 1.0000 | 0.8571 | 0.6097 |
| `BORD_d300_m2p0` | borderline | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.4681 |
| `BORD_d300_m3p0` | borderline | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.3914 |
| `BORD_d60_m1p2` | borderline | 0 | 3 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `BORD_d60_m1p35` | borderline | 3 | 2 | 0 | 0.6000 | 1.0000 | 0.7500 | 0.9035 |
| `BORD_d60_m1p5` | borderline | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.7152 |
| `BORD_d60_m2p0` | borderline | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.5145 |
| `BORD_d60_m3p0` | borderline | 3 | 3 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.1152 |
| `COMP_caller_concentration` | composition_only | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `COMP_route_concentration` | composition_only | 0 | 1 | 0 | N/A | N/A | N/A | 0.0000 |
| `COMP_status_shift` | composition_only | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_dup_noise` | telemetry_negative | 0 | 1 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_dup_replay` | telemetry_negative | 0 | 3 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_linear_drift` | hard_negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_long_gap` | telemetry_negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_overdispersed_ar1` | hard_negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_overdispersed_gamma` | hard_negative | 0 | 2 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_periodic_sine` | hard_negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_scheduled_bursts` | hard_negative | 0 | 9 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_short_dropout` | telemetry_negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_sparse` | negative | 0 | 2 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_stable_high` | negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_stable_low` | negative | 0 | 6 | 0 | N/A | N/A | N/A | 0.0000 |
| `NEG_stable_mid` | negative | 0 | 0 | 0 | N/A | N/A | N/A | 0.0000 |
| `POS_concentrated_volume` | positive | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.1142 |
| `POS_dual_spikes` | positive | 2 | 2 | 4 | 0.5000 | 0.3333 | 0.4000 | 0.1293 |
| `POS_dup_burst` | positive | 3 | 1 | 0 | 0.7500 | 1.0000 | 0.8571 | 0.1158 |
| `POS_level_shift` | positive | 0 | 0 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `POS_post_gap_spike` | positive | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.1020 |
| `POS_ramp_slow` | positive | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.3358 |
| `POS_spike_short` | positive | 2 | 2 | 1 | 0.5000 | 0.6667 | 0.5714 | 0.1180 |
| `POS_surge_long` | positive | 3 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0.1687 |
