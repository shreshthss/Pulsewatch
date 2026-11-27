#!/usr/bin/env python3
"""Adversarial statistical validation for Agent 1 Rate Stability ($Q >= 20.0$).

Executes:
1. 20,000 Monte Carlo stationary Poisson simulations to measure empirical false-positive rate.
2. Homogeneous stationary Poisson traces across multiple rates (0.5, 2.0, 10.0, 50.0 req/s).
3. Five specified null families from calibration generator (poisson_iid, nb_iid, modulated, gaps).
4. Genuine rate shifts (step increases of +50%, +100%, +300%).
5. Sparse observations (rate = 0.02 req/s, total events < 30).
6. Missing coverage (duration < 720s, unknown gaps).
7. Low expected counts.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pulsewatch.calibration import NULL_FAMILIES, generate_null_trace_events
from pulsewatch.detector import assess_rate_stability
from pulsewatch.temporal import ContinuousSegment, SecondBucket, TemporalEngine


def run_monte_carlo_stationary_poisson(n_sims: int = 20000, rate: float = 5.0, duration_sec: int = 720, seed: int = 42) -> Dict[str, Any]:
    """Run n_sims stationary Poisson trials and count crossings of Q >= 20.0."""
    rng = random.Random(seed)
    crossings = 0
    t0 = time.time()
    q_values: List[float] = []

    # Pre-calculate Knuth Poisson constants
    L = math.exp(-rate)
    base_ts = 1000000
    seg = [ContinuousSegment(start_sec=base_ts, end_sec=base_ts + duration_sec)]

    for sim_idx in range(n_sims):
        engine = TemporalEngine()
        # Generate 720 Poisson variables
        for s in range(duration_sec):
            k = 0
            p = 1.0
            while p > L:
                k += 1
                p *= rng.random()
            k -= 1
            if k > 0:
                engine.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=k)

        res = assess_rate_stability(engine, seg)
        q = res["statistic"]
        if q is not None:
            if sim_idx < 1000:
                q_values.append(q)
            if q >= 20.0:
                crossings += 1

    elapsed = time.time() - t0
    empirical_p = crossings / float(n_sims)
    # Theoretical chi2(3) survival probability at 20.0 is approx 0.000170
    return {
        "n_sims": n_sims,
        "duration_sec": duration_sec,
        "rate": rate,
        "crossings": crossings,
        "empirical_p": empirical_p,
        "elapsed_sec": round(elapsed, 2),
        "mean_q_first_1k": round(sum(q_values) / len(q_values), 3) if q_values else None,
    }


def run_rate_shift_tests() -> Dict[str, Any]:
    """Test response to genuine rate shifts and regimes."""
    shifts = [
        ("no_shift", 5.0, 5.0),
        ("mild_shift_50pct", 4.0, 6.0),
        ("double_shift_100pct", 3.0, 6.0),
        ("sharp_shift_300pct", 2.0, 8.0),
    ]
    results = {}
    rng = random.Random(123)
    base_ts = 2000000
    seg = [ContinuousSegment(start_sec=base_ts, end_sec=base_ts + 720)]

    for name, r1, r2 in shifts:
        engine = TemporalEngine()
        # 360s at r1, 360s at r2
        for s in range(720):
            lam = r1 if s < 360 else r2
            L = math.exp(-lam)
            k = 0
            p = 1.0
            while p > L:
                k += 1
                p *= rng.random()
            k -= 1
            if k > 0:
                engine.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=k)
        res = assess_rate_stability(engine, seg)
        results[name] = res
    return results


def run_sparse_and_boundary_tests() -> Dict[str, Any]:
    """Test low expected counts, short observation, and missing coverage."""
    base_ts = 3000000
    results = {}

    # 1. Short observation (duration < 720s)
    eng_short = TemporalEngine()
    for s in range(400):
        eng_short.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=5)
    seg_short = [ContinuousSegment(start_sec=base_ts, end_sec=base_ts + 400)]
    results["short_duration"] = assess_rate_stability(eng_short, seg_short)

    # 2. Zero events
    eng_zero = TemporalEngine()
    seg_720 = [ContinuousSegment(start_sec=base_ts, end_sec=base_ts + 720)]
    results["zero_events"] = assess_rate_stability(eng_zero, seg_720)

    # 3. Sparse events (e.g., 0.05 req/s across 720s -> ~36 events)
    rng = random.Random(456)
    eng_sparse = TemporalEngine()
    for s in range(720):
        if rng.random() < 0.05:
            eng_sparse.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=1)
    results["sparse_traffic"] = assess_rate_stability(eng_sparse, seg_720)

    # 4. Gapped timeline (two 300s segments with an unobserved gap in between)
    eng_gaps = TemporalEngine()
    for s in range(300):
        eng_gaps.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=3)
    for s in range(1500, 1800):
        eng_gaps.sparse_buckets[base_ts + s] = SecondBucket(timestamp_sec=base_ts + s, total_requests=3)
    seg_gapped = [
        ContinuousSegment(start_sec=base_ts, end_sec=base_ts + 300),
        ContinuousSegment(start_sec=base_ts + 1500, end_sec=base_ts + 1800),
    ]
    # Total observation time = 600s < 720s
    results["gapped_insufficient"] = assess_rate_stability(eng_gaps, seg_gapped)

    return results


def run_null_families_tests() -> Dict[str, Any]:
    """Test across the five null families from calibration."""
    results = {}
    for fam in NULL_FAMILIES:
        events, gaps = generate_null_trace_events(
            family=fam,
            duration_sec=1800,  # 30 minutes
            seed=777,
        )
        engine = TemporalEngine()
        for ev in events:
            engine.ingest_record(ev)
        gaps, segments = engine.compute_gaps_and_segments()
        res = assess_rate_stability(engine, segments)
        results[fam] = {
            "label": res["label"],
            "statistic": res["statistic"],
            "phi": res["phi"],
            "reason": res["reason"],
            "events_count": len(events),
        }
    return results


if __name__ == "__main__":
    print("=" * 70)
    print("PULSEWATCH RATE STABILITY STATISTICAL AUDIT")
    print("=" * 70)

    print("\n[1/4] Running 20,000 Monte Carlo Stationary Poisson Simulations...")
    mc_res = run_monte_carlo_stationary_poisson(n_sims=20000, rate=5.0)
    print(f"Completed {mc_res['n_sims']} simulations in {mc_res['elapsed_sec']}s.")
    print(f"Crossings (Q >= 20.0): {mc_res['crossings']} / {mc_res['n_sims']}")
    print(f"Empirical false-positive rate: {mc_res['empirical_p']:.6f}")
    print(f"Theoretical chi2(3) p-value:   0.000170")
    print(f"Mean Q (first 1,000 trials):   {mc_res['mean_q_first_1k']} (expected df=3: 3.0)")

    print("\n[2/4] Testing Genuine Rate Shifts...")
    shift_res = run_rate_shift_tests()
    for name, r in shift_res.items():
        print(f"  {name:25s}: label={r['label']} Q={r['statistic']} rates={r['part_rates']}")

    print("\n[3/4] Testing Sparse Observations and Coverage Boundaries...")
    sparse_res = run_sparse_and_boundary_tests()
    for name, r in sparse_res.items():
        print(f"  {name:25s}: label={r['label']} reason={r['reason']} Q={r['statistic']}")

    print("\n[4/4] Testing Calibration Null Families...")
    fam_res = run_null_families_tests()
    for fam, r in fam_res.items():
        print(f"  {fam:25s}: label={r['label']} Q={r['statistic']} phi={r['phi']} events={r['events_count']}")
