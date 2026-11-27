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
from traffic_lab.metrics import (
    compute_iou,
    evaluate_scenario_detections,
    ScenarioEvaluationResult,
)


@dataclass
class MetricSummary:
    precision: Optional[float]
    recall: Optional[float]
    f1_score: Optional[float]
    true_positives: int
    false_positives: int
    false_negatives: int
    mean_detection_delay_sec: float
    mean_temporal_iou: float
    outcome_label: str = "positive_evaluated"
    false_incidents_per_day: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precision": round(self.precision, 4) if self.precision is not None else None,
            "recall": round(self.recall, 4) if self.recall is not None else None,
            "f1_score": round(self.f1_score, 4) if self.f1_score is not None else None,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "mean_detection_delay_sec": round(self.mean_detection_delay_sec, 2),
            "mean_temporal_iou": round(self.mean_temporal_iou, 4),
            "outcome_label": self.outcome_label,
            "false_incidents_per_day": round(self.false_incidents_per_day, 4),
        }


def evaluate_detections(
    detected_incidents: List[Incident],
    ground_truth: GroundTruth
) -> MetricSummary:
    det_intervals = [
        (inc.observed_facts.start_sec, inc.observed_facts.end_sec)
        for inc in detected_incidents
    ]
    gt_intervals = [
        (g.start_sec, g.end_sec)
        for g in ground_truth.incidents
    ]

    dur_sec = getattr(ground_truth, "total_span_sec", getattr(ground_truth, "duration_sec", 3600))
    res = evaluate_scenario_detections(
        detected_intervals=det_intervals,
        ground_truth_intervals=gt_intervals,
        scenario_name=ground_truth.scenario_name,
        total_duration_sec=float(dur_sec),
    )

    return MetricSummary(
        precision=res.precision,
        recall=res.recall,
        f1_score=res.f1_score,
        true_positives=res.true_positives,
        false_positives=res.false_positives,
        false_negatives=res.false_negatives,
        mean_detection_delay_sec=res.mean_detection_delay_sec,
        mean_temporal_iou=res.mean_temporal_iou,
        outcome_label=res.outcome_label,
        false_incidents_per_day=res.false_incidents_per_day,
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
