#!/usr/bin/env python3
"""Reproducible static website data generator for PulseWatch GitHub Pages.

Reads verified project artifacts from:
- demo/*.jsonl and demo/*_analysis.json
- data/benchmark_v2_results.json
- data/ablation_results.json
- external_validation/worldcup98/results/aggregate_summary.json

Outputs sanitized static JSON files to site/data/ and updates site/demo/
with the latest generated demo JSON and HTML reports.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from typing import Any, Dict, List, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from pulsewatch.dedup import DedupAccounting, iter_deduplicated
from pulsewatch.ingest import IngestAccounting, ingest_jsonl_file


DEMO_SCENARIOS = [
    {
        "key": "normal",
        "scene_num": 1,
        "name": "1_normal_traffic",
        "title": "Scene 1: Nominal Poisson Traffic",
        "tab_label": "1. Normal Traffic",
        "subtitle": "Steady normal baseline arrivals demonstrating zero false alarms on stationary Poisson traffic",
        "badge": "0 False Alarms",
        "badge_class": "badge-success",
        "highlight": "Zero False Alarms on Stationary Poisson Traffic",
        "explanation": (
            "Standard threshold detectors often flag natural discrete Poisson fluctuations as anomalies. "
            "PulseWatch estimates adaptive baseline dispersion (phi = 1.00) and achieves zero false positives "
            "across 12 continuous minutes (2,183 requests)."
        ),
        "ground_truth_intervals": [],
        "false_alarms": 0,
    },
    {
        "key": "gap",
        "scene_num": 2,
        "name": "4_coverage_gap",
        "title": "Scene 2: Network Outage & Coverage Gap",
        "tab_label": "2. Coverage Gap",
        "subtitle": "1,000s collector outage (>900s threshold) isolated as unknown gap with explicit abstention",
        "badge": "Detector Abstained",
        "badge_class": "badge-warning",
        "highlight": "Defensible Abstention Prevents Telemetry Resume False Alarms",
        "explanation": (
            "When log shippers crash, naive moving-average detectors treat unobserved silence as zero traffic and fire "
            "false alerts when telemetry resumes. PulseWatch isolates the 1,001s gap, measures 19.3% coverage "
            "(below the 40% defensible threshold), and explicitly abstains."
        ),
        "ground_truth_intervals": [],
        "false_alarms": 0,
    },
    {
        "key": "surge",
        "scene_num": 3,
        "name": "3_genuine_surge",
        "title": "Scene 3: Genuine 90s API Surge",
        "tab_label": "3. Genuine Surge",
        "subtitle": "Multi-client surge on /api/v1/items/search localized across 10s, 60s, and 300s scales",
        "badge": "1 Incident Flagged",
        "badge_class": "badge-danger",
        "highlight": "Multi-Scale Surge Localization & Second-Pass Contributor Attribution",
        "explanation": (
            "A genuine 90-second surge (25 req/s peak vs 2.00 req/s baseline, Z = 125.98) fired across 10s, 60s, and 300s scales. "
            "Boundary trimming isolates the exact 90s active excess (+2,070 requests), while second-pass replay extracts "
            "the concentrated route (/api/v1/items/search) and broad caller distribution."
        ),
        "ground_truth_intervals": [[180, 270]],
        "false_alarms": 0,
    },
    {
        "key": "dedup",
        "scene_num": 4,
        "name": "2_duplicate_replay",
        "title": "Scene 4: Duplicate Replay Attack / Storm",
        "tab_label": "4. Replay Storm",
        "subtitle": "235 exact duplicate proxy log replays filtered by 4-level BLAKE2b identity deduplication",
        "badge": "235 Duplicates Removed",
        "badge_class": "badge-cyan",
        "highlight": "Single-Pass Identity Deduplication Suppresses Phantom Spike",
        "explanation": (
            "Proxy retry storms replay identical request identities, creating a phantom +49% volume spike (715 raw lines vs 480 unique). "
            "PulseWatch's 4-level BLAKE2b hierarchy removes all 235 exact duplicates before temporal aggregation, yielding 0 false alarms."
        ),
        "ground_truth_intervals": [],
        "false_alarms": 0,
    },
    {
        "key": "regime",
        "scene_num": 7,
        "name": "5_ambiguous_regime",
        "title": "Bonus Scene: Ambiguous Regime Level Shift",
        "tab_label": "Bonus: Regime Shift",
        "subtitle": "Permanent rate elevation (2 -> 16 req/s) flagged as level_shift_candidate with asymmetry warning",
        "badge": "Level Shift Candidate",
        "badge_class": "badge-amber",
        "highlight": "Regime Asymmetry Check Distinguishes Scaling Steps from Transient Bursts",
        "explanation": (
            "When traffic permanently steps up from 2 to 16 req/s, PulseWatch detects significant first-half vs second-half "
            "asymmetry (ratio 0.15), classifies the incident as level_shift_candidate, downgrades baseline reliability to LOW, "
            "and guides operators to confirm whether an intentional scaling event occurred."
        ),
        "ground_truth_intervals": [[180, 300]],
        "false_alarms": 0,
    },
]


def _build_timeline_bins(
    jsonl_path: str,
    analysis: Dict[str, Any],
    bin_size_sec: int = 10,
) -> Dict[str, Any]:
    """Compute compact raw vs deduplicated request-rate bins from demo JSONL."""
    raw_counts: Counter = Counter()
    dedup_counts: Counter = Counter()

    min_ts = None
    max_ts = None

    events_iter, ingest_acc = ingest_jsonl_file(jsonl_path)
    for ev in events_iter:
        ts = ev.timestamp_sec
        if min_ts is None or ts < min_ts:
            min_ts = ts
        if max_ts is None or ts > max_ts:
            max_ts = ts
        raw_counts[ts] += 1

    dedup_acc = DedupAccounting()
    for ev in iter_deduplicated(events_iter, accounting=dedup_acc):
        dedup_counts[ev.timestamp_sec] += 1

    if min_ts is None or max_ts is None:
        return {"bin_size_sec": bin_size_sec, "bins": [], "span_sec": 0}

    # Unknown gaps from analysis
    gaps = analysis.get("coverage_and_gaps", {}).get("unknown_gaps", [])
    gap_ranges = [(int(g["start_sec"]), int(g["end_sec"])) for g in gaps]

    # Detected incident intervals
    inc_ranges = [
        (int(inc["observed_facts"]["start_sec"]), int(inc["observed_facts"]["end_sec"]))
        for inc in analysis.get("incidents", [])
    ]

    span = max_ts - min_ts + 1
    # Use larger bin size for long spans (e.g. coverage gap 1241s -> 20s bins)
    if span > 900:
        bin_size_sec = 20

    bins: List[Dict[str, Any]] = []
    for offset in range(0, span, bin_size_sec):
        b_start = min_ts + offset
        b_end = min(max_ts + 1, b_start + bin_size_sec)
        width = max(1, b_end - b_start)

        raw_sum = sum(raw_counts[t] for t in range(b_start, b_end))
        dedup_sum = sum(dedup_counts[t] for t in range(b_start, b_end))

        in_gap = any(not (b_end <= gs or b_start >= ge) for gs, ge in gap_ranges)
        in_inc = any(not (b_end <= is_ or b_start >= ie) for is_, ie in inc_ranges)

        bins.append({
            "t_rel": offset,
            "raw_rps": round(raw_sum / float(width), 2),
            "dedup_rps": round(dedup_sum / float(width), 2),
            "is_gap": in_gap,
            "is_incident": in_inc,
        })

    return {
        "bin_size_sec": bin_size_sec,
        "span_sec": span,
        "min_ts": min_ts,
        "max_ts": max_ts,
        "gap_intervals_rel": [[gs - min_ts, ge - min_ts] for gs, ge in gap_ranges],
        "incident_intervals_rel": [[is_ - min_ts, ie - min_ts] for is_, ie in inc_ranges],
        "bins": bins,
    }


def build_scenarios_data(site_demo_dir: str) -> Dict[str, Any]:
    """Build sanitized scenarios payload for the 6 judge scenes + bonus regime shift."""
    os.makedirs(site_demo_dir, exist_ok=True)
    scenarios: Dict[str, Any] = {}

    for sc in DEMO_SCENARIOS:
        name = sc["name"]
        jsonl_path = os.path.join(ROOT_DIR, "demo", f"{name}.jsonl")
        analysis_path = os.path.join(ROOT_DIR, "demo", f"{name}_analysis.json")
        html_path = os.path.join(ROOT_DIR, "demo", f"{name}_report.html")

        # Copy verified artifacts to site/demo/
        shutil.copy2(analysis_path, os.path.join(site_demo_dir, f"{name}_analysis.json"))
        shutil.copy2(html_path, os.path.join(site_demo_dir, f"{name}_report.html"))

        with open(analysis_path, "r", encoding="utf-8") as f:
            rep = json.load(f)

        inp = rep.get("input", {})
        cov = rep.get("coverage", {})
        base = rep.get("baseline_profile", {})
        incidents = rep.get("incidents", [])
        timeline = _build_timeline_bins(jsonl_path, rep)

        # Aggregate contributor evidence from first incident if present
        lead_routes: Dict[str, int] = {}
        top_clients: Dict[str, int] = {}
        status_dist: Dict[str, int] = {}
        conc_tags: List[str] = []
        leads: List[Dict[str, str]] = []
        if incidents:
            ev = incidents[0].get("contributor_evidence", {})
            lead_routes = ev.get("top_routes", {})
            top_clients = ev.get("top_clients", {})
            status_dist = ev.get("status_distribution", {})
            conc_tags = ev.get("concentration_tags", [])
            leads = incidents[0].get("investigation_leads", [])

        scenarios[sc["key"]] = {
            "key": sc["key"],
            "scene_num": sc["scene_num"],
            "title": sc["title"],
            "tab_label": sc["tab_label"],
            "subtitle": sc["subtitle"],
            "badge": sc["badge"],
            "badge_class": sc["badge_class"],
            "highlight": sc["highlight"],
            "explanation": sc["explanation"],
            "kind": "trace",
            "schema_version": rep.get("schema_version", "pulsewatch.report/1"),
            "report_revision": rep.get("report_revision", 2),
            "analysis_hash": rep.get("analysis_hash", ""),
            "source_sha256": inp.get("source_sha256", ""),
            "metrics": {
                "total_span_sec": cov.get("total_span_sec", 0),
                "input_records": inp.get("valid_records", 0),
                "retained_records": inp.get("retained_records", 0),
                "duplicates_removed": inp.get("duplicates_removed", 0),
                "coverage_pct": round(cov.get("coverage_ratio", 0.0) * 100.0, 1),
                "unknown_gaps_count": cov.get("unknown_gaps_count", 0),
                "baseline_reliability": str(base.get("reliability", "unknown")).upper(),
                "baseline_rate_1s": round(base.get("fitted_rate_1s", 0.0), 2),
                "masked_pct": round(base.get("masked_time_fraction", 0.0) * 100.0, 1),
                "incident_count": len(incidents),
                "false_alarms": sc["false_alarms"],
                "exactness_status": inp.get("exactness_status", "unknown"),
                "second_pass_status": rep.get("second_pass_status", {}).get("status", "unknown") if isinstance(rep.get("second_pass_status"), dict) else str(rep.get("second_pass_status", "unknown")),
            },
            "timeline": timeline,
            "ground_truth_intervals_rel": sc["ground_truth_intervals"],
            "incidents": incidents,
            "evidence": {
                "leading_routes": lead_routes,
                "top_clients": top_clients,
                "status_distribution": status_dist,
                "concentration_tags": conc_tags,
                "investigation_leads": leads,
                "data_notices": rep.get("data_notices", []),
                "known_limitations": rep.get("known_limitations", []),
            },
            "report_path": f"demo/{name}_report.html",
            "json_path": f"demo/{name}_analysis.json",
        }

    # Scene 5: WorldCup98 External Validation
    wc_path = os.path.join(ROOT_DIR, "external_validation", "worldcup98", "results", "aggregate_summary.json")
    with open(wc_path, "r", encoding="utf-8") as f:
        wc = json.load(f)

    wc_samples = []
    for sid in ["day20", "day46", "day78"]:
        s = wc["samples"][sid]
        wc_samples.append({
            "sample_id": sid,
            "label": s.get("collection_day_paris_local", sid),
            "utc_interval": s.get("utc_interval_iso", ""),
            "records": s.get("records_valid_exported", 0),
            "coverage_pct": round(s.get("coverage_ratio", 1.0) * 100.0, 1),
            "baseline_rate_1s": round(s.get("baseline_fitted_rate_1s", 0.0), 2),
            "baseline_reliability": str(s.get("baseline_reliability", "high")).upper(),
            "incidents_detected": s.get("incidents_detected", 0),
            "input_sha256": s.get("input_sha256", ""),
        })

    scenarios["worldcup98"] = {
        "key": "worldcup98",
        "scene_num": 5,
        "title": "Scene 5: FIFA World Cup 1998 Historical Trace Validation",
        "tab_label": "5. WorldCup98",
        "subtitle": "Authentic 1998 HTTP server traces scaling up to 210,287 requests per 15-minute window",
        "badge": "0 False Alarms",
        "badge_class": "badge-success",
        "highlight": "Scale-Invariant Stability Across Real Historical HTTP Workloads",
        "explanation": (
            "Evaluated on three authentic 15-minute slices from the 1998 FIFA World Cup dataset "
            "(Day 20: 19.25 req/s, Day 46: 237.58 req/s, Day 78 Final: 144.02 req/s). "
            "PulseWatch maintained zero false alarms across all authentic background windows. "
            "Separate semi-synthetic perturbation experiments inject controlled bursts onto these traces with manifest-tracked ground truth."
        ),
        "kind": "worldcup98",
        "samples": wc_samples,
        "metrics": {
            "samples_evaluated": len(wc_samples),
            "max_records_window": max(s["records"] for s in wc_samples),
            "max_rate_1s": max(s["baseline_rate_1s"] for s in wc_samples),
            "total_records": sum(s["records"] for s in wc_samples),
            "incident_count": sum(s["incidents_detected"] for s in wc_samples),
            "false_alarms": 0,
        },
        "json_path": "data/scenarios.json",
    }

    # Scene 6: Component Ablations
    ab_path = os.path.join(ROOT_DIR, "data", "ablation_results.json")
    with open(ab_path, "r", encoding="utf-8") as f:
        ab = json.load(f)

    scenarios["ablations"] = {
        "key": "ablations",
        "scene_num": 6,
        "title": "Scene 6: Empirical Architectural Ablations",
        "tab_label": "6. Ablations",
        "subtitle": "Five controlled ablation experiments quantifying each subsystem's causal contribution",
        "badge": "5 Studies Verified",
        "badge_class": "badge-cyan",
        "highlight": "Empirical Proof of Every Pipeline Stage",
        "explanation": (
            "Disabling individual subsystems produces measurable regressions: omitting dispersion scaling "
            "inflates false alarms on overdispersed traffic (19.2 -> 136.8 FP/day); disabling sticky masking "
            "allows surges to contaminate the baseline; single-scale scanning degrades localization IoU."
        ),
        "kind": "ablations",
        "experiments_count": len(ab.get("experiments", [])),
        "json_path": "data/ablations.json",
    }

    return {
        "schema": "pulsewatch.site.scenarios/1",
        "generated_from": "demo/*_analysis.json + aggregate_summary.json + ablation_results.json",
        "order": ["normal", "gap", "surge", "dedup", "worldcup98", "ablations", "regime"],
        "scenarios": scenarios,
    }


def build_benchmark_data() -> Dict[str, Any]:
    """Sanitize and summarize data/benchmark_v2_results.json for site/data/benchmark_v2.json."""
    src_path = os.path.join(ROOT_DIR, "data", "benchmark_v2_results.json")
    with open(src_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    scenarios_list = []
    cat_stats: Dict[str, Dict[str, Any]] = {}

    for sid, s in sorted(raw["scenarios"].items()):
        cat = s["category"]
        if cat not in cat_stats:
            cat_stats[cat] = {"category": cat, "count": 0, "tp": 0, "fp": 0, "fn": 0, "f1_vals": [], "iou_vals": []}
        cs = cat_stats[cat]
        cs["count"] += 1
        cs["tp"] += s["true_positives"]
        cs["fp"] += s["false_positives"]
        cs["fn"] += s["false_negatives"]
        if s["f1_score"] is not None:
            cs["f1_vals"].append(s["f1_score"])
        if s["has_positive_class"] and s["mean_iou"] is not None:
            cs["iou_vals"].append(s["mean_iou"])

        scenarios_list.append({
            "scenario_id": sid,
            "category": cat,
            "description": s["description"],
            "has_positive_class": s["has_positive_class"],
            "tp": s["true_positives"],
            "fp": s["false_positives"],
            "fn": s["false_negatives"],
            "precision": s["precision"],
            "recall": s["recall"],
            "f1_score": s["f1_score"],
            "mean_delay_sec": s["mean_delay_sec"],
            "mean_iou": s["mean_iou"],
        })

    categories = []
    for cat, cs in sorted(cat_stats.items()):
        categories.append({
            "category": cat,
            "count": cs["count"],
            "tp": cs["tp"],
            "fp": cs["fp"],
            "fn": cs["fn"],
            "mean_f1": round(sum(cs["f1_vals"]) / len(cs["f1_vals"]), 4) if cs["f1_vals"] else None,
            "mean_iou": round(sum(cs["iou_vals"]) / len(cs["iou_vals"]), 4) if cs["iou_vals"] else None,
        })

    return {
        "benchmark_schema": raw["benchmark_schema"],
        "scenario_fingerprint": raw["scenario_fingerprint"],
        "seeds": raw["seeds"],
        "scenarios_count": raw["scenarios_count"],
        "categories_count": len(categories),
        "overall_summary": raw["overall_summary"],
        "categories": categories,
        "scenarios": scenarios_list,
    }


def build_ablation_data() -> Dict[str, Any]:
    """Copy and structure data/ablation_results.json for site/data/ablations.json."""
    src_path = os.path.join(ROOT_DIR, "data", "ablation_results.json")
    with open(src_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    site_data_dir = os.path.join(ROOT_DIR, "site", "data")
    site_demo_dir = os.path.join(ROOT_DIR, "site", "demo")
    os.makedirs(site_data_dir, exist_ok=True)
    os.makedirs(site_demo_dir, exist_ok=True)

    scenarios_payload = build_scenarios_data(site_demo_dir)
    benchmark_payload = build_benchmark_data()
    ablation_payload = build_ablation_data()

    with open(os.path.join(site_data_dir, "scenarios.json"), "w", encoding="utf-8") as f:
        json.dump(scenarios_payload, f, indent=2)

    with open(os.path.join(site_data_dir, "benchmark_v2.json"), "w", encoding="utf-8") as f:
        json.dump(benchmark_payload, f, indent=2)

    with open(os.path.join(site_data_dir, "ablations.json"), "w", encoding="utf-8") as f:
        json.dump(ablation_payload, f, indent=2)

    # Ensure .nojekyll exists for GitHub Pages
    nojekyll_path = os.path.join(ROOT_DIR, "site", ".nojekyll")
    with open(nojekyll_path, "w", encoding="utf-8") as f:
        f.write("")

    print(f"Generated site/data/scenarios.json ({len(scenarios_payload['scenarios'])} scenes)")
    print(f"Generated site/data/benchmark_v2.json ({benchmark_payload['scenarios_count']} scenarios, {benchmark_payload['categories_count']} categories)")
    print(f"Generated site/data/ablations.json ({len(ablation_payload['experiments'])} experiments)")


if __name__ == "__main__":
    main()
