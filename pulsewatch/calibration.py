"""PulseWatch Statistical Calibration: synthetic null generators, empirical threshold calibration, and validation bounds.

This module implements empirical false-incident calibration across five synthetic
null traffic families, rigorous Poisson (Garwood) and bootstrap bounds, monotonic
threshold selection, configuration fingerprinting, and profile management.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

CALIBRATION_SCHEMA_VERSION = "pulsewatch.calibration/1"
CALIBRATION_PROTOCOL_VERSION = "1.0.0"

# Standard candidate z-threshold evaluation grid
GRID_Z = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0]

# Supported null traffic families
NULL_FAMILIES = ["poisson_low", "poisson", "nb_iid", "modulated", "gaps"]

# Durations in seconds for simulation traces
DEFAULT_DURATIONS_SEC = [900, 3600, 10800]


# ---------------------------------------------------------------------------
# Numerical Statistics: Exact Poisson (Garwood) and Clopper-Pearson Bounds
# ---------------------------------------------------------------------------

def _regularized_gamma_q(a: float, x: float) -> float:
    """Upper regularized incomplete gamma Q(a, x) = Gamma(a, x) / Gamma(a).

    For integer a, Q(a, x) = sum_{j=0}^{a-1} exp(-x) * x^j / j!
    """
    if x <= 0.0:
        return 1.0
    if a <= 0.0:
        return 0.0
    if isinstance(a, int) or (isinstance(a, float) and a.is_integer()):
        k = int(a)
        # Direct summation using log-space terms to avoid underflow
        total = 0.0
        term = math.exp(-x)
        total += term
        curr = term
        for j in range(1, k):
            curr *= x / j
            total += curr
        return min(1.0, max(0.0, total))

    # For non-integer a, use standard continued fraction or series
    if x < a + 1.0:
        # Series expansion for P(a, x) then Q = 1 - P
        ap = a
        sum_val = 1.0 / a
        del_val = sum_val
        n = 1
        while n <= 100:
            ap += 1.0
            del_val *= x / ap
            sum_val += del_val
            if abs(del_val) < abs(sum_val) * 1e-14:
                break
            n += 1
        p_val = sum_val * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return min(1.0, max(0.0, 1.0 - p_val))
    else:
        # Lentz continued fraction for Q(a, x)
        b = x + 1.0 - a
        c = 1.0 / 1e-30
        d = 1.0 / b
        h = d
        for i in range(1, 100):
            an = -i * (i - a)
            b += 2.0
            d = an * d + b
            if abs(d) < 1e-30:
                d = 1e-30
            c = b + an / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            del_val = d * c
            h *= del_val
            if abs(del_val - 1.0) < 1e-14:
                break
        return min(1.0, max(0.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h))


def poisson_upper_bound(count: int, exposure_days: float, confidence: float = 0.95) -> float:
    """Exact one-sided upper Poisson confidence bound on incident rate per day (Garwood).

    For count == 0 and alpha = 0.05 (confidence = 0.95):
        rate_ub = -ln(0.05) / exposure_days ~= 2.995732 / exposure_days
    In general:
        rate_ub = chi2_inv(2 * (count + 1), confidence) / (2 * exposure_days)
    """
    if exposure_days <= 0.0:
        return float("inf")
    alpha = 1.0 - confidence
    if count == 0:
        return -math.log(alpha) / exposure_days

    # We need lambda such that P(Poisson(lambda) <= count) = alpha
    # P(Poisson(lambda) <= count) = Q(count + 1, lambda)
    target = alpha
    # Bisection search for lambda:
    low = 0.0
    high = max(10.0, float(count) * 4.0)
    while _regularized_gamma_q(count + 1, high) > target:
        high *= 2.0

    for _ in range(60):
        mid = 0.5 * (low + high)
        val = _regularized_gamma_q(count + 1, mid)
        if val > target:
            low = mid
        else:
            high = mid
        if (high - low) < 1e-10:
            break

    lam = 0.5 * (low + high)
    return lam / exposure_days


def clopper_pearson_upper_bound(successes: int, trials: int, confidence: float = 0.95) -> float:
    """Exact one-sided Clopper-Pearson binomial upper bound P(X >= 1) <= bound.

    Parameters:
        successes: number of observed positive incidents (0 <= successes <= trials)
        trials: number of independent trace trials (trials > 0)
        confidence: confidence level in (0, 1), default 0.95

    Returns:
        Exact upper bound in [0.0, 1.0]

    Raises:
        ValueError: if parameters violate binomial constraints (trials <= 0, successes < 0,
                    successes > trials) or confidence is not in (0, 1). Invalid observations
                    are rejected explicitly and never masked.
    """
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"Confidence level must be strictly between 0 and 1, got {confidence}")
    if trials <= 0:
        raise ValueError(f"Number of trials must be a positive integer, got trials={trials}")
    if successes < 0:
        raise ValueError(f"Number of successes cannot be negative, got successes={successes}")
    if successes > trials:
        raise ValueError(f"Number of successes ({successes}) cannot exceed trials ({trials})")

    if successes == trials:
        return 1.0

    alpha = 1.0 - confidence
    if successes == 0:
        # 1 - (1 - p)^n = 1 - alpha => (1 - p)^n = alpha => p = 1 - alpha^(1/n)
        return 1.0 - math.pow(alpha, 1.0 / float(trials))

    # General beta quantile via bisection on binomial CDF
    def binom_cdf(p: float) -> float:
        # P(Bin(n, p) <= successes)
        # Using summation
        tot = 0.0
        for i in range(successes + 1):
            tot += math.comb(trials, i) * math.pow(p, i) * math.pow(1.0 - p, trials - i)
        return tot

    low = 0.0
    high = 1.0
    for _ in range(60):
        mid = 0.5 * (low + high)
        if binom_cdf(mid) > alpha:
            low = mid
        else:
            high = mid
        if (high - low) < 1e-10:
            break
    return 0.5 * (low + high)


def bootstrap_ratio_upper_bound(
    trace_incidents: Sequence[int],
    trace_exposures_days: Sequence[float],
    replicates: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> Optional[float]:
    """Non-parametric percentile bootstrap upper bound on ratio (total incidents / total exposure).

    Returns None if total incidents is 0 (as specified in §4.3).
    """
    total_incidents = sum(trace_incidents)
    if total_incidents == 0 or len(trace_incidents) == 0:
        return None

    n = len(trace_incidents)
    rng = random.Random(seed)
    ratios: List[float] = []

    for _ in range(replicates):
        samp_inc = 0
        samp_exp = 0.0
        for _ in range(n):
            idx = rng.randrange(n)
            samp_inc += trace_incidents[idx]
            samp_exp += trace_exposures_days[idx]
        if samp_exp > 0.0:
            ratios.append(samp_inc / samp_exp)
        else:
            ratios.append(0.0)

    ratios.sort()
    idx = min(len(ratios) - 1, int(math.ceil(confidence * len(ratios))) - 1)
    return ratios[idx]


def quasi_poisson_dispersion(
    trace_incidents: Sequence[int],
    trace_exposures_days: Sequence[float],
) -> Optional[float]:
    """Quasi-Poisson dispersion index across traces: Var(k) / E(k).

    Returns None if mean rate is 0 or traces <= 1.
    """
    if len(trace_incidents) <= 1 or sum(trace_incidents) == 0:
        return None
    tot_exp = sum(trace_exposures_days)
    if tot_exp <= 0.0:
        return None
    overall_rate = sum(trace_incidents) / tot_exp

    # Pearson statistic: sum (k_i - E_i)^2 / E_i, divided by (n - 1)
    pearson = 0.0
    valid_n = 0
    for k, exp in zip(trace_incidents, trace_exposures_days):
        expected = overall_rate * exp
        if expected > 0.0:
            pearson += ((k - expected) ** 2) / expected
            valid_n += 1
    if valid_n <= 1:
        return None
    return pearson / float(valid_n - 1)


# ---------------------------------------------------------------------------
# Synthetic Null Generators
# ---------------------------------------------------------------------------

def derive_trace_seed(base_seed: int, role: str, family: str, index: int) -> int:
    """Derive deterministic seed using BLAKE2b: blake2b(base_seed | role | family | index)."""
    h = hashlib.blake2b(digest_size=8)
    h.update(str(base_seed).encode("utf-8"))
    h.update(b":")
    h.update(role.encode("utf-8"))
    h.update(b":")
    h.update(family.encode("utf-8"))
    h.update(b":")
    h.update(str(index).encode("utf-8"))
    return int.from_bytes(h.digest(), byteorder="big")


def _poisson_sample(rng: random.Random, lam: float) -> int:
    """Exact Poisson draw using Knuth's algorithm or Gaussian inversion for large lambda."""
    if lam <= 0.0:
        return 0
    if lam > 30.0:
        return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def generate_null_trace_events(
    family: str,
    duration_sec: int,
    seed: int,
    start_timestamp: int = 1_700_000_000,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
    """Generate synthetic null traffic event dictionaries for the specified family.

    Returns:
        (events, injected_unknown_gaps)
    """
    rng = random.Random(seed)
    events: List[Dict[str, Any]] = []
    injected_gaps: List[Tuple[int, int]] = []

    routes = ["/api/v1/users", "/api/v1/orders", "/api/v1/products"]
    clients = ["192.168.1.10", "192.168.1.11", "192.168.1.12"]

    if family == "poisson_low":
        rate = rng.choice([0.2, 0.4, 0.6])
        for t in range(duration_sec):
            sec_ts = start_timestamp + t
            cnt = _poisson_sample(rng, rate)
            for _ in range(cnt):
                events.append({
                    "timestamp": sec_ts,
                    "method": "GET",
                    "route": rng.choice(routes),
                    "client": rng.choice(clients),
                    "status": 200,
                })

    elif family == "poisson":
        rate = float(rng.choice([1, 3, 8, 20]))
        for t in range(duration_sec):
            sec_ts = start_timestamp + t
            cnt = _poisson_sample(rng, rate)
            for _ in range(cnt):
                events.append({
                    "timestamp": sec_ts,
                    "method": "GET",
                    "route": rng.choice(routes),
                    "client": rng.choice(clients),
                    "status": 200,
                })

    elif family == "nb_iid":
        # Negative binomial (gamma-Poisson mixture) per second
        rate = float(rng.choice([1, 3, 8, 20]))
        phi = float(rng.choice([1.5, 3.0, 6.0]))
        # Var = rate * phi => sigma^2 = rate * (phi - 1)
        # Gamma shape k = rate / (phi - 1), scale theta = phi - 1
        shape = rate / (phi - 1.0)
        scale = phi - 1.0
        for t in range(duration_sec):
            sec_ts = start_timestamp + t
            # Draw intensity from Gamma(shape, scale)
            lam_t = rng.gammavariate(shape, scale)
            cnt = _poisson_sample(rng, lam_t)
            for _ in range(cnt):
                events.append({
                    "timestamp": sec_ts,
                    "method": "GET",
                    "route": rng.choice(routes),
                    "client": rng.choice(clients),
                    "status": 200,
                })

    elif family == "modulated":
        # Poisson with AR(1) log-rate modulation
        base_rate = float(rng.choice([2.0, 5.0]))
        sigma = float(rng.choice([0.15, 0.3]))
        tau = float(rng.choice([20.0, 60.0, 180.0]))
        rho = math.exp(-1.0 / tau)
        innov_std = sigma * math.sqrt(max(1e-6, 1.0 - rho * rho))
        x_t = rng.gauss(0.0, sigma)
        log_base = math.log(base_rate)

        for t in range(duration_sec):
            x_t = rho * x_t + rng.gauss(0.0, innov_std)
            lam_t = math.exp(log_base + x_t)
            sec_ts = start_timestamp + t
            cnt = _poisson_sample(rng, lam_t)
            for _ in range(cnt):
                events.append({
                    "timestamp": sec_ts,
                    "method": "GET",
                    "route": rng.choice(routes),
                    "client": rng.choice(clients),
                    "status": 200,
                })

    elif family == "gaps":
        # Poisson with 1-2 unknown gaps longer than 900 s
        base_rate = float(rng.choice([2.0, 4.0]))
        num_gaps = rng.choice([1, 2])
        gap_len = 1000  # > 900s threshold
        # Place gap at 30% and optionally 65% of trace
        gap_points: List[Tuple[int, int]] = []
        if num_gaps >= 1 and duration_sec >= 1800:
            g1_start = int(duration_sec * 0.3)
            gap_points.append((g1_start, g1_start + gap_len))
        if num_gaps >= 2 and duration_sec >= 3600:
            g2_start = int(duration_sec * 0.65)
            gap_points.append((g2_start, g2_start + gap_len))

        curr_t = 0
        while curr_t < duration_sec:
            # Check if curr_t falls in a gap
            in_gap = False
            for gs, ge in gap_points:
                if gs <= curr_t < ge:
                    in_gap = True
                    injected_gaps.append((start_timestamp + gs, start_timestamp + ge))
                    curr_t = ge
                    break
            if in_gap:
                continue

            sec_ts = start_timestamp + curr_t
            cnt = _poisson_sample(rng, base_rate)
            for _ in range(cnt):
                events.append({
                    "timestamp": sec_ts,
                    "method": "GET",
                    "route": rng.choice(routes),
                    "client": rng.choice(clients),
                    "status": 200,
                })
            curr_t += 1

    else:
        raise ValueError(f"Unknown null family: {family}. Supported: {NULL_FAMILIES}")

    return events, injected_gaps


# ---------------------------------------------------------------------------
# Calibration Profile Schema and Structures
# ---------------------------------------------------------------------------

@dataclass
class FamilyMetricResult:
    family: str
    traces_evaluated: int
    exposure_days: float
    incidents_by_z: Dict[float, int]
    poisson_ub_by_z: Dict[float, float]
    bootstrap_ub_by_z: Dict[float, Optional[float]]
    dispersion_by_z: Dict[float, Optional[float]]
    prob_incident_ub_by_z: Dict[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "traces_evaluated": self.traces_evaluated,
            "exposure_days": round(self.exposure_days, 4),
            "incidents_by_z": {str(k): v for k, v in self.incidents_by_z.items()},
            "poisson_ub_by_z": {str(k): round(v, 4) for k, v in self.poisson_ub_by_z.items()},
            "bootstrap_ub_by_z": {str(k): (round(v, 4) if v is not None else None) for k, v in self.bootstrap_ub_by_z.items()},
            "dispersion_by_z": {str(k): (round(v, 4) if v is not None else None) for k, v in self.dispersion_by_z.items()},
            "prob_incident_ub_by_z": {str(k): round(v, 6) for k, v in self.prob_incident_ub_by_z.items()},
        }


@dataclass
class CalibrationProfile:
    schema_version: str = CALIBRATION_SCHEMA_VERSION
    protocol_version: str = CALIBRATION_PROTOCOL_VERSION
    detector_fingerprint: str = ""
    base_seed: int = 0
    traces_per_family: int = 0
    target_false_incidents_per_day: float = 1.0
    selected_z_threshold: Optional[float] = None
    calibrated: bool = False
    false_alarm_claim_supported: bool = False
    rejection_reason: Optional[str] = None
    calibration_results: Dict[str, FamilyMetricResult] = field(default_factory=dict)
    holdout_results: Dict[str, FamilyMetricResult] = field(default_factory=dict)
    threshold_mapping: Dict[float, float] = field(default_factory=dict)  # target -> z

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "detector_fingerprint": self.detector_fingerprint,
            "base_seed": self.base_seed,
            "traces_per_family": self.traces_per_family,
            "target_false_incidents_per_day": self.target_false_incidents_per_day,
            "selected_z_threshold": self.selected_z_threshold,
            "calibrated": self.calibrated,
            "false_alarm_claim_supported": self.false_alarm_claim_supported,
            "rejection_reason": self.rejection_reason,
            "calibration_results": {k: v.to_dict() for k, v in self.calibration_results.items()},
            "holdout_results": {k: v.to_dict() for k, v in self.holdout_results.items()},
            "threshold_mapping": {str(k): v for k, v in self.threshold_mapping.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CalibrationProfile":
        cp = cls(
            schema_version=data.get("schema_version", CALIBRATION_SCHEMA_VERSION),
            protocol_version=data.get("protocol_version", CALIBRATION_PROTOCOL_VERSION),
            detector_fingerprint=data.get("detector_fingerprint", ""),
            base_seed=data.get("base_seed", 0),
            traces_per_family=data.get("traces_per_family", 0),
            target_false_incidents_per_day=float(data.get("target_false_incidents_per_day", 1.0)),
            selected_z_threshold=float(data["selected_z_threshold"]) if data.get("selected_z_threshold") is not None else None,
            calibrated=bool(data.get("calibrated", False)),
            false_alarm_claim_supported=bool(data.get("false_alarm_claim_supported", False)),
            rejection_reason=data.get("rejection_reason"),
        )
        if "threshold_mapping" in data:
            cp.threshold_mapping = {float(k): float(v) for k, v in data["threshold_mapping"].items()}
        return cp

    def save_json(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load_json(cls, path: str) -> "CalibrationProfile":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def load_calibration_profile(path: str) -> CalibrationProfile:
    """Load calibration profile from a JSON file."""
    return CalibrationProfile.load_json(path)

    @classmethod
    def load_json(cls, path: str) -> "CalibrationProfile":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Calibration Runner and Threshold Selection
# ---------------------------------------------------------------------------

def evaluate_threshold_grid_on_trace(
    events: List[Dict[str, Any]],
    grid_z: Sequence[float] = GRID_Z,
    detector_factory: Optional[Callable[..., Any]] = None,
) -> Tuple[float, Dict[float, int]]:
    """Scan a single trace once across all continuous segments and count incidents at each candidate z.

    Returns:
        (exposure_days, incidents_count_by_z)
    """
    from pulsewatch.detector import PWScanDetector
    from pulsewatch.incident import IncidentMerger
    from pulsewatch.temporal import TemporalEngine

    engine = TemporalEngine()
    engine.ingest_records(events)
    gaps, segments = engine.compute_gaps_and_segments()
    coverage = engine.compute_coverage()
    detector = detector_factory() if detector_factory else PWScanDetector()
    baseline = detector.fit_baseline_profile(engine, coverage, segments)

    if baseline.reliability == "abstain":
        return 0.0, {z: 0 for z in grid_z}

    exposure_sec = sum(seg.duration_sec for seg in segments)
    exposure_days = exposure_sec / 86400.0

    merger = IncidentMerger()
    incidents_by_z: Dict[float, int] = {}

    # Run scan across candidate grid
    for z in grid_z:
        candidates = detector.scan(engine, segments, baseline, z_threshold=z)
        incidents = merger.merge_candidates(candidates, engine, median_rate_1s=baseline.fitted_rate_1s)
        incidents_by_z[z] = len(incidents)

    return exposure_days, incidents_by_z


def select_calibrated_threshold(
    family_results: Dict[str, FamilyMetricResult],
    target_rate_per_day: float,
    grid_z: Sequence[float] = GRID_Z,
) -> Tuple[Optional[float], Optional[str]]:
    """Select the smallest grid z such that every family passes at that z and every larger z.

    Returns:
        (selected_z, failure_reason)
    """
    # 1. Insufficiency check: >= 30 traces per family and exposure >= -ln(0.05) / target
    min_exposure_days = -math.log(0.05) / target_rate_per_day
    for fam, res in family_results.items():
        if res.traces_evaluated < 30:
            return None, f"insufficient_data: family '{fam}' has only {res.traces_evaluated} traces (< 30)"
        if res.exposure_days < min_exposure_days:
            return None, f"insufficient_data: family '{fam}' exposure {res.exposure_days:.2f}d < required {min_exposure_days:.2f}d"

    # 2. Candidate evaluation: conservative bound <= target
    def family_passes(fam_res: FamilyMetricResult, z: float) -> bool:
        p_ub = fam_res.poisson_ub_by_z.get(z, float("inf"))
        b_ub = fam_res.bootstrap_ub_by_z.get(z)
        binding_ub = max(p_ub, b_ub) if b_ub is not None else p_ub
        return binding_ub <= target_rate_per_day

    # Check monotonicity from each candidate upward
    for i, z in enumerate(grid_z):
        # Must pass at z and all larger grid values
        passes_all_subsequent = True
        for z_sub in grid_z[i:]:
            if not all(family_passes(res, z_sub) for res in family_results.values()):
                passes_all_subsequent = False
                break
        if passes_all_subsequent:
            return z, None

    return None, f"no_threshold_met_target: grid values up to {grid_z[-1]} exceed target {target_rate_per_day}/day"
