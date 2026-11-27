"""Temporal engine: 1-second sparse aggregation, coverage tracking, and windowing."""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from pulsewatch.ingest import EventRecord

DEFAULT_GAP_THRESHOLD_SEC = 900  # Researched 900s (15 min) default


@dataclass
class SecondBucket:
    timestamp_sec: int
    total_requests: int = 0
    status_2xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    status_other: int = 0
    routes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class Gap:
    start_sec: int
    end_sec: int
    duration_sec: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "duration_sec": self.duration_sec,
            "start_iso": datetime.datetime.fromtimestamp(self.start_sec, tz=datetime.timezone.utc).isoformat(),
            "end_iso": datetime.datetime.fromtimestamp(self.end_sec, tz=datetime.timezone.utc).isoformat(),
        }


@dataclass
class ContinuousSegment:
    """A span of continuous observation without unknown gaps."""
    start_sec: int
    end_sec: int  # inclusive

    @property
    def duration_sec(self) -> int:
        return max(0, self.end_sec - self.start_sec + 1)


@dataclass
class WindowSummary60s:
    start_sec: int
    end_sec: int  # exclusive: [start_sec, end_sec)
    start_iso: str
    end_iso: str
    total_requests: int
    active_seconds_count: int
    known_zeros_count: int
    assumed_zeros_count: int
    status_2xx: int
    status_4xx: int
    status_5xx: int
    top_routes: Dict[str, int]
    is_complete: bool
    is_boundary: bool
    contains_gap: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "total_requests": self.total_requests,
            "active_seconds_count": self.active_seconds_count,
            "known_zeros_count": self.known_zeros_count,
            "assumed_zeros_count": self.assumed_zeros_count,
            "status_2xx": self.status_2xx,
            "status_4xx": self.status_4xx,
            "status_5xx": self.status_5xx,
            "top_routes": self.top_routes,
            "is_complete": self.is_complete,
            "is_boundary": self.is_boundary,
            "contains_gap": self.contains_gap,
        }


@dataclass
class CoverageReport:
    earliest_sec: Optional[int]
    latest_sec: Optional[int]
    total_span_sec: int
    active_seconds: int
    known_zero_seconds: int = 0
    assumed_zero_seconds: int = 0
    unknown_gap_seconds: int = 0
    coverage_ratio: float = 0.0
    gap_threshold_sec: int = DEFAULT_GAP_THRESHOLD_SEC
    unknown_gaps: List[Gap] = field(default_factory=list)
    declared_coverage_intervals: List[Tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "earliest_sec": self.earliest_sec,
            "latest_sec": self.latest_sec,
            "total_span_sec": self.total_span_sec,
            "active_seconds": self.active_seconds,
            "known_zero_seconds": self.known_zero_seconds,
            "assumed_zero_seconds": self.assumed_zero_seconds,
            "unknown_gap_seconds": self.unknown_gap_seconds,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "gap_threshold_sec": self.gap_threshold_sec,
            "unknown_gaps_count": len(self.unknown_gaps),
            "unknown_gaps": [g.to_dict() for g in self.unknown_gaps[:20]],
            "declared_coverage_count": len(self.declared_coverage_intervals),
        }


class TemporalEngine:
    """Manages sparse 1s time aggregation, coverage boundaries, and multi-scale windowing."""

    def __init__(
        self,
        gap_threshold_sec: int = DEFAULT_GAP_THRESHOLD_SEC,
        declared_coverage_intervals: Optional[List[Tuple[int, int]]] = None
    ):
        self.gap_threshold_sec = gap_threshold_sec
        self.declared_coverage = list(declared_coverage_intervals or [])
        # Sparse dictionary: sec -> SecondBucket (O(events), never O(span))
        self.sparse_buckets: Dict[int, SecondBucket] = {}
        self.earliest_sec: Optional[int] = None
        self.latest_sec: Optional[int] = None
        # Canonical route strings. json.loads yields a fresh str per record, and each bucket's
        # dict would otherwise keep its own copy of every route it sees. Pooling shares one
        # object per distinct route across all buckets; it changes no value or ordering.
        self._route_pool: Dict[str, str] = {}

    def ingest_record(self, record: EventRecord) -> None:
        t = record.timestamp_sec
        if self.earliest_sec is None or t < self.earliest_sec:
            self.earliest_sec = t
        if self.latest_sec is None or t > self.latest_sec:
            self.latest_sec = t

        if t not in self.sparse_buckets:
            self.sparse_buckets[t] = SecondBucket(timestamp_sec=t)

        bucket = self.sparse_buckets[t]
        bucket.total_requests += 1

        status = record.status
        if 200 <= status <= 299:
            bucket.status_2xx += 1
        elif 400 <= status <= 499:
            bucket.status_4xx += 1
        elif 500 <= status <= 599:
            bucket.status_5xx += 1
        else:
            bucket.status_other += 1

        route = record.route
        bucket.routes[self._route_pool.setdefault(route, route)] += 1

    def ingest_records(self, records: Iterable[EventRecord]) -> None:
        """Fold records into the sparse buckets one at a time.

        Accepts any iterable, including a one-shot generator; no record is retained.
        Aggregation is order-independent (counts plus min/max), so streaming order does
        not affect the result.
        """
        for r in records:
            self.ingest_record(r)

    def set_declared_coverage(self, intervals: Iterable[Tuple[int, int]]) -> None:
        """Set declared coverage intervals after (or between) ingestion.

        Coverage control records may appear anywhere in the input, so a streaming caller
        only knows the complete list once the stream is exhausted. The constructor
        copies its argument, which would freeze whatever was known at construction time;
        this method replaces that copy. Coverage is only consulted by
        ``compute_coverage``, ``compute_gaps_and_segments`` and
        ``generate_60s_windows``, so it must be set before those are called.
        """
        self.declared_coverage = list(intervals)

    def is_in_declared_coverage(self, sec: int) -> bool:
        """Check if a second is covered by explicitly declared coverage intervals."""
        for start, end in self.declared_coverage:
            if start <= sec <= end:
                return True
        return False

    def compute_gaps_and_segments(self) -> Tuple[List[Gap], List[ContinuousSegment]]:
        """Identify unknown gaps and partition timeline into continuous segments."""
        if not self.sparse_buckets or self.earliest_sec is None or self.latest_sec is None:
            return [], []

        sorted_secs = sorted(self.sparse_buckets.keys())
        gaps: List[Gap] = []
        segments: List[ContinuousSegment] = []

        seg_start = sorted_secs[0]
        prev_sec = sorted_secs[0]

        for s in sorted_secs[1:]:
            diff = s - prev_sec
            # If gap is outside declared coverage and exceeds gap_threshold_sec
            if diff > self.gap_threshold_sec and not (
                self.is_in_declared_coverage(prev_sec + 1) and self.is_in_declared_coverage(s - 1)
            ):
                segments.append(ContinuousSegment(seg_start, prev_sec))
                gap_start = prev_sec + 1
                gap_end = s - 1
                gaps.append(Gap(gap_start, gap_end, gap_end - gap_start + 1))
                seg_start = s
            prev_sec = s

        segments.append(ContinuousSegment(seg_start, prev_sec))
        return gaps, segments

    def compute_coverage(self) -> CoverageReport:
        effective_earliest = self.earliest_sec
        effective_latest = self.latest_sec
        if self.declared_coverage:
            decl_min = min(c[0] for c in self.declared_coverage)
            decl_max = max(c[1] for c in self.declared_coverage)
            effective_earliest = decl_min if effective_earliest is None else min(effective_earliest, decl_min)
            effective_latest = decl_max if effective_latest is None else max(effective_latest, decl_max)

        if effective_earliest is None or effective_latest is None:
            return CoverageReport(
                earliest_sec=None,
                latest_sec=None,
                total_span_sec=0,
                active_seconds=0,
                known_zero_seconds=0,
                assumed_zero_seconds=0,
                unknown_gap_seconds=0,
                coverage_ratio=0.0,
                gap_threshold_sec=self.gap_threshold_sec,
                unknown_gaps=[],
                declared_coverage_intervals=self.declared_coverage,
            )

        total_span = max(1, effective_latest - effective_earliest + 1)
        active_secs = len(self.sparse_buckets)
        gaps, segments = self.compute_gaps_and_segments()
        gap_secs = sum(g.duration_sec for g in gaps)

        # Distinguish known zero vs assumed zero:
        known_zeros = 0
        for c_start, c_end in self.declared_coverage:
            act_in_range = sum(1 for s in self.sparse_buckets if c_start <= s <= c_end)
            known_zeros += max(0, (c_end - c_start + 1) - act_in_range)

        assumed_zeros = max(0, total_span - active_secs - gap_secs - known_zeros)
        coverage_ratio = max(0.0, min(1.0, (total_span - gap_secs) / float(total_span)))

        return CoverageReport(
            earliest_sec=effective_earliest,
            latest_sec=effective_latest,
            total_span_sec=total_span,
            active_seconds=active_secs,
            known_zero_seconds=known_zeros,
            assumed_zero_seconds=assumed_zeros,
            unknown_gap_seconds=gap_secs,
            coverage_ratio=coverage_ratio,
            gap_threshold_sec=self.gap_threshold_sec,
            unknown_gaps=gaps,
            declared_coverage_intervals=self.declared_coverage,
        )

    def generate_60s_windows(self) -> List[WindowSummary60s]:
        """Produce primary UTC-aligned 60-second window summaries."""
        if self.earliest_sec is None or self.latest_sec is None:
            return []

        gaps, _ = self.compute_gaps_and_segments()
        gap_intervals = [(g.start_sec, g.end_sec) for g in gaps]

        first_window_start = (self.earliest_sec // 60) * 60
        last_window_start = (self.latest_sec // 60) * 60

        windows: List[WindowSummary60s] = []

        w_start = first_window_start
        while w_start <= last_window_start:
            w_end = w_start + 60

            # Check boundary conditions
            is_boundary = False
            if w_start < self.earliest_sec or (w_end - 1) > self.latest_sec:
                is_boundary = True

            # Check overlap with unknown gaps
            contains_gap = any(
                not (g_end < w_start or g_start >= w_end)
                for g_start, g_end in gap_intervals
            )

            is_complete = (not is_boundary) and (not contains_gap)

            total_reqs = 0
            active_secs = 0
            known_zeros = 0
            assumed_zeros = 0
            s2 = 0
            s4 = 0
            s5 = 0
            routes_map: Dict[str, int] = defaultdict(int)

            for sec in range(w_start, w_end):
                if sec in self.sparse_buckets:
                    b = self.sparse_buckets[sec]
                    active_secs += 1
                    total_reqs += b.total_requests
                    s2 += b.status_2xx
                    s4 += b.status_4xx
                    s5 += b.status_5xx
                    for r_name, r_cnt in b.routes.items():
                        routes_map[r_name] += r_cnt
                elif not contains_gap:
                    if self.is_in_declared_coverage(sec):
                        known_zeros += 1
                    else:
                        assumed_zeros += 1

            top_routes = dict(
                sorted(routes_map.items(), key=lambda item: item[1], reverse=True)[:5]
            )

            start_dt = datetime.datetime.fromtimestamp(w_start, tz=datetime.timezone.utc)
            end_dt = datetime.datetime.fromtimestamp(w_end, tz=datetime.timezone.utc)

            windows.append(
                WindowSummary60s(
                    start_sec=w_start,
                    end_sec=w_end,
                    start_iso=start_dt.isoformat(),
                    end_iso=end_dt.isoformat(),
                    total_requests=total_reqs,
                    active_seconds_count=active_secs,
                    known_zeros_count=known_zeros,
                    assumed_zeros_count=assumed_zeros,
                    status_2xx=s2,
                    status_4xx=s4,
                    status_5xx=s5,
                    top_routes=top_routes,
                    is_complete=is_complete,
                    is_boundary=is_boundary,
                    contains_gap=contains_gap,
                )
            )

            w_start += 60

        return windows
