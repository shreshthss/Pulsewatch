"""Complete PW-Scan statistical detector: adaptive baseline, sticky masking, dispersion, and tail evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from pulsewatch.dedup import DedupAccounting
from pulsewatch.temporal import ContinuousSegment, CoverageReport, TemporalEngine

SCALES_SEC = [10, 60, 300]
BASELINE_BLOCK_LENGTHS = [60, 300, 900, 3600]


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

    @property
    def median_rate_1s(self) -> float:
        return self.fitted_rate_1s

    def to_dict(self) -> Dict[str, Any]:
        return {
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
        target_false_incidents_per_day: float = 1.0,
        screening_z_threshold: float = 4.0,
        min_elevation_ratio: float = 1.3,
        min_excess_counts: Optional[Dict[int, int]] = None
    ):
        self.target_false_incidents_per_day = target_false_incidents_per_day
        self.screening_z_threshold = screening_z_threshold
        self.min_elevation_ratio = min_elevation_ratio
        self.min_excess_counts = min_excess_counts or {10: 4, 60: 12, 300: 25}

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
        )

    def scan(
        self,
        engine: TemporalEngine,
        coverage: CoverageReport,
        segments: List[ContinuousSegment],
        baseline: BaselineProfile
    ) -> List[DetectionCandidate]:
        """Multi-scale sliding window scan across continuous segments with calibrated thresholds."""
        if baseline.reliability == "abstain":
            return []

        candidates: List[DetectionCandidate] = []

        for scale in SCALES_SEC:
            min_excess = self.min_excess_counts.get(scale, 5)
            step = max(1, scale // 5)
            disp = baseline.dispersion_by_scale.get(
                scale,
                DispersionProfile(scale_sec=scale, phi=1.0, is_poisson=True)
            )

            # Expected count in window of scale
            base_count = baseline.fitted_rate_1s * scale
            base_variance = base_count * disp.phi
            base_sigma = math.sqrt(max(0.25, base_variance))

            # Incident false-alarm calibration: c threshold
            # r_w(c) = (86400 / w) * kappa * c^2 * Psi(c)
            # Default practical c is around 3.5 to 4.0 calibrated for target_false_incidents_per_day
            calibrated_z_thresh = 3.5

            for seg in segments:
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
