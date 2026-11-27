"""PulseWatch Benchmark v2 Scenario Definitions: 39 parametric scenarios across 6 categories.

Categories:
- negative: stationary traffic controls across rates
- hard_negative: overdispersion, autocorrelation, periodicity, scheduled bursts, drift
- telemetry_negative: duplicate storm, noise replays, short dropout, long collector gap
- positive: spikes, surges, ramps, level shifts, multi-bursts, post-gap anomalies
- borderline: duration (10, 60, 300) x multiplier (1.2, 1.35, 1.5, 2.0, 3.0) grid
- composition_only: constant-volume route, caller, or status shifts
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from traffic_lab.generator import GroundTruth, GroundTruthIncident

SEED_SETS = {
    "calibration": list(range(31001, 31006)),
    "evaluation": list(range(41001, 41011)),
    "ablation": list(range(51001, 51011)),
    "exposure": list(range(61001, 61025)),
    "injection": list(range(71001, 71011)),
}


def assert_seed_discipline(seed: int, role: str) -> None:
    """Assert that a seed belongs strictly to its declared role's disjoint set."""
    allowed = SEED_SETS.get(role)
    if allowed is not None and seed not in allowed:
        raise ValueError(f"Seed {seed} is not in the allowed disjoint set for role '{role}' ({allowed[0]}..{allowed[-1]})")


@dataclass
class ScenarioSpec:
    scenario_id: str
    category: str
    description: str
    duration_sec: int
    base_rate_req_s: float
    params: Dict[str, Any] = field(default_factory=dict)


def get_all_scenarios_v2() -> List[ScenarioSpec]:
    """Return the canonical list of 39 benchmark v2 scenarios."""
    scenarios: List[ScenarioSpec] = [
        # 1. Negative Controls (4)
        ScenarioSpec("NEG_stable_low", "negative", "Low-rate stationary Poisson traffic (0.4 req/s)", 1800, 0.4),
        ScenarioSpec("NEG_stable_mid", "negative", "Mid-rate stationary Poisson traffic (5.0 req/s)", 1800, 5.0),
        ScenarioSpec("NEG_stable_high", "negative", "High-rate stationary Poisson traffic (40.0 req/s)", 1200, 40.0),
        ScenarioSpec("NEG_sparse", "negative", "Sparse micro-service traffic (0.1 req/s)", 1800, 0.1),

        # 2. Hard Negative Controls (5)
        ScenarioSpec("NEG_overdispersed_gamma", "hard_negative", "Negative binomial overdispersed arrivals (phi=3.0)", 1800, 5.0, {"phi": 3.0}),
        ScenarioSpec("NEG_overdispersed_ar1", "hard_negative", "AR(1) log-rate modulated Poisson traffic (tau=60s)", 1800, 5.0, {"tau": 60.0, "sigma": 0.25}),
        ScenarioSpec("NEG_periodic_sine", "hard_negative", "Periodic diurnal-like sinusoidal variation", 3600, 10.0, {"period": 600.0, "amp": 0.3}),
        ScenarioSpec("NEG_scheduled_bursts", "hard_negative", "Scheduled batch cron pulses (sub-threshold)", 1800, 4.0, {"pulse_interval": 300, "pulse_dur": 5, "pulse_rate": 8.0}),
        ScenarioSpec("NEG_linear_drift", "hard_negative", "Gradual slow volume drift (+30% over 1 hour)", 3600, 5.0, {"slope": 0.3 / 3600.0}),

        # 3. Telemetry Negative Controls (4)
        ScenarioSpec("NEG_dup_replay", "telemetry_negative", "Heavy client/proxy duplicate retransmission storm", 1800, 5.0, {"dup_fraction": 0.40}),
        ScenarioSpec("NEG_dup_noise", "telemetry_negative", "Low-level random duplicate retry noise (5%)", 1800, 5.0, {"dup_fraction": 0.05}),
        ScenarioSpec("NEG_short_dropout", "telemetry_negative", "Short telemetry collection dropout (120s < 900s threshold)", 1800, 5.0, {"gap_start": 600, "gap_len": 120}),
        ScenarioSpec("NEG_long_gap", "telemetry_negative", "Collector failure with unknown gap (1000s > 900s threshold)", 2400, 5.0, {"gap_start": 600, "gap_len": 1000}),

        # 4. Genuine Anomalies / Positive Class (8)
        ScenarioSpec("POS_spike_short", "positive", "Isolated 10s traffic spike (8x elevation)", 1800, 5.0, {"start": 600, "dur": 10, "mult": 8.0, "shape": "isolated_spike"}),
        ScenarioSpec("POS_surge_long", "positive", "Sustained 90s API surge (3.5x elevation)", 1800, 5.0, {"start": 600, "dur": 90, "mult": 3.5, "shape": "sustained_surge"}),
        ScenarioSpec("POS_ramp_slow", "positive", "Linear ramp-up and drop-off anomaly (duration 180s)", 1800, 5.0, {"start": 600, "dur": 180, "peak_mult": 4.0, "shape": "ramp"}),
        ScenarioSpec("POS_level_shift", "positive", "Permanent step-up regime shift (2.5x rate)", 1800, 4.0, {"start": 900, "dur": 900, "mult": 2.5, "shape": "level_shift_candidate"}),
        ScenarioSpec("POS_dual_spikes", "positive", "Two distinct 20s spikes separated by 300s", 1800, 5.0, {"s1": 400, "d1": 20, "m1": 6.0, "s2": 800, "d2": 20, "m2": 6.0}),
        ScenarioSpec("POS_dup_burst", "positive", "Genuine volume burst combined with duplicate retransmission", 1800, 5.0, {"start": 600, "dur": 60, "mult": 4.0, "dup_pct": 0.35}),
        ScenarioSpec("POS_post_gap_spike", "positive", "Sharp recovery surge immediately following a 950s gap", 2400, 5.0, {"gap_s": 500, "gap_len": 950, "spike_dur": 30, "mult": 7.0}),
        ScenarioSpec("POS_concentrated_volume", "positive", "Volume flood concentrated 95% on single API endpoint", 1800, 5.0, {"start": 600, "dur": 60, "mult": 5.0, "target_route": "/api/v1/checkout"}),
    ]

    # 5. Borderline Sensitivity Grid (15)
    # Durations: 10s, 60s, 300s x Multipliers: 1.2, 1.35, 1.5, 2.0, 3.0
    for dur in [10, 60, 300]:
        for mult in [1.2, 1.35, 1.5, 2.0, 3.0]:
            sid = f"BORD_d{dur}_m{str(mult).replace('.', 'p')}"
            scenarios.append(
                ScenarioSpec(
                    sid,
                    "borderline",
                    f"Borderline sensitivity probe: duration={dur}s, elevation={mult}x",
                    1800,
                    5.0,
                    {"start": 600, "dur": dur, "mult": mult, "shape": "borderline_surge"},
                )
            )

    # 6. Composition-Only Shifts (3)
    scenarios.extend([
        ScenarioSpec("COMP_route_concentration", "composition_only", "Constant volume with route concentration shift (90% to /login)", 1800, 8.0, {"start": 600, "dur": 300, "target_route": "/api/v1/login"}),
        ScenarioSpec("COMP_caller_concentration", "composition_only", "Constant volume with single client flood (80% from one IP)", 1800, 8.0, {"start": 600, "dur": 300, "target_client": "10.0.0.99"}),
        ScenarioSpec("COMP_status_shift", "composition_only", "Constant volume with 40% HTTP 500 error elevation", 1800, 8.0, {"start": 600, "dur": 300, "error_pct": 0.40}),
    ])

    return scenarios


def fingerprint_scenarios_v2() -> str:
    """Compute SHA-256 digest of the canonical scenario table."""
    specs = get_all_scenarios_v2()
    data = [asdict(s) for s in specs]
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def generate_scenario_v2_trace(
    spec: ScenarioSpec,
    seed: int,
    start_ts: int = 1_700_000_000,
) -> Tuple[List[Dict[str, Any]], GroundTruth]:
    """Generate events and ground-truth metadata for benchmark v2 scenario."""
    rng_seed = f"{seed}:{spec.scenario_id}"
    h = hashlib.blake2b(rng_seed.encode("utf-8"), digest_size=8)
    int_seed = int.from_bytes(h.digest(), byteorder="big")
    rng = random.Random(int_seed)

    routes = ["/api/v1/users", "/api/v1/orders", "/api/v1/items", "/api/v1/search"]
    clients = [f"192.168.1.{i}" for i in range(10, 25)]

    events: List[Dict[str, Any]] = []
    gt_incidents: List[GroundTruthIncident] = []
    expected_duplicates = 0
    total_dur = spec.duration_sec
    base_rate = spec.base_rate_req_s

    # Generate baseline per-second arrivals
    for t in range(total_dur):
        sec_ts = start_ts + t
        rate_t = base_rate

        # 1. Hard negative adjustments
        if spec.category == "hard_negative":
            if "phi" in spec.params:
                phi = spec.params["phi"]
                shape = max(0.1, base_rate / (phi - 1.0))
                scale = max(0.1, phi - 1.0)
                rate_t = rng.gammavariate(shape, scale)
            elif "period" in spec.params:
                phase = (2.0 * math.pi * t) / spec.params["period"]
                rate_t = base_rate * (1.0 + spec.params["amp"] * math.sin(phase))
            elif "pulse_interval" in spec.params:
                if (t % spec.params["pulse_interval"]) < spec.params["pulse_dur"]:
                    rate_t = spec.params["pulse_rate"]
            elif "slope" in spec.params:
                rate_t = base_rate * (1.0 + spec.params["slope"] * t)

        # 2. Telemetry negative adjustments
        if spec.category == "telemetry_negative":
            if "gap_start" in spec.params:
                gs = spec.params["gap_start"]
                gl = spec.params["gap_len"]
                if gs <= t < gs + gl:
                    continue

        # 3. Positive and borderline volume adjustments
        is_in_anomaly = False
        shape_name = spec.params.get("shape", "surge")

        if spec.category in ("positive", "borderline"):
            if "start" in spec.params and "dur" in spec.params:
                s_start = spec.params["start"]
                s_dur = spec.params["dur"]
                if s_start <= t < s_start + s_dur:
                    is_in_anomaly = True
                    if "mult" in spec.params:
                        rate_t = base_rate * spec.params["mult"]
                    elif "peak_mult" in spec.params:
                        # Ramp up then down
                        half = s_dur / 2.0
                        rel = (t - s_start) if (t - s_start) < half else (s_dur - (t - s_start))
                        frac = rel / max(1.0, half)
                        rate_t = base_rate * (1.0 + (spec.params["peak_mult"] - 1.0) * frac)
            elif "s1" in spec.params:
                # Dual spikes
                if spec.params["s1"] <= t < spec.params["s1"] + spec.params["d1"]:
                    is_in_anomaly = True
                    rate_t = base_rate * spec.params["m1"]
                elif spec.params["s2"] <= t < spec.params["s2"] + spec.params["d2"]:
                    is_in_anomaly = True
                    rate_t = base_rate * spec.params["m2"]
            elif "post_gap_spike" in spec.scenario_id:
                gap_end = spec.params["gap_s"] + spec.params["gap_len"]
                if spec.params["gap_s"] <= t < gap_end:
                    continue
                if gap_end <= t < gap_end + spec.params["spike_dur"]:
                    is_in_anomaly = True
                    rate_t = base_rate * spec.params["mult"]

        # Poisson draws for rate_t
        if rate_t > 30.0:
            cnt = max(0, int(rng.gauss(rate_t, math.sqrt(rate_t)) + 0.5))
        else:
            cnt = 0
            p = 1.0
            L = math.exp(-max(0.0, rate_t))
            while True:
                cnt += 1
                p *= rng.random()
                if p <= L:
                    cnt -= 1
                    break

        # Generate event records for second t
        for _ in range(cnt):
            route = rng.choice(routes)
            client = rng.choice(clients)
            status = 200

            # Composition-only perturbations
            if spec.category == "composition_only":
                if spec.params["start"] <= t < spec.params["start"] + spec.params["dur"]:
                    if "target_route" in spec.params and rng.random() < 0.90:
                        route = spec.params["target_route"]
                    if "target_client" in spec.params and rng.random() < 0.80:
                        client = spec.params["target_client"]
                    if "error_pct" in spec.params and rng.random() < spec.params["error_pct"]:
                        status = 500

            # Concentrated volume
            if is_in_anomaly and "target_route" in spec.params:
                if rng.random() < 0.95:
                    route = spec.params["target_route"]

            ev = {
                "timestamp": sec_ts,
                "method": "GET",
                "route": route,
                "client": client,
                "status": status,
                "source": "api-gateway",
                "request_id": f"req-{t}-{rng.randint(1000, 9999999)}",
            }
            events.append(ev)

            # Injected duplicate replays
            dup_frac = spec.params.get("dup_fraction", 0.0)
            if spec.params.get("dup_pct") and is_in_anomaly:
                dup_frac = spec.params["dup_pct"]
            if dup_frac > 0.0 and rng.random() < dup_frac:
                expected_duplicates += 1
                # Append identical copy with delay
                dup_delay = rng.randint(0, 3)
                dup_ev = dict(ev)
                dup_ev["timestamp"] = sec_ts + dup_delay
                events.append(dup_ev)

    # Build Ground Truth Incidents
    if spec.category == "positive":
        if "start" in spec.params and "dur" in spec.params:
            gt_incidents.append(
                GroundTruthIncident(
                    start_sec=start_ts + spec.params["start"],
                    end_sec=start_ts + spec.params["start"] + spec.params["dur"],
                    shape=spec.params.get("shape", "surge"),
                    description=spec.description,
                )
            )
        elif "s1" in spec.params:
            gt_incidents.append(
                GroundTruthIncident(
                    start_sec=start_ts + spec.params["s1"],
                    end_sec=start_ts + spec.params["s1"] + spec.params["d1"],
                    shape="spike",
                    description="First spike",
                )
            )
            gt_incidents.append(
                GroundTruthIncident(
                    start_sec=start_ts + spec.params["s2"],
                    end_sec=start_ts + spec.params["s2"] + spec.params["d2"],
                    shape="spike",
                    description="Second spike",
                )
            )
        elif "post_gap_spike" in spec.scenario_id:
            gap_end = spec.params["gap_s"] + spec.params["gap_len"]
            gt_incidents.append(
                GroundTruthIncident(
                    start_sec=start_ts + gap_end,
                    end_sec=start_ts + gap_end + spec.params["spike_dur"],
                    shape="spike",
                    description="Post-gap recovery surge",
                )
            )
    elif spec.category == "borderline":
        gt_incidents.append(
            GroundTruthIncident(
                start_sec=start_ts + spec.params["start"],
                end_sec=start_ts + spec.params["start"] + spec.params["dur"],
                shape=spec.params.get("shape", "borderline_surge"),
                description=spec.description,
            )
        )

    gt = GroundTruth(
        scenario_name=spec.scenario_id,
        total_records=len(events),
        total_span_sec=total_dur,
        expected_duplicates=expected_duplicates,
        expected_unverified=0,
        expected_gaps=1 if ("gap_start" in spec.params or "gap_s" in spec.params) else 0,
        incidents=gt_incidents,
    )

    return events, gt
