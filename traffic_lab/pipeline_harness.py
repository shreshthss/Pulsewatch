"""PulseWatch Pipeline Harness for ablation experiments and controlled benchmarking.

Provides a parameterizable wrapper around the end-to-end detection pipeline
allowing isolated toggle of individual architectural components:
1. deduplication (dedup_enabled)
2. coverage-gap isolation (coverage_aware)
3. dispersion scaling (dispersion_scaling)
4. sticky contamination masking (masking_enabled)
5. multi-scale scanning (scales)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pulsewatch.dedup import deduplicate_records
from pulsewatch.detector import (
    BaselineProfile,
    DetectionCandidate,
    DispersionProfile,
    PWScanDetector,
    SCALES_SEC,
)
from pulsewatch.incident import Incident, IncidentMerger
from pulsewatch.ingest import EventRecord, parse_record_dict
from pulsewatch.temporal import ContinuousSegment, CoverageReport, TemporalEngine


@dataclass
class HarnessConfig:
    dedup_enabled: bool = True
    coverage_aware: bool = True
    dispersion_scaling: bool = True
    masking_enabled: bool = True
    scales: List[int] = field(default_factory=lambda: list(SCALES_SEC))
    z_threshold: Optional[float] = None


class PipelineHarness:
    """Configurable execution harness for controlled experiments and ablations."""

    def __init__(self, config: Optional[HarnessConfig] = None):
        self.config = config or HarnessConfig()

    def run_on_events(
        self,
        raw_events: Sequence[Dict[str, Any]],
        declared_coverage: Optional[Sequence[Tuple[int, int]]] = None,
    ) -> List[Incident]:
        """Execute the pipeline on an in-memory event sequence with harness toggles applied."""
        # 1. Parsing
        from pulsewatch.ingest import IngestAccounting
        acc = IngestAccounting()
        valid_records: List[EventRecord] = []
        for idx, ev in enumerate(raw_events):
            rec = parse_record_dict(ev, line_num=idx + 1, accounting=acc)
            if isinstance(rec, EventRecord):
                valid_records.append(rec)

        # 2. Deduplication Toggle
        if self.config.dedup_enabled:
            retained_records, dedup_acc = deduplicate_records(valid_records)
        else:
            retained_records = valid_records
            dedup_acc = None

        # 3. Temporal Aggregation
        engine = TemporalEngine()
        if declared_coverage:
            engine.set_declared_coverage(declared_coverage)
        engine.ingest_records(retained_records)

        # 4. Coverage and Segments Toggle
        gaps, segments = engine.compute_gaps_and_segments()
        coverage = engine.compute_coverage()

        if not self.config.coverage_aware:
            # Blind to gaps: treat span as single continuous segment with 0 unknown gaps
            if engine.earliest_sec is not None and engine.latest_sec is not None:
                segments = [ContinuousSegment(start_sec=engine.earliest_sec, end_sec=engine.latest_sec + 1)]
                span = (engine.latest_sec - engine.earliest_sec + 1)
                coverage = CoverageReport(
                    earliest_sec=engine.earliest_sec,
                    latest_sec=engine.latest_sec,
                    total_span_sec=span,
                    active_seconds=sum(1 for _ in engine.sparse_buckets),
                    known_zero_seconds=0,
                    assumed_zero_seconds=0,
                    unknown_gap_seconds=0,
                    coverage_ratio=1.0,
                    unknown_gaps=[],
                )

        # 5. Detector & Masking Toggle
        screening_z = 4.0 if self.config.masking_enabled else float("inf")
        detector = PWScanDetector(
            screening_z_threshold=screening_z,
            z_threshold=self.config.z_threshold,
        )

        baseline = detector.fit_baseline_profile(engine, coverage, segments, dedup_acc)

        # 6. Dispersion Scaling Toggle
        if not self.config.dispersion_scaling:
            # Force phi = 1.0 (Poisson assumption) across all scales
            for scale in baseline.dispersion_by_scale:
                baseline.dispersion_by_scale[scale] = DispersionProfile(
                    scale_sec=scale,
                    phi=1.0,
                    is_poisson=True,
                )

        # 7. Multi-scale Scan Toggle
        import pulsewatch.detector
        old_scales = pulsewatch.detector.SCALES_SEC
        try:
            pulsewatch.detector.SCALES_SEC = list(self.config.scales)
            candidates = detector.scan(engine, coverage, segments, baseline)
        finally:
            pulsewatch.detector.SCALES_SEC = old_scales

        # 8. Incident Merging
        merger = IncidentMerger()
        incidents = merger.merge_candidates(candidates, engine, median_rate_1s=baseline.fitted_rate_1s)
        return incidents
