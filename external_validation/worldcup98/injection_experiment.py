"""Semi-synthetic perturbation injection experiment runner for WorldCup98.

Takes authentic background traces and evaluates detector response against
injected volume and composition perturbations across random seeds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from external_validation.worldcup98.inject import (
    InjectionManifest,
    inject_perturbation,
)
from pulsewatch.cli import run_pipeline
from traffic_lab.metrics import match_intervals_strict


def run_injection_experiment(
    background_path: str,
    output_dir: str = "external_validation/worldcup98/scratch/injection_runs",
    seeds_count: int = 3,
    base_seed: int = 52000,
) -> Dict[str, Any]:
    """Execute perturbation injection suite across authentic background trace."""
    if not os.path.exists(background_path):
        raise FileNotFoundError(f"Background trace '{background_path}' not found")

    with open(background_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    os.makedirs(output_dir, exist_ok=True)

    kinds = [
        ("burst", 60, 3.0),
        ("surge", 180, 2.0),
        ("level_shift", 300, 1.8),
        ("ramp", 240, 2.5),
        ("route_concentration", 120, 1.0),
        ("status_shift", 120, 1.0),
    ]

    results: List[Dict[str, Any]] = []

    for s_idx in range(seeds_count):
        seed = base_seed + s_idx
        for kind, dur, mult in kinds:
            stream_file = os.path.join(output_dir, f"injected_{kind}_seed{seed}.jsonl")
            manifest_file = os.path.join(output_dir, f"manifest_{kind}_seed{seed}.json")

            target_route = "/api/v1/injected/endpoint" if kind == "route_concentration" else None
            target_status = 503 if kind == "status_shift" else None

            injected_events, manifest = inject_perturbation(
                background_events=events,
                perturbation_kind=kind,
                start_offset_sec=300,
                duration_sec=dur,
                multiplier=mult,
                seed=seed,
                target_route=target_route,
                target_status=target_status,
            )

            # Write event stream and manifest separately
            with open(stream_file, "w", encoding="utf-8") as f:
                for ev in injected_events:
                    f.write(json.dumps(ev) + "\n")

            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest.to_dict(), f, indent=2)

            # Run detector against stream only
            report, _, _, _, _ = run_pipeline(stream_file)

            # Evaluate match against ground truth interval
            gt_intervals = [(manifest.start_sec, manifest.end_sec)]
            det_intervals = [
                (inc["observed_facts"]["start_sec"], inc["observed_facts"]["end_sec"])
                for inc in report.get("incidents", [])
            ]
            match_res = match_intervals_strict(det_intervals, gt_intervals, min_iou=0.05)

            results.append({
                "seed": seed,
                "perturbation_kind": kind,
                "duration_sec": dur,
                "multiplier": mult,
                "detected": match_res.true_positives > 0,
                "mean_iou": match_res.mean_iou,
                "incidents_count": len(det_intervals),
            })

    summary = {
        "background_path": background_path,
        "seeds_count": seeds_count,
        "total_trials": len(results),
        "results": results,
    }
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WorldCup98 Injection Experiment Runner")
    parser.add_argument("background", help="Path to authentic background JSONL file")
    parser.add_argument("--seeds", type=int, default=3, help="Number of seeds to evaluate (default: 3)")
    parser.add_argument("--outdir", default="external_validation/worldcup98/scratch/injection_runs")
    args = parser.parse_args(argv)

    summary = run_injection_experiment(
        background_path=args.background,
        output_dir=args.outdir,
        seeds_count=args.seeds,
    )
    print(f"Injection experiment completed: {summary['total_trials']} trials across {args.seeds} seeds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
