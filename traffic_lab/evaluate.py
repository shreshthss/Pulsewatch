"""Evaluation framework: compare PW-Scan against naive baseline on labeled synthetic traffic."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pulsewatch.cli import run_pipeline
from pulsewatch.dedup import deduplicate_records
from pulsewatch.detector import DetectionCandidate
from pulsewatch.incident import Incident, IncidentMerger
from pulsewatch.ingest import IngestAccounting, ingest_jsonl_stream
from pulsewatch.temporal import TemporalEngine
from traffic_lab.generator import GroundTruth


@dataclass
class MetricSummary:
    precision: float
    recall: float
    f1_score: float
    true_positives: int
    false_positives: int
    false_negatives: int
    mean_detection_delay_sec: float
    mean_temporal_iou: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "mean_detection_delay_sec": round(self.mean_detection_delay_sec, 2),
            "mean_temporal_iou": round(self.mean_temporal_iou, 4),
        }


def compute_iou(s1: int, e1: int, s2: int, e2: int) -> float:
    """Compute temporal Intersection over Union."""
    inter_s = max(s1, s2)
    inter_e = min(e1, e2)
    inter = max(0, inter_e - inter_s)
    union_s = min(s1, s2)
    union_e = max(e1, e2)
    union = max(1, union_e - union_s)
    return inter / float(union)


def evaluate_detections(
    detected_incidents: List[Incident],
    ground_truth: GroundTruth
) -> MetricSummary:
    gt_incidents = ground_truth.incidents
    if not gt_incidents:
        fp = len(detected_incidents)
        return MetricSummary(
            precision=1.0 if fp == 0 else 0.0,
            recall=1.0,
            f1_score=1.0 if fp == 0 else 0.0,
            true_positives=0,
            false_positives=fp,
            false_negatives=0,
            mean_detection_delay_sec=0.0,
            mean_temporal_iou=0.0,
        )

    matched_gt = set()
    matched_det = set()
    delays: List[float] = []
    ious: List[float] = []

    for d_idx, d_inc in enumerate(detected_incidents):
        d_obs = d_inc.observed_facts
        best_gt_idx = None
        best_iou = 0.0

        for g_idx, g_inc in enumerate(gt_incidents):
            iou = compute_iou(d_obs.start_sec, d_obs.end_sec, g_inc.start_sec, g_inc.end_sec)
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = g_idx

        if best_gt_idx is not None and best_iou > 0.05:
            matched_gt.add(best_gt_idx)
            matched_det.add(d_idx)
            g_inc = gt_incidents[best_gt_idx]
            delay = max(0, d_obs.start_sec - g_inc.start_sec)
            delays.append(float(delay))
            ious.append(best_iou)

    tp = len(matched_gt)
    fn = len(gt_incidents) - tp
    fp = len(detected_incidents) - len(matched_det)

    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = (2 * prec * rec) / max(1e-6, prec + rec) if (prec + rec) > 0 else 0.0
    mean_delay = sum(delays) / max(1, len(delays)) if delays else 0.0
    mean_iou = sum(ious) / max(1, len(ious)) if ious else 0.0

    return MetricSummary(
        precision=prec,
        recall=rec,
        f1_score=f1,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        mean_detection_delay_sec=mean_delay,
        mean_temporal_iou=mean_iou,
    )


class NaiveStaticThresholdDetector:
    """Naive non-adaptive detector that flags any 60s window with count > fixed_threshold."""

    def __init__(self, fixed_threshold_60s: int = 500):
        self.fixed_threshold_60s = fixed_threshold_60s

    def scan(self, engine: TemporalEngine) -> List[DetectionCandidate]:
        windows = engine.generate_60s_windows()
        candidates: List[DetectionCandidate] = []
        for w in windows:
            if w.total_requests >= self.fixed_threshold_60s:
                candidates.append(
                    DetectionCandidate(
                        start_sec=w.start_sec,
                        end_sec=w.end_sec,
                        scale_sec=60,
                        observed_count=w.total_requests,
                        baseline_count=float(self.fixed_threshold_60s),
                        excess_count=w.total_requests - self.fixed_threshold_60s,
                        z_score=3.0,
                        elevation_ratio=w.total_requests / max(1, self.fixed_threshold_60s),
                    )
                )
        return candidates


def run_benchmark_comparison(
    jsonl_path: str,
    ground_truth: GroundTruth
) -> Dict[str, Any]:
    """Run both PW-Scan and Naive detector on the dataset and compare performance."""
    # 1. Run PW-Scan
    pw_report, engine, _, _, _ = run_pipeline(jsonl_path)
    # Convert report incident dicts to Incident instances for evaluation
    merger = IncidentMerger()
    pw_incidents_candidates: List[DetectionCandidate] = []
    # Evaluate directly from pw_report incidents
    pw_incidents_data = pw_report["incidents"]

    # Reconstitute Incident objects
    class DummyIncidentWrapper:
        def __init__(self, d: Dict[str, Any]):
            self.observed_facts = type("Obs", (), d["observed_facts"])()

    reconstituted_pw = [DummyIncidentWrapper(inc) for inc in pw_incidents_data]
    pw_metrics = evaluate_detections(reconstituted_pw, ground_truth)  # type: ignore

    # 2. Run Naive Detector
    naive_detector = NaiveStaticThresholdDetector(fixed_threshold_60s=500)
    naive_candidates = naive_detector.scan(engine)
    naive_incidents = merger.merge_candidates(naive_candidates, engine, median_rate_1s=2.0)
    naive_metrics = evaluate_detections(naive_incidents, ground_truth)

    return {
        "dataset": ground_truth.scenario_name,
        "total_records": ground_truth.total_records,
        "pw_scan": pw_metrics.to_dict(),
        "naive_baseline": naive_metrics.to_dict(),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 -m traffic_lab.evaluate DATASET.jsonl GROUND_TRUTH.json")
        sys.exit(1)
    jsonl_file = sys.argv[1]
    gt_file = sys.argv[2]
    with open(gt_file, "r", encoding="utf-8") as f:
        gt_data = json.load(f)
    gt = GroundTruth.from_dict(gt_data)
    results = run_benchmark_comparison(jsonl_file, gt)
    print(json.dumps(results, indent=2))
