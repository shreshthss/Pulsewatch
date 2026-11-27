"""Semi-synthetic perturbation injection layer for WorldCup98 authentic background logs.

Injects controlled perturbations into authentic background traces:
- volume anomalies: burst, surge, level_shift, ramp
- composition anomalies: route_concentration, status_shift

Maintains strict separation between the injected event stream and the ground-truth manifest.
"""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class InjectionManifest:
    scenario_id: str
    perturbation_kind: str
    start_sec: int
    end_sec: int
    duration_sec: int
    multiplier: float
    target_route: Optional[str] = None
    target_status: Optional[int] = None
    records_added: int = 0
    records_modified: int = 0
    seed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def inject_perturbation(
    background_events: Sequence[Dict[str, Any]],
    perturbation_kind: str,
    start_offset_sec: int,
    duration_sec: int,
    multiplier: float = 2.0,
    seed: int = 42,
    target_route: Optional[str] = None,
    target_status: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], InjectionManifest]:
    """Inject a single perturbation into background events.

    Constraints:
    - start_offset_sec >= 300 (first 300s reserved for warm-up)
    - output event stream contains only the 5 standard fields: timestamp, method, route, client, status
    """
    if start_offset_sec < 300:
        raise ValueError(f"Warm-up violation: start_offset_sec={start_offset_sec} must be >= 300s")

    if not background_events:
        raise ValueError("Cannot inject perturbation into empty background events")

    # Sort background events by timestamp to get bounds
    sorted_bg = sorted(background_events, key=lambda x: x["timestamp"])
    t0 = sorted_bg[0]["timestamp"]
    t_end = sorted_bg[-1]["timestamp"]
    trace_duration = t_end - t0 + 1

    inj_start = t0 + start_offset_sec
    inj_end = inj_start + duration_sec

    if inj_end > t_end + 1:
        raise ValueError(f"Overflow violation: perturbation end {inj_end} exceeds trace end {t_end}")

    rng = random.Random(seed)
    injected_events: List[Dict[str, Any]] = []

    # Calculate background rate
    bg_rate = len(sorted_bg) / max(1.0, float(trace_duration))
    extra_rate = max(0.0, (multiplier - 1.0) * bg_rate)

    records_added = 0
    records_modified = 0

    # Clean background copy preserving standard fields
    allowed_fields = ("timestamp", "method", "route", "client", "status")

    for ev in sorted_bg:
        clean_ev = {k: ev[k] for k in allowed_fields if k in ev}
        ts = clean_ev["timestamp"]

        # Composition perturbation during interval
        if inj_start <= ts < inj_end:
            if perturbation_kind == "route_concentration":
                if target_route and rng.random() < 0.90:
                    clean_ev["route"] = target_route
                    records_modified += 1
            elif perturbation_kind == "status_shift":
                if target_status and rng.random() < 0.40:
                    clean_ev["status"] = target_status
                    records_modified += 1

        injected_events.append(clean_ev)

    # Volume additions
    if perturbation_kind in ("burst", "surge", "level_shift", "ramp"):
        # Resample pools for composition realism
        routes_pool = [e.get("route", "/") for e in sorted_bg]
        clients_pool = [e.get("client", "127.0.0.1") for e in sorted_bg]
        methods_pool = [e.get("method", "GET") for e in sorted_bg]

        for sec in range(inj_start, inj_end):
            rel_t = sec - inj_start
            if perturbation_kind == "ramp":
                current_extra = extra_rate * (rel_t / max(1.0, float(duration_sec)))
            else:
                current_extra = extra_rate

            # Poisson draw for extra arrivals
            if current_extra > 30.0:
                cnt = max(0, int(rng.gauss(current_extra, math.sqrt(current_extra)) + 0.5))
            else:
                cnt = 0
                p = 1.0
                L = math.exp(-current_extra)
                while True:
                    cnt += 1
                    p *= rng.random()
                    if p <= L:
                        cnt -= 1
                        break

            for _ in range(cnt):
                extra_ev = {
                    "timestamp": sec,
                    "method": rng.choice(methods_pool),
                    "route": rng.choice(routes_pool),
                    "client": rng.choice(clients_pool),
                    "status": 200,
                }
                injected_events.append(extra_ev)
                records_added += 1

    # Re-sort stream by timestamp
    injected_events.sort(key=lambda x: x["timestamp"])

    manifest = InjectionManifest(
        scenario_id=f"INJ_{perturbation_kind}_{start_offset_sec}_{duration_sec}",
        perturbation_kind=perturbation_kind,
        start_sec=inj_start,
        end_sec=inj_end,
        duration_sec=duration_sec,
        multiplier=multiplier,
        target_route=target_route,
        target_status=target_status,
        records_added=records_added,
        records_modified=records_modified,
        seed=seed,
    )

    return injected_events, manifest
