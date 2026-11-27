"""Old-vs-new pipeline measurement: runtime, peak RSS, accounting and report equivalence.

Each measurement runs ``pulsewatch.cli.run_pipeline`` in a *fresh subprocess* against a
chosen source tree (``--old`` / ``--new``), so peak RSS is that run's own high-water mark.

Workloads are generated deterministically from a seed using only ``random.random()`` and
``random.gauss()``. They are synthetic: they exercise duplicates, identity conflicts,
records with no usable identity, malformed lines, out-of-order timestamps and coverage
control records (one mid-file, one at end-of-file). They are NOT independent real-world
traffic and say nothing about detection accuracy.

Usage:
    python benchmarks/pipeline_memory.py compare --old /path/to/old --new /path/to/new \
        --sizes 20000,200000,1000000 --reps 3 --workdir /tmp/pwbench --out results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
from typing import Any, Dict, List, Optional

T0 = 1_700_000_000


# --------------------------------------------------------------------------- generation
def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def generate(
    path: str,
    n_events: int,
    seed: int = 7,
    rate: float = 40.0,
    dup_rate: float = 0.03,
    identity_rate: float = 0.8,
    n_routes: int = 40,
    n_clients: int = 2000,
    n_bursts: int = 3,
    burst_len: int = 30,
    burst_mult: float = 12.0,
    conflict_rate: float = 0.0005,
    malformed_rate: float = 0.0005,
    out_of_order_rate: float = 0.005,
) -> Dict[str, Any]:
    """Write a deterministic JSONL workload; returns a description incl. SHA-256."""
    rng = random.Random(seed)
    span_est = int(n_events / rate) + 1
    burst_starts = sorted(int((i + 1) * span_est / (n_bursts + 1)) for i in range(n_bursts))
    attackers = [f"198.51.100.{i}" for i in range(5)]
    seq_counter = [0, 0, 0]
    pending: List[List[Any]] = []  # [emit_after_count, line]
    emitted = 0
    t_sec = 0
    prev_line: Optional[str] = None
    in_burst = lambda s: any(b <= s < b + burst_len for b in burst_starts)  # noqa: E731
    n_dups = n_conf = n_bad = 0

    def make_event(sec: int, burst: bool) -> Dict[str, Any]:
        if burst and rng.random() < 0.7:
            route, client = "/api/v1/login", attackers[int(rng.random() * 5)]
            status = 401 if rng.random() < 0.8 else 429
            method = "POST"
        else:
            ri = int(n_routes * rng.random() ** 2)
            route = f"/api/v1/r{ri}"
            client = f"10.{(int(n_clients * rng.random() ** 1.5) >> 8) & 255}.{int(n_clients * rng.random() ** 1.5) & 255}.1"
            u = rng.random()
            status = 200 if u < 0.96 else (404 if u < 0.99 else 503)
            method = "GET" if ri % 5 else "POST"
        ev: Dict[str, Any] = {
            "timestamp": T0 + sec, "method": method, "route": route,
            "client": client, "status": status,
        }
        if rng.random() < identity_rate:
            k = int(rng.random() * 3)
            seq_counter[k] += 1
            ev["source"] = f"proxy-{k}"
            ev["seq"] = seq_counter[k]
        return ev

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        def emit(line: str) -> None:
            nonlocal emitted, prev_line
            if prev_line is not None and rng.random() < out_of_order_rate:
                f.write(line + "\n")      # swap with previous line: small out-of-order
                f.write(prev_line + "\n")
                prev_line = None
            else:
                if prev_line is not None:
                    f.write(prev_line + "\n")
                prev_line = line
            emitted += 1
            for p in pending[:]:
                if emitted >= p[0]:
                    if prev_line is not None:
                        f.write(prev_line + "\n")
                    prev_line = p[1]
                    pending.remove(p)

        written_events = 0
        mid_cov_done = False
        while written_events < n_events:
            burst = in_burst(t_sec)
            for _ in range(_poisson(rng, rate * (burst_mult if burst else 1.0))):
                if written_events >= n_events:
                    break
                ev = make_event(t_sec, burst)
                line = json.dumps(ev, separators=(",", ":"))
                emit(line)
                written_events += 1
                if "seq" in ev:
                    u = rng.random()
                    if u < dup_rate:
                        pending.append([emitted + int(rng.random() * 50), line])
                        n_dups += 1
                    elif u < dup_rate + conflict_rate:
                        c = dict(ev)
                        c["status"] = 500 if ev["status"] != 500 else 200
                        emit(json.dumps(c, separators=(",", ":")))
                        n_conf += 1
                if rng.random() < malformed_rate:
                    emit("{not json")
                    n_bad += 1
            if not mid_cov_done and written_events >= n_events // 2:
                emit(json.dumps({"_pw": "coverage", "start": T0 + t_sec - 600, "end": T0 + t_sec}))
                mid_cov_done = True
            t_sec += 1
        for p in pending:
            emit(p[1])
        if prev_line is not None:
            f.write(prev_line + "\n")
        # End-of-file coverage declaration: engine must learn it only after exhaustion.
        f.write(json.dumps({"_pw": "coverage", "start": T0 + t_sec - 300, "end": T0 + t_sec + 300}) + "\n")

    h = hashlib.sha256()
    with open(path, "rb") as fb:
        for chunk in iter(lambda: fb.read(1 << 20), b""):
            h.update(chunk)
    return {
        "path": path, "events_requested": n_events, "seed": seed,
        "replays_injected": n_dups, "conflicts_injected": n_conf, "malformed_injected": n_bad,
        "bytes": os.path.getsize(path), "sha256": h.hexdigest(),
    }


# --------------------------------------------------------------------------- measurement
def _maxrss_mb() -> float:
    import resource
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / (1024.0 * 1024.0) if sys.platform == "darwin" else v / 1024.0


def child_main(path: str, report_out: Optional[str]) -> None:
    import time
    try:
        from pulsewatch.cli import run_pipeline  # imported from the tree selected via PYTHONPATH
    except ModuleNotFoundError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from pulsewatch.cli import run_pipeline

    baseline_rss = _maxrss_mb()
    t0 = time.perf_counter()
    report, engine, _w, _c, dacc = run_pipeline(path)
    elapsed = time.perf_counter() - t0
    peak = _maxrss_mb()
    canon = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if report_out:
        with open(report_out, "w", encoding="utf-8") as fo:
            fo.write(canon)
    total = dacc.total_input_records
    json.dump({
        "seconds": elapsed, "peak_rss_mb": peak, "interpreter_baseline_rss_mb": baseline_rss,
        "records_after_ingest": total, "retained": dacc.retained_records,
        "duplicates_removed": dacc.duplicates_removed,
        "identity_ineligible": dacc.identity_ineligible,
        "identity_coverage": (1 - dacc.identity_ineligible / total) if total else None,
        "identity_conflicts": dacc.identity_conflicts, "cap_degraded": dacc.cap_degraded,
        "exactness_status": dacc.exactness_status,
        "temporal_buckets": len(engine.sparse_buckets),
        "incidents": len(report["incidents"]),
        "report_sha256": hashlib.sha256(canon.encode()).hexdigest(),
    }, sys.stdout)


def run_child(tree: str, path: str, report_out: Optional[str]) -> Dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=tree, PYTHONHASHSEED="0")
    p = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "child", path] + (["--report-out", report_out] if report_out else []),
        env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(p.stdout)


def json_diff(a: Any, b: Any, path: str = "$", out: Optional[List[str]] = None, cap: int = 40) -> List[str]:
    out = [] if out is None else out
    if len(out) >= cap:
        return out
    if type(a) != type(b):
        out.append(path)
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}")
            else:
                json_diff(a[k], b[k], f"{path}.{k}", out, cap)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}[len {len(a)} vs {len(b)}]")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                json_diff(x, y, f"{path}[{i}]", out, cap)
    elif a != b:
        out.append(path)
    return out


def compare_main(args: argparse.Namespace) -> None:
    os.makedirs(args.workdir, exist_ok=True)
    results: Dict[str, Any] = {
        "machine": {
            "python": platform.python_version(), "platform": platform.platform(),
            "cpu_count": os.cpu_count(), "machine": platform.machine(),
        },
        "reps": args.reps, "workloads": [],
    }
    for n in [int(x) for x in args.sizes.split(",")]:
        path = os.path.join(args.workdir, f"workload_{n}.jsonl")
        meta = generate(path, n, seed=args.seed)
        row: Dict[str, Any] = {"input": meta, "runs": {}}
        reports: Dict[str, str] = {}
        for label, tree in (("old", args.old), ("new", args.new)):
            runs = []
            for r in range(args.reps):
                rep_out = os.path.join(args.workdir, f"report_{n}_{label}.json") if r == 0 else None
                runs.append(run_child(tree, path, rep_out))
            if os.path.exists(os.path.join(args.workdir, f"report_{n}_{label}.json")):
                reports[label] = os.path.join(args.workdir, f"report_{n}_{label}.json")
            row["runs"][label] = {
                "seconds_median": statistics.median(x["seconds"] for x in runs),
                "seconds_all": [round(x["seconds"], 3) for x in runs],
                "peak_rss_mb_median": statistics.median(x["peak_rss_mb"] for x in runs),
                "peak_rss_mb_all": [round(x["peak_rss_mb"], 1) for x in runs],
                "interpreter_baseline_rss_mb": round(runs[0]["interpreter_baseline_rss_mb"], 1),
                **{k: runs[0][k] for k in (
                    "records_after_ingest", "retained", "duplicates_removed", "identity_ineligible",
                    "identity_coverage", "identity_conflicts", "cap_degraded", "exactness_status",
                    "temporal_buckets", "incidents", "report_sha256")},
            }
        with open(reports["old"], encoding="utf-8") as fa, open(reports["new"], encoding="utf-8") as fb:
            ra, rb = json.load(fa), json.load(fb)
        diffs = json_diff(ra, rb)
        row["report_identical"] = not diffs
        row["report_differing_paths"] = diffs
        results["workloads"].append(row)
        o, nw = row["runs"]["old"], row["runs"]["new"]
        print(f"n={n:>9,} old {o['seconds_median']:7.2f}s {o['peak_rss_mb_median']:8.1f}MB | "
              f"new {nw['seconds_median']:7.2f}s {nw['peak_rss_mb_median']:8.1f}MB | "
              f"report_identical={row['report_identical']} diffs={len(diffs)}", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fo:
            json.dump(results, fo, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("child"); c.add_argument("path"); c.add_argument("--report-out")
    m = sub.add_parser("compare")
    m.add_argument("--old", required=True); m.add_argument("--new", required=True)
    m.add_argument("--sizes", default="20000,200000,1000000")
    m.add_argument("--reps", type=int, default=3); m.add_argument("--seed", type=int, default=7)
    m.add_argument("--workdir", default="/tmp/pwbench"); m.add_argument("--out")
    g = sub.add_parser("gen"); g.add_argument("path"); g.add_argument("n", type=int); g.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if a.cmd == "child":
        child_main(a.path, a.report_out)
    elif a.cmd == "compare":
        compare_main(a)
    else:
        print(json.dumps(generate(a.path, a.n, seed=a.seed), indent=2))


if __name__ == "__main__":
    main()
