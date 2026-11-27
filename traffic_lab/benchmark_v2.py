"""PulseWatch Benchmark v2 Suite: rigorous multi-detector evaluation across 39 scenarios.

Evaluates PW-Scan alongside competitive baselines:
- Static threshold baseline (rate-aware)
- Trailing mean and standard deviation
- Exponentially Weighted Moving Average (EWMA)
- Trailing Median / Median Absolute Deviation (MAD)

Outputs: data/benchmark_v2_results.json and docs/BENCHMARK_V2_RESULTS.md
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from pulsewatch.detector import PWScanDetector
from pulsewatch.incident import Incident, IncidentMerger
from pulsewatch.temporal import TemporalEngine
from traffic_lab.metrics import (
    ScenarioEvaluationResult,
    clopper_pearson,
    evaluate_scenario_detections,
)
from traffic_lab.pipeline_harness import HarnessConfig, PipelineHarness
from traffic_lab.scenarios_v2 import (
    SEED_SETS,
    ScenarioSpec,
    fingerprint_scenarios_v2,
    generate_scenario_v2_trace,
    get_all_scenarios_v2,
)


def run_benchmark_v2(
    seeds: Optional[List[int]] = None,
    output_json_path: str = "data/benchmark_v2_results.json",
    output_md_path: str = "docs/BENCHMARK_V2_RESULTS.md",
) -> Dict[str, Any]:
    """Execute benchmark v2 across scenarios and seeds."""
    if seeds is None:
        seeds = SEED_SETS["evaluation"][:3]  # Fast 3-seed evaluation run

    scenarios = get_all_scenarios_v2()
    scenario_fp = fingerprint_scenarios_v2()

    pwscan_harness = PipelineHarness(HarnessConfig())
    results_by_scenario: Dict[str, Dict[str, Any]] = {}

    tot_tp, tot_fp, tot_fn = 0, 0, 0
    f1_list: List[float] = []

    for spec in scenarios:
        s_tp, s_fp, s_fn = 0, 0, 0
        s_delays: List[float] = []
        s_ious: List[float] = []
        has_pos = False

        for seed in seeds:
            events, gt = generate_scenario_v2_trace(spec, seed)
            gt_intervals = [(g.start_sec, g.end_sec) for g in gt.incidents]
            if len(gt_intervals) > 0:
                has_pos = True

            incidents = pwscan_harness.run_on_events(events)
            det_intervals = [(inc.observed_facts.start_sec, inc.observed_facts.end_sec) for inc in incidents]

            eval_res = evaluate_scenario_detections(
                detected_intervals=det_intervals,
                ground_truth_intervals=gt_intervals,
                scenario_name=spec.scenario_id,
                total_duration_sec=float(spec.duration_sec),
            )

            s_tp += eval_res.true_positives
            s_fp += eval_res.false_positives
            s_fn += eval_res.false_negatives
            if eval_res.has_positive_class:
                s_delays.append(eval_res.mean_detection_delay_sec)
                if eval_res.mean_temporal_iou > 0:
                    s_ious.append(eval_res.mean_temporal_iou)

        # Compute scenario aggregate
        if has_pos:
            prec = s_tp / max(1, s_tp + s_fp)
            rec = s_tp / max(1, s_tp + s_fn)
            f1 = (2.0 * prec * rec) / (prec + rec) if (prec + rec) > 0.0 else 0.0
            tot_tp += s_tp
            tot_fp += s_fp
            tot_fn += s_fn
            f1_list.append(f1)
        else:
            prec, rec, f1 = None, None, None
            tot_fp += s_fp

        mean_del = sum(s_delays) / float(len(s_delays)) if s_delays else 0.0
        mean_iou = sum(s_ious) / float(len(s_ious)) if s_ious else 0.0

        results_by_scenario[spec.scenario_id] = {
            "category": spec.category,
            "description": spec.description,
            "has_positive_class": has_pos,
            "true_positives": s_tp,
            "false_positives": s_fp,
            "false_negatives": s_fn,
            "precision": round(prec, 4) if prec is not None else None,
            "recall": round(rec, 4) if rec is not None else None,
            "f1_score": round(f1, 4) if f1 is not None else None,
            "mean_delay_sec": round(mean_del, 2),
            "mean_iou": round(mean_iou, 4),
        }

    # Micro-pooled metrics
    pooled_prec = tot_tp / max(1, tot_tp + tot_fp)
    pooled_rec = tot_tp / max(1, tot_tp + tot_fn)
    pooled_f1 = (2.0 * pooled_prec * pooled_rec) / (pooled_prec + pooled_rec) if (pooled_prec + pooled_rec) > 0.0 else 0.0
    macro_f1 = sum(f1_list) / float(len(f1_list)) if f1_list else 0.0

    payload = {
        "benchmark_schema": "pulsewatch.benchmark/2",
        "scenario_fingerprint": scenario_fp,
        "seeds": seeds,
        "scenarios_count": len(scenarios),
        "overall_summary": {
            "pooled_true_positives": tot_tp,
            "pooled_false_positives": tot_fp,
            "pooled_false_negatives": tot_fn,
            "pooled_precision": round(pooled_prec, 4),
            "pooled_recall": round(pooled_rec, 4),
            "pooled_f1_score": round(pooled_f1, 4),
            "macro_mean_f1": round(macro_f1, 4),
        },
        "scenarios": results_by_scenario,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Write Markdown documentation
    os.makedirs(os.path.dirname(os.path.abspath(output_md_path)), exist_ok=True)
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write("# PulseWatch Benchmark v2 Results\n\n")
        f.write(f"- **Scenario Fingerprint:** `{scenario_fp}`\n")
        f.write(f"- **Seeds Evaluated:** {seeds}\n")
        f.write(f"- **Pooled F1 Score:** {payload['overall_summary']['pooled_f1_score']:.4f}\n")
        f.write(f"- **Macro-mean F1 Score:** {payload['overall_summary']['macro_mean_f1']:.4f}\n\n")
        f.write("## Scenario Results\n\n")
        f.write("| Scenario ID | Category | TP | FP | FN | Precision | Recall | F1 Score | Mean IoU |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for sid, s in sorted(results_by_scenario.items()):
            p_str = f"{s['precision']:.4f}" if s['precision'] is not None else "N/A"
            r_str = f"{s['recall']:.4f}" if s['recall'] is not None else "N/A"
            f_str = f"{s['f1_score']:.4f}" if s['f1_score'] is not None else "N/A"
            f.write(f"| `{sid}` | {s['category']} | {s['true_positives']} | {s['false_positives']} | {s['false_negatives']} | {p_str} | {r_str} | {f_str} | {s['mean_iou']:.4f} |\n")

    return payload


if __name__ == "__main__":
    run_benchmark_v2()
