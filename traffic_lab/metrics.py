"""PulseWatch Evaluation Metrics: interval-based matching, Clopper-Pearson intervals, and null-class handling.

Centralizes all benchmark evaluation mathematics:
- Half-open interval Intersection over Union (IoU)
- Permissive matching (best IoU > 0.05) and strict one-to-one bipartite matching
- Honest handling of scenarios with no positive class (precision/recall/F1 are undefined/None)
- Exact Garwood Poisson bounds for false incident rates
- Exact Clopper-Pearson binomial confidence intervals
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


def compute_iou(s1: int, e1: int, s2: int, e2: int) -> float:
    """Compute temporal Intersection over Union for half-open intervals [s, e)."""
    inter_s = max(s1, s2)
    inter_e = min(e1, e2)
    inter = max(0, inter_e - inter_s)
    union_s = min(s1, s2)
    union_e = max(e1, e2)
    union = max(1, union_e - union_s)
    return inter / float(union)


def clopper_pearson(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Exact two-sided Clopper-Pearson binomial confidence interval for success probability."""
    if n <= 0 or k < 0 or k > n:
        return (0.0, 1.0)
    alpha = 1.0 - confidence
    alpha2 = alpha / 2.0

    # Lower bound: P(Bin(n, p) >= k) = alpha2 => P(Bin(n, p) <= k - 1) = 1 - alpha2
    if k == 0:
        lower = 0.0
    else:
        # Solve for p via bisection
        def binom_tail_upper(p: float) -> float:
            tot = 0.0
            for i in range(k, n + 1):
                tot += math.comb(n, i) * math.pow(p, i) * math.pow(1.0 - p, n - i)
            return tot

        low, high = 0.0, 1.0
        for _ in range(60):
            mid = 0.5 * (low + high)
            if binom_tail_upper(mid) < alpha2:
                low = mid
            else:
                high = mid
            if (high - low) < 1e-12:
                break
        lower = 0.5 * (low + high)

    # Upper bound: P(Bin(n, p) <= k) = alpha2
    if k == n:
        upper = 1.0
    else:
        def binom_tail_lower(p: float) -> float:
            tot = 0.0
            for i in range(k + 1):
                tot += math.comb(n, i) * math.pow(p, i) * math.pow(1.0 - p, n - i)
            return tot

        low, high = 0.0, 1.0
        for _ in range(60):
            mid = 0.5 * (low + high)
            if binom_tail_lower(mid) > alpha2:
                low = mid
            else:
                high = mid
            if (high - low) < 1e-12:
                break
        upper = 0.5 * (low + high)

    return (max(0.0, min(1.0, lower)), max(0.0, min(1.0, upper)))


def garwood_poisson_interval(k: int, exposure_units: float, confidence: float = 0.95) -> Tuple[float, float]:
    """Exact two-sided Garwood Poisson confidence interval for rate per unit exposure."""
    if exposure_units <= 0.0:
        return (0.0, float("inf"))
    alpha = 1.0 - confidence
    alpha2 = alpha / 2.0

    from pulsewatch.calibration import _regularized_gamma_q

    # Lower bound: Q(k, lambda_low) = 1 - alpha2 => 0 if k == 0
    if k == 0:
        lower_rate = 0.0
    else:
        low, high = 0.0, float(k)
        target = 1.0 - alpha2
        for _ in range(60):
            mid = 0.5 * (low + high)
            if _regularized_gamma_q(k, mid) < target:
                high = mid
            else:
                low = mid
            if (high - low) < 1e-10:
                break
        lower_rate = (0.5 * (low + high)) / exposure_units

    # Upper bound: Q(k + 1, lambda_high) = alpha2
    target_up = alpha2
    low, high = float(k), max(10.0, float(k) * 4.0)
    while _regularized_gamma_q(k + 1, high) > target_up:
        high *= 2.0
    for _ in range(60):
        mid = 0.5 * (low + high)
        if _regularized_gamma_q(k + 1, mid) > target_up:
            low = mid
        else:
            high = mid
        if (high - low) < 1e-10:
            break
    upper_rate = (0.5 * (low + high)) / exposure_units

    return (lower_rate, upper_rate)


@dataclass
class StrictMatchResult:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: Optional[float]
    recall: Optional[float]
    f1_score: Optional[float]
    mean_iou: float
    pairs: List[Tuple[int, int, float]]  # (det_idx, gt_idx, iou)


def match_intervals_strict(
    detections: Sequence[Tuple[int, int]],
    ground_truth: Sequence[Tuple[int, int]],
    min_iou: float = 0.05,
) -> StrictMatchResult:
    """Greedy one-to-one bipartite interval assignment in descending order of IoU."""
    candidates: List[Tuple[float, int, int]] = []
    for d_idx, (ds, de) in enumerate(detections):
        for g_idx, (gs, ge) in enumerate(ground_truth):
            iou = compute_iou(ds, de, gs, ge)
            if iou > min_iou:
                candidates.append((iou, d_idx, g_idx))

    # Sort descending by IoU, with deterministic tie-breaking by indices
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    used_det: Set[int] = set()
    used_gt: Set[int] = set()
    pairs: List[Tuple[int, int, float]] = []

    for iou, d_idx, g_idx in candidates:
        if d_idx not in used_det and g_idx not in used_gt:
            used_det.add(d_idx)
            used_gt.add(g_idx)
            pairs.append((d_idx, g_idx, iou))

    tp = len(pairs)
    fp = len(detections) - len(used_det)
    fn = len(ground_truth) - len(used_gt)

    if len(ground_truth) == 0:
        prec = None
        rec = None
        f1 = None
    else:
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, tp + fn)
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0.0 else 0.0

    mean_iou = sum(p[2] for p in pairs) / float(tp) if tp > 0 else 0.0

    return StrictMatchResult(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=prec,
        recall=rec,
        f1_score=f1,
        mean_iou=mean_iou,
        pairs=pairs,
    )


def match_intervals_optimal(
    detections: Sequence[Tuple[int, int]],
    ground_truth: Sequence[Tuple[int, int]],
    min_iou: float = 0.05,
) -> StrictMatchResult:
    """Exact optimal maximum-weight one-to-one bipartite interval assignment.

    Maximizes (num_matches, total_iou) globally via branch-and-bound, avoiding
    greedy heuristic trap cases on asymmetric IoU matrices.
    """
    if len(detections) == 0 or len(ground_truth) == 0:
        return match_intervals_strict(detections, ground_truth, min_iou=min_iou)

    # Compute candidate edges above threshold
    matrix: Dict[Tuple[int, int], float] = {}
    for d_idx, (ds, de) in enumerate(detections):
        for g_idx, (gs, ge) in enumerate(ground_truth):
            iou = compute_iou(ds, de, gs, ge)
            if iou > min_iou:
                matrix[(d_idx, g_idx)] = iou

    if not matrix:
        return match_intervals_strict(detections, ground_truth, min_iou=min_iou)

    best_pairs: List[Tuple[int, int, float]] = []
    best_score = (0, 0.0)

    dets = sorted(set(d for d, _ in matrix.keys()))

    def search(det_pos: int, used_gt: Set[int], current_pairs: List[Tuple[int, int, float]], current_iou: float) -> None:
        nonlocal best_score, best_pairs
        remaining = len(dets) - det_pos
        max_possible_matches = len(current_pairs) + remaining
        if max_possible_matches < best_score[0]:
            return
        if max_possible_matches == best_score[0] and (current_iou + remaining * 1.0) <= best_score[1]:
            return

        if det_pos == len(dets):
            score = (len(current_pairs), round(current_iou, 6))
            if score > best_score:
                best_score = score
                best_pairs = list(current_pairs)
            return

        d_idx = dets[det_pos]
        # Option 1: try matching d_idx to each available g_idx (descending IoU for early pruning)
        available_g = [(g, matrix[(d_idx, g)]) for (d, g) in matrix if d == d_idx and g not in used_gt]
        available_g.sort(key=lambda x: -x[1])

        for g_idx, iou in available_g:
            used_gt.add(g_idx)
            current_pairs.append((d_idx, g_idx, iou))
            search(det_pos + 1, used_gt, current_pairs, current_iou + iou)
            current_pairs.pop()
            used_gt.remove(g_idx)

        # Option 2: skip matching d_idx
        search(det_pos + 1, used_gt, current_pairs, current_iou)

    search(0, set(), [], 0.0)

    best_pairs.sort(key=lambda x: (x[0], x[1]))
    tp = len(best_pairs)
    fp = len(detections) - tp
    fn = len(ground_truth) - tp
    prec = tp / max(1, tp + fp) if len(ground_truth) > 0 else None
    rec = tp / max(1, tp + fn) if len(ground_truth) > 0 else None
    f1 = (2 * prec * rec) / (prec + rec) if (prec and rec and (prec + rec) > 0.0) else (0.0 if len(ground_truth) > 0 else None)
    mean_iou = sum(p[2] for p in best_pairs) / float(tp) if tp > 0 else 0.0

    return StrictMatchResult(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=prec,
        recall=rec,
        f1_score=f1,
        mean_iou=mean_iou,
        pairs=best_pairs,
    )


@dataclass
class ScenarioEvaluationResult:
    scenario_name: str
    has_positive_class: bool
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: Optional[float]
    recall: Optional[float]
    f1_score: Optional[float]
    mean_detection_delay_sec: float
    mean_temporal_iou: float
    mean_abs_start_error_sec: float
    mean_abs_end_error_sec: float
    fragmentation_extra_detections: int
    strict_one_to_one: Dict[str, Any]
    iou_sensitivity: Dict[str, Dict[str, Any]]
    exposure_sec: float
    exposure_days: float
    false_incidents_per_hour: float
    false_incidents_per_day: float
    poisson_interval_rate_per_day: Tuple[float, float]
    outcome_label: str  # no_alert_success, no_alert_abstained, false_alerts, positive_evaluated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "has_positive_class": self.has_positive_class,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4) if self.precision is not None else None,
            "recall": round(self.recall, 4) if self.recall is not None else None,
            "f1_score": round(self.f1_score, 4) if self.f1_score is not None else None,
            "mean_detection_delay_sec": round(self.mean_detection_delay_sec, 2),
            "mean_temporal_iou": round(self.mean_temporal_iou, 4),
            "mean_abs_start_error_sec": round(self.mean_abs_start_error_sec, 2),
            "mean_abs_end_error_sec": round(self.mean_abs_end_error_sec, 2),
            "fragmentation_extra_detections": self.fragmentation_extra_detections,
            "strict_one_to_one": self.strict_one_to_one,
            "iou_sensitivity": self.iou_sensitivity,
            "exposure_sec": round(self.exposure_sec, 1),
            "exposure_days": round(self.exposure_days, 4),
            "false_incidents_per_hour": round(self.false_incidents_per_hour, 4),
            "false_incidents_per_day": round(self.false_incidents_per_day, 4),
            "poisson_interval_rate_per_day": [
                round(self.poisson_interval_rate_per_day[0], 4),
                round(self.poisson_interval_rate_per_day[1], 4),
            ],
            "outcome_label": self.outcome_label,
        }


def evaluate_scenario_detections(
    detected_intervals: Sequence[Tuple[int, int]],
    ground_truth_intervals: Sequence[Tuple[int, int]],
    scenario_name: str = "scenario",
    total_duration_sec: float = 3600.0,
    abstained: bool = False,
) -> ScenarioEvaluationResult:
    """Comprehensive evaluation of detected incident intervals against ground truth."""
    has_positives = len(ground_truth_intervals) > 0
    exposure_sec = max(1.0, float(total_duration_sec))
    exposure_days = exposure_sec / 86400.0

    # 1. Negative scenario (No ground truth incidents)
    if not has_positives:
        fp = len(detected_intervals)
        if abstained:
            outcome = "no_alert_abstained"
        elif fp == 0:
            outcome = "no_alert_success"
        else:
            outcome = "false_alerts"

        rate_per_hr = (fp / exposure_sec) * 3600.0
        rate_per_day = fp / exposure_days
        garwood_bounds = garwood_poisson_interval(fp, exposure_days)

        return ScenarioEvaluationResult(
            scenario_name=scenario_name,
            has_positive_class=False,
            true_positives=0,
            false_positives=fp,
            false_negatives=0,
            precision=None,  # undefined: serialized as null
            recall=None,     # undefined: serialized as null
            f1_score=None,   # undefined: serialized as null
            mean_detection_delay_sec=0.0,
            mean_temporal_iou=0.0,
            mean_abs_start_error_sec=0.0,
            mean_abs_end_error_sec=0.0,
            fragmentation_extra_detections=0,
            strict_one_to_one={
                "true_positives": 0,
                "false_positives": fp,
                "false_negatives": 0,
                "precision": None,
                "recall": None,
                "f1_score": None,
                "mean_iou": 0.0,
            },
            iou_sensitivity={
                "0.05": {"tp": 0, "fp": fp, "fn": 0, "f1": None},
                "0.30": {"tp": 0, "fp": fp, "fn": 0, "f1": None},
                "0.50": {"tp": 0, "fp": fp, "fn": 0, "f1": None},
            },
            exposure_sec=exposure_sec,
            exposure_days=exposure_days,
            false_incidents_per_hour=rate_per_hr,
            false_incidents_per_day=rate_per_day,
            poisson_interval_rate_per_day=garwood_bounds,
            outcome_label=outcome,
        )

    # 2. Positive scenario (Primary permissive legacy matching IoU > 0.05)
    matched_gt: Set[int] = set()
    matched_det: Set[int] = set()
    delays: List[float] = []
    ious: List[float] = []
    start_errors: List[float] = []
    end_errors: List[float] = []

    for d_idx, (ds, de) in enumerate(detected_intervals):
        best_gt_idx = None
        best_iou = 0.0
        for g_idx, (gs, ge) in enumerate(ground_truth_intervals):
            iou = compute_iou(ds, de, gs, ge)
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = g_idx

        if best_gt_idx is not None and best_iou > 0.05:
            matched_gt.add(best_gt_idx)
            matched_det.add(d_idx)
            gs, ge = ground_truth_intervals[best_gt_idx]
            delay = max(0, ds - gs)
            delays.append(float(delay))
            ious.append(best_iou)
            start_errors.append(abs(float(ds - gs)))
            end_errors.append(abs(float(de - ge)))

    tp = len(matched_gt)
    fn = len(ground_truth_intervals) - tp
    fp = len(detected_intervals) - len(matched_det)
    frag_extra = max(0, len(matched_det) - len(matched_gt))

    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = (2.0 * prec * rec) / (prec + rec) if (prec + rec) > 0.0 else 0.0

    mean_delay = sum(delays) / float(len(delays)) if delays else 0.0
    mean_iou = sum(ious) / float(len(ious)) if ious else 0.0
    mean_start_err = sum(start_errors) / float(len(start_errors)) if start_errors else 0.0
    mean_end_err = sum(end_errors) / float(len(end_errors)) if end_errors else 0.0

    # Strict one-to-one matching
    strict_res = match_intervals_strict(detected_intervals, ground_truth_intervals, min_iou=0.05)

    # IoU sensitivity sweep
    sens: Dict[str, Dict[str, Any]] = {}
    for thresh in [0.05, 0.30, 0.50]:
        st = match_intervals_strict(detected_intervals, ground_truth_intervals, min_iou=thresh)
        sens[str(thresh)] = {
            "tp": st.true_positives,
            "fp": st.false_positives,
            "fn": st.false_negatives,
            "f1": round(st.f1_score, 4) if st.f1_score is not None else None,
        }

    # Background exposure (exposure minus ground-truth seconds)
    gt_sec_total = sum(ge - gs for gs, ge in ground_truth_intervals)
    bg_exposure_sec = max(1.0, exposure_sec - gt_sec_total)
    bg_exposure_days = bg_exposure_sec / 86400.0

    rate_per_hr = (fp / bg_exposure_sec) * 3600.0
    rate_per_day = fp / bg_exposure_days
    garwood_bounds = garwood_poisson_interval(fp, bg_exposure_days)

    return ScenarioEvaluationResult(
        scenario_name=scenario_name,
        has_positive_class=True,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=prec,
        recall=rec,
        f1_score=f1,
        mean_detection_delay_sec=mean_delay,
        mean_temporal_iou=mean_iou,
        mean_abs_start_error_sec=mean_start_err,
        mean_abs_end_error_sec=mean_end_err,
        fragmentation_extra_detections=frag_extra,
        strict_one_to_one={
            "true_positives": strict_res.true_positives,
            "false_positives": strict_res.false_positives,
            "false_negatives": strict_res.false_negatives,
            "precision": round(strict_res.precision, 4) if strict_res.precision is not None else None,
            "recall": round(strict_res.recall, 4) if strict_res.recall is not None else None,
            "f1_score": round(strict_res.f1_score, 4) if strict_res.f1_score is not None else None,
            "mean_iou": round(strict_res.mean_iou, 4),
        },
        iou_sensitivity=sens,
        exposure_sec=exposure_sec,
        exposure_days=exposure_days,
        false_incidents_per_hour=rate_per_hr,
        false_incidents_per_day=rate_per_day,
        poisson_interval_rate_per_day=garwood_bounds,
        outcome_label="positive_evaluated",
    )
