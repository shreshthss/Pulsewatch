"""Complete PW-Scan statistical detector: adaptive baseline, sticky masking, dispersion, and tail evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from pulsewatch.dedup import DedupAccounting
from pulsewatch.temporal import ContinuousSegment, CoverageReport, TemporalEngine

SCALES_SEC = [10, 60, 300]
BASELINE_BLOCK_LENGTHS = [60, 300, 900, 3600]

FIXED_Z_THRESHOLD: float = 3.5
ALGORITHM_REVISION: str = "pwscan-v1.2"


def compute_detector_config_fingerprint(
    revision: str = ALGORITHM_REVISION,
    screening_z_threshold: float = 4.0,
    min_elevation_ratio: float = 1.3,
    min_excess_counts: Optional[Dict[int, int]] = None,
    scales: Optional[List[int]] = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint for detector configuration."""
    cfg = {
        "revision": revision,
        "screening_z_threshold": round(screening_z_threshold, 4),
        "min_elevation_ratio": round(min_elevation_ratio, 4),
        "min_excess_counts": {str(k): v for k, v in sorted((min_excess_counts or {10: 4, 60: 12, 300: 25}).items())},
        "scales": list(scales or SCALES_SEC),
    }
    raw = json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def assess_rate_stability(
    engine: TemporalEngine,
    segments: List[ContinuousSegment],
    excluded_seconds: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """Assess rate homogeneity across 4 equal consecutive parts of the observation timeline.

    Computes score statistic Q = sum_{k=0}^3 (N_k - r_bar * D_k)^2 / (phi * r_bar * D_k),
    approx chi2(3) under stationary Poisson traffic.
    Excludes seconds identified as masked/contaminated.
    """
    excluded = set(excluded_seconds or [])
    timeline_counts: List[int] = []
    for seg in segments:
        for sec in range(seg.start_sec, seg.end_sec):
            if sec not in excluded:
                cnt = engine.sparse_buckets[sec].total_requests if sec in engine.sparse_buckets else 0
                timeline_counts.append(cnt)

    T = len(timeline_counts)
    total_events = sum(timeline_counts)

    if T < 720:
        return {
            "label": "not_assessed",
            "statistic": None,
            "df": 3,
            "phi": None,
            "part_rates": [],
            "critical_value": 20.0,
            "reason": "insufficient_duration",
        }

    if total_events == 0:
        return {
            "label": "not_assessed",
            "statistic": None,
            "df": 3,
            "phi": None,
            "part_rates": [0.0, 0.0, 0.0, 0.0],
            "critical_value": 20.0,
            "reason": "no_traffic",
        }

    part_len = T // 4
    parts = [timeline_counts[k * part_len : (k + 1) * part_len] for k in range(4)]
    part_counts = [sum(p) for p in parts]
    part_rates = [round(c / float(part_len), 4) for c in part_counts]
    overall_rate = float(sum(part_counts)) / float(4 * part_len)

    if overall_rate <= 0.0:
        return {
            "label": "not_assessed",
            "statistic": None,
            "df": 3,
            "phi": None,
            "part_rates": part_rates,
            "critical_value": 20.0,
            "reason": "no_traffic",
        }

    # Estimate pooled within-part overdispersion from 60s blocks
    block_size = 60
    pooled_sq_diff = 0.0
    total_blocks = 0
    pooled_mean_sum = 0.0

    for p in parts:
        num_blocks = len(p) // block_size
        if num_blocks >= 2:
            blocks = [sum(p[b * block_size : (b + 1) * block_size]) for b in range(num_blocks)]
            b_mean = sum(blocks) / float(num_blocks)
            if b_mean > 0:
                pooled_sq_diff += sum((x - b_mean) ** 2 for x in blocks)
                total_blocks += num_blocks
                pooled_mean_sum += sum(blocks)

    if total_blocks > 4 and pooled_mean_sum > 0:
        pooled_mean = pooled_mean_sum / float(total_blocks)
        pooled_var = pooled_sq_diff / float(total_blocks - 4)
        phi = max(1.0, pooled_var / max(0.1, pooled_mean))
    else:
        phi = 1.0

    expected_per_part = overall_rate * float(part_len)
    variance_per_part = max(0.1, phi * expected_per_part)

    Q = sum(((N_k - expected_per_part) ** 2) / variance_per_part for N_k in part_counts)

    if Q >= 20.0:
        label = "heterogeneous"
        reason = f"score_statistic_{round(Q, 2)}_exceeds_critical_20"
    else:
        label = "stable"
        reason = "score_statistic_below_critical_20"

    return {
        "label": label,
        "statistic": round(Q, 3),
        "df": 3,
        "phi": round(phi, 3),
        "part_rates": part_rates,
        "critical_value": 20.0,
        "reason": reason,
    }



@dataclass
class MaskedInterval:
    start_sec: int
    end_sec: int
    scale_sec: int
    z_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "scale_sec": self.scale_sec,
            "z_score": round(self.z_score, 3),
        }


@dataclass
class DispersionProfile:
    scale_sec: int
    phi: float  # Var(S_w) / E(S_w) >= 1.0
    is_poisson: bool
    nb_r: Optional[float] = None  # Negative-binomial size r = E / (phi - 1)
    nb_q: Optional[float] = None  # Negative-binomial success prob q = E / (r + E)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scale_sec": self.scale_sec,
            "phi": round(self.phi, 4),
            "is_poisson": self.is_poisson,
            "nb_r": round(self.nb_r, 4) if self.nb_r is not None else None,
            "nb_q": round(self.nb_q, 4) if self.nb_q is not None else None,
        }


class BaselineProfile:
    def __init__(
        self,
        fitted_rate_1s: float = 0.0,
        initial_block_length_sec: Any = 60,
        refitting_iterations: Any = 0,
        fraction_time_masked: Any = 0.0,
        fraction_events_masked: Any = 0.0,
        reliability: str = "medium",
        abstain_reason: Optional[str] = None,
        warning: Optional[str] = None,
        asymmetry_ratio: Optional[float] = None,
        masked_intervals: Optional[List[MaskedInterval]] = None,
        dispersion_by_scale: Optional[Dict[int, DispersionProfile]] = None,
        median_rate_1s: Optional[float] = None,
        mad_1s: Optional[float] = None,
        dispersion_1s: Optional[float] = None,
        rate_stability: Optional[Dict[str, Any]] = None,
    ):
        if isinstance(fraction_time_masked, str):
            # Legacy positional call: (rate, mad, disp, reliability, abstain_reason, warning)
            self.fitted_rate_1s = float(fitted_rate_1s)
            self.reliability = fraction_time_masked
            self.abstain_reason = str(fraction_events_masked) if isinstance(fraction_events_masked, str) else abstain_reason
            self.warning = warning
            self.initial_block_length_sec = 60
            self.refitting_iterations = 0
            self.fraction_time_masked = 0.0
            self.fraction_events_masked = 0.0
            self.asymmetry_ratio = 1.0
            self.masked_intervals = []
            self.dispersion_by_scale = {}
            self.rate_stability = rate_stability
        else:
            self.fitted_rate_1s = float(median_rate_1s) if median_rate_1s is not None else float(fitted_rate_1s)
            self.initial_block_length_sec = int(initial_block_length_sec)
            self.refitting_iterations = int(refitting_iterations)
            self.fraction_time_masked = float(fraction_time_masked)
            self.fraction_events_masked = float(fraction_events_masked) if isinstance(fraction_events_masked, (int, float)) else 0.0
            self.reliability = str(reliability)
            self.abstain_reason = abstain_reason
            self.warning = warning
            self.asymmetry_ratio = asymmetry_ratio
            self.masked_intervals = list(masked_intervals or [])
            self.dispersion_by_scale = dict(dispersion_by_scale or {})
            self.rate_stability = rate_stability

    @property
    def median_rate_1s(self) -> float:
        return self.fitted_rate_1s

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "fitted_rate_1s": round(self.fitted_rate_1s, 4),
            "median_rate_1s": round(self.fitted_rate_1s, 4),
            "initial_block_length_sec": self.initial_block_length_sec,
            "refitting_iterations": self.refitting_iterations,
            "fraction_time_masked": round(self.fraction_time_masked, 4),
            "fraction_events_masked": round(self.fraction_events_masked, 4),
            "reliability": self.reliability,
            "abstain_reason": self.abstain_reason,
            "warning": self.warning,
            "asymmetry_ratio": round(self.asymmetry_ratio, 3) if self.asymmetry_ratio is not None else None,
            "masked_intervals_count": len(self.masked_intervals),
            "dispersion_by_scale": {str(k): v.to_dict() for k, v in self.dispersion_by_scale.items()},
        }
        if self.rate_stability is not None:
            d["rate_stability"] = self.rate_stability
        return d


@dataclass
class DetectionCandidate:
    start_sec: int
    end_sec: int  # exclusive: [start_sec, end_sec)
    scale_sec: int
    observed_count: int
    baseline_count: float
    excess_count: int
    z_score: float
    elevation_ratio: float
    log_p_value: float = 0.0
    p_value: float = 1.0
    tail_exact: bool = True
    omitted_mass_bound: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "scale_sec": self.scale_sec,
            "observed_count": self.observed_count,
            "baseline_count": round(self.baseline_count, 2),
            "excess_count": self.excess_count,
            "z_score": round(self.z_score, 3),
            "elevation_ratio": round(self.elevation_ratio, 3),
            "log_p_value": round(self.log_p_value, 4),
            "p_value": round(self.p_value, 8),
            "tail_exact": self.tail_exact,
            "omitted_mass_bound": round(self.omitted_mass_bound, 8),
        }


def log_poisson_pmf(k: int, lamb: float) -> float:
    """Compute ln(e^(-lamb) * lamb^k / k!) stably."""
    if lamb <= 0.0:
        return 0.0 if k == 0 else -1e30
    if k < 0:
        return -1e30
    return -lamb + k * math.log(lamb) - math.lgamma(k + 1)


def log_nb_pmf(k: int, r: float, q: float) -> float:
    """Compute ln(Gamma(k+r)/(Gamma(r)*k!) * (1-q)^r * q^k) stably."""
    if k < 0 or r <= 0.0 or q <= 0.0 or q >= 1.0:
        return -1e30
    log_p = math.log1p(-q)
    log_q = math.log(q)
    return math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * log_p + k * log_q


def compute_upper_tail_prob(
    observed_k: int,
    expected_e: float,
    disp: DispersionProfile,
    max_terms: int = 1500
) -> Tuple[float, float, bool, float]:
    """Compute upper tail P(X >= observed_k) with numerical error bound.

    Returns: (log_p_val, p_val, is_exact, omitted_mass_bound)
    """
    if observed_k <= expected_e:
        return (0.0, 1.0, True, 0.0)

    # If expected_e is 0, any positive count is immediate extreme anomaly
    if expected_e <= 0.0:
        return (-100.0, 1e-43, True, 0.0)

    # Use Poisson or Negative-Binomial based on dispersion profile
    is_poisson = disp.is_poisson or (disp.nb_r is None) or (disp.nb_q is None)

    # Sum pmf from observed_k upwards in log-space / stable accumulator
    tail_sum = 0.0
    omitted_bound = 0.0
    is_exact = True

    # Pre-test first term
    if is_poisson:
        first_log = log_poisson_pmf(observed_k, expected_e)
    else:
        first_log = log_nb_pmf(observed_k, disp.nb_r, disp.nb_q)  # type: ignore

    if first_log < -700.0:
        # Extremely small tail, beyond IEEE double precision
        return (first_log, 0.0, True, 0.0)

    # Compute terms
    curr_log = first_log
    term_val = math.exp(curr_log)
    tail_sum = term_val
    last_term = term_val

    for j in range(observed_k + 1, observed_k + max_terms):
        if is_poisson:
            # Poisson step: term(j) = term(j-1) * (expected_e / j)
            ratio = expected_e / float(j)
        else:
            # NB step: term(j) = term(j-1) * ((j - 1 + r) / j) * q
            r = disp.nb_r  # type: ignore
            q = disp.nb_q  # type: ignore
            ratio = ((j - 1.0 + r) / float(j)) * q

        last_term = last_term * ratio
        tail_sum += last_term

        # Check geometric convergence once ratio < 0.95 and term is small
        if ratio < 0.95 and last_term < 1e-9:
            omitted_bound = (last_term * ratio) / (1.0 - ratio)
            break
    else:
        # Reached max_terms without breaking
        is_exact = False
        ratio = max(0.01, min(0.99, last_term / max(1e-30, tail_sum)))
        omitted_bound = (last_term * ratio) / max(1e-6, (1.0 - ratio))

    p_val = max(0.0, min(1.0, tail_sum))
    log_p = math.log(max(1e-300, p_val)) if p_val > 0.0 else first_log
    return (log_p, p_val, is_exact, omitted_bound)


class PWScanDetector:
    """Definitive PW-Scan statistical detector: adaptive baseline, sticky masking, dispersion, and multi-scale scanning."""

    def __init__(
        self,
        target_false_incidents_per_day: Optional[float] = None,
        screening_z_threshold: float = 4.0,
        min_elevation_ratio: float = 1.3,
        min_excess_counts: Optional[Dict[int, int]] = None,
        z_threshold: Optional[float] = None,
        calibration: Optional[Any] = None,
    ):
        self.target_false_incidents_per_day = target_false_incidents_per_day
        self.screening_z_threshold = screening_z_threshold
        self.min_elevation_ratio = min_elevation_ratio
        self.min_excess_counts = min_excess_counts or {10: 4, 60: 12, 300: 25}
        self.calibration = calibration

        # Resolve active threshold and calibration state
        self.active_z_threshold: float = FIXED_Z_THRESHOLD
        self.is_calibrated: bool = False
        self.calibration_status_message: str = "uncalibrated_fixed_default"

        if z_threshold is not None:
            self.active_z_threshold = float(z_threshold)
            self.calibration_status_message = f"manual_override_z_{self.active_z_threshold}"
        elif calibration is not None:
            current_fp = self.config_fingerprint()
            calib_fp = getattr(calibration, "detector_fingerprint", "")
            if calib_fp and calib_fp != current_fp:
                warnings.warn(
                    f"Calibration profile fingerprint mismatch: profile={calib_fp} != detector={current_fp}. "
                    "Refusing incompatible calibration profile and falling back to fixed threshold.",
                    UserWarning,
                    stacklevel=2,
                )
                self.calibration_status_message = "fingerprint_mismatch_fallback_to_fixed"
            elif getattr(calibration, "rejection_reason", None):
                self.calibration_status_message = f"calibration_rejected: {calibration.rejection_reason}"
            else:
                target = target_false_incidents_per_day if target_false_incidents_per_day is not None else 1.0
                selected_z = None
                if hasattr(calibration, "threshold_mapping") and target in calibration.threshold_mapping:
                    selected_z = calibration.threshold_mapping[target]
                elif getattr(calibration, "selected_z_threshold", None) is not None:
                    selected_z = calibration.selected_z_threshold

                if selected_z is not None:
                    self.active_z_threshold = float(selected_z)
                    self.is_calibrated = bool(getattr(calibration, "calibrated", True))
                    claim = bool(getattr(calibration, "false_alarm_claim_supported", False))
                    self.calibration_status_message = (
                        f"calibrated_z_{self.active_z_threshold}_for_target_{target}/day"
                        + ("_holdout_verified" if claim else "_unverified")
                    )
                else:
                    self.calibration_status_message = "no_threshold_in_calibration"
        elif target_false_incidents_per_day is not None:
            warnings.warn(
                f"target_false_incidents_per_day={target_false_incidents_per_day} specified without an applicable "
                f"CalibrationProfile. Falling back to uncalibrated fixed threshold {FIXED_Z_THRESHOLD}.",
                UserWarning,
                stacklevel=2,
            )
            self.calibration_status_message = "uncalibrated_target_ignored_fallback_to_fixed"

    def config_fingerprint(self) -> str:
        return compute_detector_config_fingerprint(
            revision=ALGORITHM_REVISION,
            screening_z_threshold=self.screening_z_threshold,
            min_elevation_ratio=self.min_elevation_ratio,
            min_excess_counts=self.min_excess_counts,
            scales=SCALES_SEC,
        )

    def calibration_info(self) -> Dict[str, Any]:
        return {
            "active_z_threshold": self.active_z_threshold,
            "target_false_incidents_per_day": self.target_false_incidents_per_day,
            "calibrated": self.is_calibrated,
            "calibration_fingerprint": getattr(self.calibration, "detector_fingerprint", None) if self.calibration else None,
            "false_alarm_claim_supported": bool(getattr(self.calibration, "false_alarm_claim_supported", False)) if self.calibration else False,
            "status": self.calibration_status_message,
        }

    def select_initial_block_length(
        self,
        engine: TemporalEngine,
        segments: List[ContinuousSegment]
    ) -> int:
        """Select smallest initial block length from {60, 300, 900, 3600} where expected count >= 20."""
        total_obs = sum(b.total_requests for b in engine.sparse_buckets.values())
        total_sec = sum(seg.duration_sec for seg in segments)
        rough_rate = total_obs / max(1.0, float(total_sec))

        for L in BASELINE_BLOCK_LENGTHS:
            if rough_rate * L >= 20.0:
                return L
        return BASELINE_BLOCK_LENGTHS[-1]

    def fit_baseline_profile(
        self,
        engine: TemporalEngine,
        coverage: CoverageReport,
        segments: List[ContinuousSegment],
        dedup_acc: Optional[DedupAccounting] = None
    ) -> BaselineProfile:
        """Fit baseline using sticky contamination masking and iterative refitting."""
        if coverage.earliest_sec is None or coverage.latest_sec is None or not segments:
            return BaselineProfile(
                fitted_rate_1s=0.0,
                initial_block_length_sec=60,
                refitting_iterations=0,
                fraction_time_masked=0.0,
                fraction_events_masked=0.0,
                reliability="abstain",
                abstain_reason="No observed events or continuous coverage segments."
            )

        total_obs_sec = sum(seg.duration_sec for seg in segments)
        total_events = sum(b.total_requests for b in engine.sparse_buckets.values())

        if total_obs_sec < 60:
            return BaselineProfile(
                fitted_rate_1s=0.0,
                initial_block_length_sec=60,
                refitting_iterations=0,
                fraction_time_masked=0.0,
                fraction_events_masked=0.0,
                reliability="abstain",
                abstain_reason=f"Insufficient observation duration ({total_obs_sec}s < 60s minimum threshold)."
            )

        if total_events < 20:
            return BaselineProfile(
                fitted_rate_1s=total_events / max(1.0, float(total_obs_sec)),
                initial_block_length_sec=60,
                refitting_iterations=0,
                fraction_time_masked=0.0,
                fraction_events_masked=0.0,
                reliability="abstain",
                abstain_reason=f"Insufficient total events ({total_events} < 20 minimum threshold)."
            )

        block_len = self.select_initial_block_length(engine, segments)

        # Iterative sticky contamination masking
        masked_seconds: Set[int] = set()
        masked_intervals: List[MaskedInterval] = []
        iteration = 0
        current_rate = total_events / max(1.0, float(total_obs_sec))
        phi_est = 1.0

        for iteration in range(1, 6):
            # 1. Estimate rate on clean (unmasked) blocks of length block_len
            clean_blocks: List[int] = []
            clean_counts: List[int] = []
            for seg in segments:
                b_s = seg.start_sec
                while b_s + block_len <= seg.end_sec + 1:
                    b_e = b_s + block_len
                    if not any(s in masked_seconds for s in range(b_s, b_e)):
                        cnt = sum(
                            engine.sparse_buckets[s].total_requests
                            for s in range(b_s, b_e)
                            if s in engine.sparse_buckets
                        )
                        clean_blocks.append(cnt)
                    b_s += block_len

                for s in range(seg.start_sec, seg.end_sec + 1):
                    if s not in masked_seconds:
                        cnt = engine.sparse_buckets[s].total_requests if s in engine.sparse_buckets else 0
                        clean_counts.append(cnt)

            if not clean_counts:
                break

            # Robust baseline rate from clean blocks
            if len(clean_blocks) >= 2:
                sorted_b = sorted(clean_blocks)
                med_b = float(sorted_b[len(sorted_b) // 2])
                current_rate = max(0.001, med_b / float(block_len))
            else:
                current_rate = max(0.001, sum(clean_counts) / float(len(clean_counts)))

            # Dispersion estimation across clean blocks for screening sigma
            if len(clean_blocks) >= 4:
                mean_b = sum(clean_blocks) / float(len(clean_blocks))
                var_b = sum((x - mean_b) ** 2 for x in clean_blocks) / float(len(clean_blocks) - 1)
                phi_est = max(1.0, var_b / max(0.1, mean_b))
            else:
                phi_est = 1.0

            sigma_1s = math.sqrt(max(0.5, phi_est * current_rate))

            # 2. Screen initial candidate windows across fine scales for sticky masking
            new_masked = 0
            screening_scales = [10, 60] if total_obs_sec < 900 else [10, 60, 300]
            for scale in screening_scales:
                step = max(1, scale // 4)
                base_c = current_rate * scale
                base_s = math.sqrt(scale) * sigma_1s
                min_excess = self.min_excess_counts.get(scale, 4)

                for seg in segments:
                    if seg.duration_sec < scale:
                        continue
                    # Slide
                    w_s = seg.start_sec
                    while w_s + scale <= seg.end_sec + 1:
                        w_e = w_s + scale
                        obs = sum(
                            engine.sparse_buckets[s].total_requests
                            for s in range(w_s, w_e)
                            if s in engine.sparse_buckets
                        )
                        z = (obs - base_c) / max(0.5, base_s)
                        if z >= self.screening_z_threshold and obs >= 1.3 * base_c and (obs - base_c) >= min_excess:
                            # Add to sticky mask
                            for s in range(w_s, w_e):
                                if s not in masked_seconds:
                                    masked_seconds.add(s)
                                    new_masked += 1
                            masked_intervals.append(MaskedInterval(w_s, w_e, scale, z))
                        w_s += step

            if new_masked == 0:
                break

        # Calculate masked proportions
        masked_events = sum(
            engine.sparse_buckets[s].total_requests
            for s in masked_seconds
            if s in engine.sparse_buckets
        )
        frac_time_masked = len(masked_seconds) / max(1.0, float(total_obs_sec))
        frac_events_masked = masked_events / max(1.0, float(total_events))

        # 3. Model dispersion for each scale on clean non-overlapping blocks
        dispersion_by_scale: Dict[int, DispersionProfile] = {}
        for scale in SCALES_SEC:
            blocks: List[int] = []
            for seg in segments:
                b_s = seg.start_sec
                while b_s + scale <= seg.end_sec + 1:
                    b_e = b_s + scale
                    # Check if block contains any masked seconds
                    if not any(s in masked_seconds for s in range(b_s, b_e)):
                        cnt = sum(
                            engine.sparse_buckets[s].total_requests
                            for s in range(b_s, b_e)
                            if s in engine.sparse_buckets
                        )
                        blocks.append(cnt)
                    b_s += scale

            if len(blocks) >= 4:
                mean_b = sum(blocks) / float(len(blocks))
                var_b = sum((x - mean_b) ** 2 for x in blocks) / float(len(blocks) - 1)
                phi = max(1.0, var_b / max(0.1, mean_b))
            else:
                # Fallback to phi_est or Poisson lower bound
                phi = phi_est if phi_est > 1.0 else 1.0

            is_poisson = (phi <= 1.05)
            nb_r = None
            nb_q = None
            if not is_poisson:
                e_val = current_rate * scale
                nb_r = max(0.1, e_val / (phi - 1.0))
                nb_q = e_val / (nb_r + e_val)

            dispersion_by_scale[scale] = DispersionProfile(
                scale_sec=scale,
                phi=phi,
                is_poisson=is_poisson,
                nb_r=nb_r,
                nb_q=nb_q
            )

        # 4. Asymmetry check: rate in first half of clean segments vs second half
        clean_secs = [
            s for seg in segments
            for s in range(seg.start_sec, seg.end_sec + 1)
            if s not in masked_seconds
        ]
        asymmetry_ratio = 1.0
        if len(clean_secs) >= 60:
            h = len(clean_secs) // 2
            h1_rate = sum(engine.sparse_buckets[s].total_requests for s in clean_secs[:h] if s in engine.sparse_buckets) / float(h)
            h2_rate = sum(engine.sparse_buckets[s].total_requests for s in clean_secs[h:] if s in engine.sparse_buckets) / float(len(clean_secs) - h)
            asymmetry_ratio = max(0.01, h1_rate / max(0.01, h2_rate))

        # 5. Evaluate Reliability State
        warning = None
        if coverage.coverage_ratio < 0.40 or total_obs_sec < 60:
            reliability = "abstain"
            abstain_reason = f"Coverage ({coverage.coverage_ratio:.1%}) or observation duration ({total_obs_sec}s) below minimum defensible threshold."
            return BaselineProfile(
                fitted_rate_1s=current_rate,
                initial_block_length_sec=block_len,
                refitting_iterations=iteration,
                fraction_time_masked=frac_time_masked,
                fraction_events_masked=frac_events_masked,
                reliability=reliability,
                abstain_reason=abstain_reason,
                asymmetry_ratio=asymmetry_ratio,
                masked_intervals=masked_intervals,
                dispersion_by_scale=dispersion_by_scale,
            )

        if frac_time_masked > 0.40:
            reliability = "low"
            warning = f"High baseline contamination: {frac_time_masked:.1%} of time masked during refitting."
        elif not (0.40 <= asymmetry_ratio <= 2.50):
            reliability = "low"
            warning = f"Significant regime asymmetry detected (first/second half ratio: {asymmetry_ratio:.2f})."
        elif dedup_acc and dedup_acc.cap_degraded:
            reliability = "medium" if total_obs_sec >= 300 else "low"
            warning = "Deduplication memory bound exceeded; potential residual unverified duplicate logs."
        elif total_obs_sec >= 600 and coverage.coverage_ratio >= 0.85:
            reliability = "high"
        elif total_obs_sec >= 180 and coverage.coverage_ratio >= 0.60:
            reliability = "medium"
        else:
            reliability = "low"
            warning = "Limited observation time or coverage."

        # Rate-stability assessment
        masked_sec_set: Set[int] = set()
        for mi in masked_intervals:
            for s in range(mi.start_sec, mi.end_sec):
                masked_sec_set.add(s)

        rate_stability = assess_rate_stability(engine, segments, excluded_seconds=masked_sec_set)
        if rate_stability["label"] == "heterogeneous":
            if reliability in ("high", "medium"):
                reliability = "low"
                warn_msg = f"Significant rate heterogeneity detected across observation window (score: {rate_stability['statistic']} >= 20.0). Baseline reliability downgraded to low."
                warning = f"{warning} {warn_msg}" if warning else warn_msg

        return BaselineProfile(
            fitted_rate_1s=current_rate,
            initial_block_length_sec=block_len,
            refitting_iterations=iteration,
            fraction_time_masked=frac_time_masked,
            fraction_events_masked=frac_events_masked,
            reliability=reliability,
            warning=warning,
            asymmetry_ratio=asymmetry_ratio,
            masked_intervals=masked_intervals,
            dispersion_by_scale=dispersion_by_scale,
            rate_stability=rate_stability,
        )

    def scan(
        self,
        engine: TemporalEngine,
        coverage: Any,
        segments: Optional[List[ContinuousSegment]] = None,
        baseline: Optional[BaselineProfile] = None,
        z_threshold: Optional[float] = None,
    ) -> List[DetectionCandidate]:
        """Multi-scale sliding window scan across continuous segments with calibrated thresholds."""
        if baseline is None and isinstance(coverage, list):
            actual_segments = coverage
            actual_baseline = segments  # type: ignore
            actual_z = z_threshold
        else:
            actual_segments = segments or []
            actual_baseline = baseline  # type: ignore
            actual_z = z_threshold

        if actual_baseline is None or actual_baseline.reliability == "abstain":
            return []

        effective_z = float(actual_z) if actual_z is not None else self.active_z_threshold
        candidates: List[DetectionCandidate] = []

        for scale in SCALES_SEC:
            min_excess = self.min_excess_counts.get(scale, 5)
            step = max(1, scale // 5)
            disp = actual_baseline.dispersion_by_scale.get(
                scale,
                DispersionProfile(scale_sec=scale, phi=1.0, is_poisson=True)
            )

            # Expected count in window of scale
            base_count = actual_baseline.fitted_rate_1s * scale
            base_variance = base_count * disp.phi
            base_sigma = math.sqrt(max(0.25, base_variance))

            calibrated_z_thresh = effective_z

            for seg in actual_segments:
                if seg.duration_sec < scale:
                    continue

                seg_len = seg.duration_sec
                # Prefix sums for O(1) query
                prefix_sums = [0] * (seg_len + 1)
                curr_sum = 0
                for i in range(seg_len):
                    sec = seg.start_sec + i
                    if sec in engine.sparse_buckets:
                        curr_sum += engine.sparse_buckets[sec].total_requests
                    prefix_sums[i + 1] = curr_sum

                w_offset = 0
                while w_offset + scale <= seg_len:
                    obs_count = prefix_sums[w_offset + scale] - prefix_sums[w_offset]
                    excess = int(obs_count - base_count)
                    elev_ratio = obs_count / max(0.1, base_count)

                    # Practical elevation gates: X >= 1.3E and X - E >= min_excess
                    if excess >= min_excess and elev_ratio >= self.min_elevation_ratio:
                        z = (obs_count - base_count) / base_sigma
                        if z >= calibrated_z_thresh:
                            log_p, p_val, is_exact, omitted_mass = compute_upper_tail_prob(
                                observed_k=obs_count,
                                expected_e=base_count,
                                disp=disp
                            )
                            w_start = seg.start_sec + w_offset
                            w_end = w_start + scale
                            candidates.append(
                                DetectionCandidate(
                                    start_sec=w_start,
                                    end_sec=w_end,
                                    scale_sec=scale,
                                    observed_count=obs_count,
                                    baseline_count=base_count,
                                    excess_count=excess,
                                    z_score=z,
                                    elevation_ratio=elev_ratio,
                                    log_p_value=log_p,
                                    p_value=p_val,
                                    tail_exact=is_exact,
                                    omitted_mass_bound=omitted_mass,
                                )
                            )

                    w_offset += step

        return candidates

    evaluate_baseline = fit_baseline_profile
