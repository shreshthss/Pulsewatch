"""PulseWatch Ablation Study: isolated measurement of architectural component contributions.

Executes 5 controlled ablation experiments across the ablation seed set (51001-51010):
A1: Deduplication (with vs without deduplication)
A2: Coverage-awareness (with vs without gap isolation)
A3: Dispersion scaling (fitted negative-binomial dispersion vs Poisson-only)
A4: Sticky contamination masking (with vs without iterative masking)
A5: Multi-scale scanning (multi-scale [10, 60, 300] vs single-scale)

Outputs schema: pulsewatch.ablation/1
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from traffic_lab.metrics import evaluate_scenario_detections
from traffic_lab.pipeline_harness import HarnessConfig, PipelineHarness
from traffic_lab.scenarios_v2 import (
    SEED_SETS,
    get_all_scenarios_v2,
    generate_scenario_v2_trace,
)

ABLATION_SCHEMA_VERSION = "pulsewatch.ablation/1"


@dataclass
class AblationScenarioSummary:
    scenario_id: str
    control_tp: int
    control_fp: int
    control_fn: int
    control_f1: Optional[float]
    control_mean_iou: float
    ablated_tp: int
    ablated_fp: int
    ablated_fn: int
    ablated_f1: Optional[float]
    ablated_mean_iou: float
    exposure_hours: float
    delta_f1: Optional[float]
    delta_fp: int


@dataclass
class AblationExperiment:
    experiment_id: str
    component_name: str
    description: str
    scenarios: List[AblationScenarioSummary]


def run_ablation_comparison(
    experiment_id: str,
    component_name: str,
    description: str,
    scenario_ids: List[str],
    control_config: HarnessConfig,
    ablated_config: HarnessConfig,
    seeds: Optional[List[int]] = None,
) -> AblationExperiment:
    """Run an ablation comparison over selected scenarios across specified seeds."""
    if seeds is None:
        seeds = SEED_SETS["ablation"][:5]  # Fast, representative subset of ablation seeds

    all_specs = {s.scenario_id: s for s in get_all_scenarios_v2()}
    control_harness = PipelineHarness(control_config)
    ablated_harness = PipelineHarness(ablated_config)

    scenario_summaries: List[AblationScenarioSummary] = []

    for sid in scenario_ids:
        if sid not in all_specs:
            continue
        spec = all_specs[sid]

        c_tp, c_fp, c_fn = 0, 0, 0
        a_tp, a_fp, a_fn = 0, 0, 0
        c_ious: List[float] = []
        a_ious: List[float] = []
        tot_exp_sec = 0.0

        for seed in seeds:
            events, gt = generate_scenario_v2_trace(spec, seed)
            gt_intervals = [(g.start_sec, g.end_sec) for g in gt.incidents]
            tot_exp_sec += spec.duration_sec

            # Run control
            c_incidents = control_harness.run_on_events(events)
            c_det = [(inc.observed_facts.start_sec, inc.observed_facts.end_sec) for inc in c_incidents]
            c_res = evaluate_scenario_detections(c_det, gt_intervals, total_duration_sec=spec.duration_sec)

            c_tp += c_res.true_positives
            c_fp += c_res.false_positives
            c_fn += c_res.false_negatives
            if c_res.has_positive_class and c_res.mean_temporal_iou > 0:
                c_ious.append(c_res.mean_temporal_iou)

            # Run ablated
            a_incidents = ablated_harness.run_on_events(events)
            a_det = [(inc.observed_facts.start_sec, inc.observed_facts.end_sec) for inc in a_incidents]
            a_res = evaluate_scenario_detections(a_det, gt_intervals, total_duration_sec=spec.duration_sec)

            a_tp += a_res.true_positives
            a_fp += a_res.false_positives
            a_fn += a_res.false_negatives
            if a_res.has_positive_class and a_res.mean_temporal_iou > 0:
                a_ious.append(a_res.mean_temporal_iou)

        # Compute aggregate metrics for scenario
        is_pos = ("POS" in sid) or ("BORD" in sid)
        c_f1 = (2.0 * c_tp) / (2.0 * c_tp + c_fp + c_fn) if is_pos and (2 * c_tp + c_fp + c_fn) > 0 else None
        a_f1 = (2.0 * a_tp) / (2.0 * a_tp + a_fp + a_fn) if is_pos and (2 * a_tp + a_fp + a_fn) > 0 else None

        c_mean_iou = sum(c_ious) / float(len(c_ious)) if c_ious else 0.0
        a_mean_iou = sum(a_ious) / float(len(a_ious)) if a_ious else 0.0
        delta_f1 = round(c_f1 - a_f1, 4) if (c_f1 is not None and a_f1 is not None) else None
        delta_fp = c_fp - a_fp

        scenario_summaries.append(
            AblationScenarioSummary(
                scenario_id=sid,
                control_tp=c_tp,
                control_fp=c_fp,
                control_fn=c_fn,
                control_f1=round(c_f1, 4) if c_f1 is not None else None,
                control_mean_iou=round(c_mean_iou, 4),
                ablated_tp=a_tp,
                ablated_fp=a_fp,
                ablated_fn=a_fn,
                ablated_f1=round(a_f1, 4) if a_f1 is not None else None,
                ablated_mean_iou=round(a_mean_iou, 4),
                exposure_hours=round(tot_exp_sec / 3600.0, 2),
                delta_f1=delta_f1,
                delta_fp=delta_fp,
            )
        )

    return AblationExperiment(
        experiment_id=experiment_id,
        component_name=component_name,
        description=description,
        scenarios=scenario_summaries,
    )


def run_full_ablation_suite(output_path: str = "data/ablation_results.json") -> Dict[str, Any]:
    """Run all 5 ablation studies and write pulsewatch.ablation/1 JSON artifact."""
    experiments: List[AblationExperiment] = []

    # A1: Deduplication
    exp1 = run_ablation_comparison(
        experiment_id="A1_deduplication",
        component_name="Deduplication",
        description="Filter duplicate replay storms before temporal aggregation",
        scenario_ids=["NEG_dup_replay", "NEG_dup_noise", "POS_dup_burst", "NEG_stable_mid", "POS_spike_short"],
        control_config=HarnessConfig(dedup_enabled=True),
        ablated_config=HarnessConfig(dedup_enabled=False),
    )
    experiments.append(exp1)

    # A2: Coverage Gap Isolation
    exp2 = run_ablation_comparison(
        experiment_id="A2_coverage_awareness",
        component_name="Coverage-Gap Isolation",
        description="Segment boundary isolation across unobserved collector outages (>900s)",
        scenario_ids=["NEG_long_gap", "NEG_short_dropout", "POS_post_gap_spike", "NEG_stable_mid"],
        control_config=HarnessConfig(coverage_aware=True),
        ablated_config=HarnessConfig(coverage_aware=False),
    )
    experiments.append(exp2)

    # A3: Dispersion Scaling
    exp3 = run_ablation_comparison(
        experiment_id="A3_dispersion_scaling",
        component_name="Dispersion Scaling (Negative Binomial)",
        description="Dynamic dispersion variance scaling vs naive Poisson unit variance",
        scenario_ids=["NEG_overdispersed_gamma", "NEG_overdispersed_ar1", "NEG_stable_mid", "POS_spike_short"],
        control_config=HarnessConfig(dispersion_scaling=True),
        ablated_config=HarnessConfig(dispersion_scaling=False),
    )
    experiments.append(exp3)

    # A4: Sticky Contamination Masking
    exp4 = run_ablation_comparison(
        experiment_id="A4_sticky_masking",
        component_name="Sticky Contamination Masking",
        description="Iterative screening and masking of anomaly windows during baseline profiling",
        scenario_ids=["POS_surge_long", "POS_level_shift", "NEG_stable_mid"],
        control_config=HarnessConfig(masking_enabled=True),
        ablated_config=HarnessConfig(masking_enabled=False),
    )
    experiments.append(exp4)

    # A5: Multi-Scale Scanning
    exp5 = run_ablation_comparison(
        experiment_id="A5_multiscale_scanning",
        component_name="Multi-Scale Sliding Scanning",
        description="Multi-scale [10s, 60s, 300s] vs single 60s scale scan",
        scenario_ids=["POS_spike_short", "POS_surge_long", "NEG_stable_mid"],
        control_config=HarnessConfig(scales=[10, 60, 300]),
        ablated_config=HarnessConfig(scales=[60]),
    )
    experiments.append(exp5)

    result_payload = {
        "schema_version": ABLATION_SCHEMA_VERSION,
        "experiments": [
            {
                "experiment_id": e.experiment_id,
                "component_name": e.component_name,
                "description": e.description,
                "scenarios": [asdict(s) for s in e.scenarios],
            }
            for e in experiments
        ],
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, indent=2)

    return result_payload


if __name__ == "__main__":
    run_full_ablation_suite()
