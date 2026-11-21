"""Deterministic synthetic API traffic generator for evaluation and testing."""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class GroundTruthIncident:
    start_sec: int
    end_sec: int
    shape: str
    description: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GroundTruth:
        incidents = [GroundTruthIncident(**inc) for inc in data.get("incidents", [])]
        return cls(
            scenario_name=data["scenario_name"],
            total_records=data["total_records"],
            total_span_sec=data["total_span_sec"],
            expected_duplicates=data["expected_duplicates"],
            expected_unverified=data["expected_unverified"],
            expected_gaps=[tuple(g) for g in data["expected_gaps"]],
            incidents=incidents,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GroundTruth:
    scenario_name: str
    total_records: int
    total_span_sec: int
    expected_duplicates: int
    expected_unverified: int
    expected_gaps: List[Tuple[int, int]]
    incidents: List[GroundTruthIncident] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GroundTruth:
        incidents = [GroundTruthIncident(**inc) for inc in data.get("incidents", [])]
        return cls(
            scenario_name=data["scenario_name"],
            total_records=data["total_records"],
            total_span_sec=data["total_span_sec"],
            expected_duplicates=data["expected_duplicates"],
            expected_unverified=data["expected_unverified"],
            expected_gaps=[tuple(g) for g in data["expected_gaps"]],
            incidents=incidents,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "total_records": self.total_records,
            "total_span_sec": self.total_span_sec,
            "expected_duplicates": self.expected_duplicates,
            "expected_unverified": self.expected_unverified,
            "expected_gaps": self.expected_gaps,
            "incidents": [inc.to_dict() for inc in self.incidents],
        }


ROUTES = [
    "/api/v1/auth/login",
    "/api/v1/items",
    "/api/v1/items/search",
    "/api/v1/checkout",
    "/api/v1/user/profile",
]

CLIENTS = [f"client-{i:03d}" for i in range(1, 21)]


class SyntheticTrafficGenerator:
    """Generates reproducible, labeled synthetic API traffic datasets."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def generate_benchmark_suite(
        self,
        base_time: int = 1795237200
    ) -> Tuple[List[Dict[str, Any]], GroundTruth]:
        """Generate a comprehensive multi-scenario dataset with ground truth."""
        records: List[Dict[str, Any]] = []
        incidents: List[GroundTruthIncident] = []
        expected_dups = 0
        expected_unverified = 0
        gaps: List[Tuple[int, int]] = []

        curr_t = base_time
        seq_num = 1

        # Phase 1: Clean Baseline (300 seconds, ~2 req/s)
        for s in range(300):
            t = curr_t + s
            # 1 to 3 requests per second
            cnt = self.rng.randint(1, 3)
            for _ in range(cnt):
                records.append({
                    "timestamp": t,
                    "method": "GET",
                    "route": self.rng.choice(ROUTES),
                    "client": self.rng.choice(CLIENTS),
                    "status": 200,
                    "source": "gw-us-east",
                    "seq": seq_num,
                })
                seq_num += 1
        curr_t += 300

        # Phase 2: Isolated Spike with duplicates (t = curr_t .. curr_t + 15)
        spike_start = curr_t
        spike_duration = 15
        for s in range(spike_duration):
            t = curr_t + s
            # Surge of 35 req/s
            for _ in range(35):
                rec = {
                    "timestamp": t,
                    "method": "POST",
                    "route": "/api/v1/checkout",
                    "client": "client-bad-actor",
                    "status": 429 if s > 5 else 200,
                    "source": "gw-us-east",
                    "seq": seq_num,
                }
                records.append(rec)
                seq_num += 1

                # Inject deliberate duplicates
                if self.rng.random() < 0.20:
                    records.append(dict(rec))
                    expected_dups += 1

        incidents.append(
            GroundTruthIncident(
                start_sec=spike_start,
                end_sec=spike_start + spike_duration,
                shape="isolated_spike",
                description="Injected 15s checkout spike with duplicates"
            )
        )
        curr_t += spike_duration

        # Phase 3: Return to Normal Baseline (120s)
        for s in range(120):
            t = curr_t + s
            cnt = self.rng.randint(1, 3)
            for _ in range(cnt):
                records.append({
                    "timestamp": t,
                    "method": "GET",
                    "route": self.rng.choice(ROUTES),
                    "client": self.rng.choice(CLIENTS),
                    "status": 200,
                    "source": "gw-us-east",
                    "seq": seq_num,
                })
                seq_num += 1
        curr_t += 120

        # Phase 4: Long Unknown Gap (60s completely missing)
        gap_start = curr_t
        curr_t += 60  # Skip 60 seconds
        gaps.append((gap_start, curr_t - 1))

        # Phase 5: Sustained Surge post-gap (90 seconds, ~20 req/s)
        surge_start = curr_t
        surge_duration = 90
        for s in range(surge_duration):
            t = curr_t + s
            cnt = self.rng.randint(18, 24)
            for _ in range(cnt):
                # Include distinct repeated requests (same content, different valid IDs)
                records.append({
                    "timestamp": t,
                    "method": "GET",
                    "route": "/api/v1/items/search",
                    "client": self.rng.choice(CLIENTS),
                    "status": 200,
                    "source": "gw-us-east",
                    "seq": seq_num,
                })
                seq_num += 1

                # Inject identity-stripped requests
                if self.rng.random() < 0.05:
                    records.append({
                        "timestamp": t,
                        "method": "GET",
                        "route": "/api/v1/items/search",
                        "client": "client-anonymous",
                        "status": 200,
                    })
                    expected_unverified += 1

        incidents.append(
            GroundTruthIncident(
                start_sec=surge_start,
                end_sec=surge_start + surge_duration,
                shape="sustained_surge",
                description="Injected 90s sustained surge with anonymous requests"
            )
        )
        curr_t += surge_duration

        # Phase 6: Cooldown baseline (60s)
        for s in range(60):
            t = curr_t + s
            cnt = self.rng.randint(1, 3)
            for _ in range(cnt):
                records.append({
                    "timestamp": t,
                    "method": "GET",
                    "route": self.rng.choice(ROUTES),
                    "client": self.rng.choice(CLIENTS),
                    "status": 200,
                    "source": "gw-us-east",
                    "seq": seq_num,
                })
                seq_num += 1
        curr_t += 60

        total_span = curr_t - base_time
        gt = GroundTruth(
            scenario_name="benchmark_suite_v1",
            total_records=len(records),
            total_span_sec=total_span,
            expected_duplicates=expected_dups,
            expected_unverified=expected_unverified,
            expected_gaps=gaps,
            incidents=incidents,
        )

        return records, gt


def write_synthetic_dataset(
    jsonl_path: str,
    ground_truth_path: str,
    seed: int = 42
) -> GroundTruth:
    """Generate and write synthetic test dataset and ground truth JSON files."""
    gen = SyntheticTrafficGenerator(seed=seed)
    records, gt = gen.generate_benchmark_suite()

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    with open(ground_truth_path, "w", encoding="utf-8") as f:
        json.dump(gt.to_dict(), f, indent=2)

    return gt
