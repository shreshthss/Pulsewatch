"""Deterministic judge demonstration pack generator and automated runner."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict, List

from pulsewatch.cli import run_pipeline
from pulsewatch.html_report import render_html_report
from pulsewatch.report import format_cli_summary
from traffic_lab.benchmark_matrix import generate_scenario

DEMO_DIR = "demo"
DEMO_SCENARIOS = {
    "1_normal_traffic": ("N0_poisson", "Steady normal baseline traffic (Demonstrates: Zero false alarms on stationary Poisson arrivals)"),
    "2_duplicate_replay": ("D1_duplicate_storm", "Heavy duplicate proxy log storm (Demonstrates: Apparent spike filtered by identity deduplication, 0 false alarms)"),
    "3_genuine_surge": ("A3_sustained_surge", "Genuine 90s API surge (Demonstrates: Multi-scale detection, boundary trimming, concentration tags & leads)"),
    "4_coverage_gap": ("G1_long_gap", "Collector outage with 1,000s gap (Demonstrates: Unknown gap detection >900s, no false alarms across unobserved gap)"),
    "5_ambiguous_regime": ("S1_level_shift", "Regime shift with permanent rate elevation (Demonstrates: Baseline asymmetry warning, level_shift_candidate)"),
}


def prepare_demo_data(seed: int = 42):
    """Generate the 5 curated demonstration JSONL files in demo/."""
    os.makedirs(DEMO_DIR, exist_ok=True)
    out_paths = {}

    for name, (sc_id, desc) in DEMO_SCENARIOS.items():
        dur = 720 if name == "1_normal_traffic" else None
        records, _ = generate_scenario(sc_id, seed=seed, duration_sec=dur)
        jsonl_path = os.path.join(DEMO_DIR, f"{name}.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        out_paths[name] = jsonl_path

    return out_paths


def run_demo():
    """Execute complete demonstration suite, printing summary and rendering HTML reports."""
    print("=" * 78)
    print("PULSEWATCH — AEGYTHON 2026 JUDGE DEMONSTRATION SUITE")
    print("Offline synthetic API traffic anomaly detection & evidence investigation")
    print("=" * 78)

    data_paths = prepare_demo_data()

    results_table = []

    for name, (sc_id, desc) in DEMO_SCENARIOS.items():
        input_path = data_paths[name]
        json_out = os.path.join(DEMO_DIR, f"{name}_analysis.json")
        html_out = os.path.join(DEMO_DIR, f"{name}_report.html")

        print(f"\n[DEMO SCENARIO]: {name}")
        print(f"Goal: {desc}")
        print("-" * 78)

        report, _, windows_60s, coverage, dedup_acc = run_pipeline(input_path)

        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        html_content = render_html_report(report)
        with open(html_out, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(format_cli_summary(report))
        print(f"\nArtifacts: JSON -> {json_out} | HTML -> {html_out}")

        results_table.append({
            "scenario": name,
            "retained": dedup_acc.retained_records,
            "duplicates_removed": dedup_acc.duplicates_removed,
            "coverage": f"{coverage.coverage_ratio*100:.1f}%",
            "unknown_gaps": len(coverage.unknown_gaps),
            "baseline": report["baseline_status"]["reliability"].upper(),
            "incidents": len(report["incidents"]),
        })

    print("\n" + "=" * 78)
    print("DEMO SUMMARY MATRIX")
    print("=" * 78)
    print(f"{'SCENARIO':<24} {'RETAINED':<10} {'DUPS REMOVED':<14} {'COVERAGE':<10} {'BASELINE':<10} {'INCIDENTS'}")
    print("-" * 78)
    for r in results_table:
        print(f"{r['scenario']:<24} {r['retained']:<10} {r['duplicates_removed']:<14} {r['coverage']:<10} {r['baseline']:<10} {r['incidents']}")
    print("=" * 78)
    print("All demonstration reports generated in demo/ directory.")
    print("Open demo/<scenario>_report.html in any standard browser (100% offline).")


if __name__ == "__main__":
    run_demo()
