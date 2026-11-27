"""Incident merging, shape classification, second-pass evidence collection, and structured evidence separation."""

from __future__ import annotations

import datetime
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pulsewatch.detector import DetectionCandidate
from pulsewatch.dedup import (
    DedupAccounting,
    RetentionLog,
    RetentionMismatch,
    iter_deduplicated,
    iter_replayed,
)
from pulsewatch.ingest import EventRecord, ingest_jsonl_stream
from pulsewatch.stream_io import HashedTextFile
from pulsewatch.temporal import TemporalEngine

VALID_SHAPES = frozenset({
    "isolated_spike",
    "micro_burst",
    "sustained_surge",
    "ramp",
    "level_shift_candidate",
    "unclassified",
})


@dataclass
class ContributorEvidence:
    top_clients: Dict[str, int]
    top_routes: Dict[str, int]
    status_distribution: Dict[str, int]
    concentration_tags: List[str]
    share_basis: str  # "estimated_excess" or "observed_volume"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_clients": self.top_clients,
            "top_routes": self.top_routes,
            "status_distribution": self.status_distribution,
            "concentration_tags": self.concentration_tags,
            "share_basis": self.share_basis,
        }


@dataclass
class ObservedFacts:
    start_sec: int
    end_sec: int
    start_iso: str
    end_iso: str
    duration_sec: int
    peak_sec: int
    peak_1s_rate: int
    total_observed_requests: int
    estimated_excess_requests: int
    top_routes: Dict[str, int]
    status_distribution: Dict[str, int]
    merged_windows_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "duration_sec": self.duration_sec,
            "peak_sec": self.peak_sec,
            "peak_1s_rate": self.peak_1s_rate,
            "total_observed_requests": self.total_observed_requests,
            "estimated_excess_requests": self.estimated_excess_requests,
            "top_routes": self.top_routes,
            "status_distribution": self.status_distribution,
            "merged_windows_count": self.merged_windows_count,
        }


@dataclass
class StatisticalFindings:
    scales_fired: List[int]
    max_z_score: float
    max_elevation_ratio: float
    min_log_p_value: float
    baseline_expected_requests: float
    tail_probability_note: str
    observed_over_expected: Optional[float] = None
    min_p_value: Optional[float] = None
    all_tails_exact: Optional[bool] = None
    max_omitted_mass_bound: Optional[float] = None
    detection_windows: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "scales_fired": self.scales_fired,
            "max_z_score": round(self.max_z_score, 3),
            "max_elevation_ratio": round(self.max_elevation_ratio, 3),
            "min_log_p_value": round(self.min_log_p_value, 4),
            "baseline_expected_requests": round(self.baseline_expected_requests, 2),
            "tail_probability_note": self.tail_probability_note,
        }
        if self.observed_over_expected is not None:
            d["observed_over_expected"] = round(self.observed_over_expected, 3)
        if self.min_p_value is not None:
            d["min_p_value"] = round(self.min_p_value, 6)
        if self.all_tails_exact is not None:
            d["all_tails_exact"] = self.all_tails_exact
        if self.max_omitted_mass_bound is not None:
            d["max_omitted_mass_bound"] = round(self.max_omitted_mass_bound, 6)
        if self.detection_windows is not None:
            d["detection_windows"] = self.detection_windows
        return d


@dataclass
class DescriptivePattern:
    shape: str
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shape": self.shape,
            "rationale": self.rationale,
        }


@dataclass
class InvestigationLead:
    trigger: str
    suggested_action: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger": self.trigger,
            "suggested_action": self.suggested_action,
        }


@dataclass
class Incident:
    incident_id: str
    observed_facts: ObservedFacts
    statistical_findings: StatisticalFindings
    descriptive_pattern: DescriptivePattern
    contributor_evidence: Optional[ContributorEvidence] = None
    investigation_leads: List[InvestigationLead] = field(default_factory=list)
    unknown_causes: str = (
        "Offline synthetic monitoring cannot infer user intent, architectural root cause, "
        "or threat attribution from traffic counts alone."
    )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "incident_id": self.incident_id,
            "observed_facts": self.observed_facts.to_dict(),
            "statistical_findings": self.statistical_findings.to_dict(),
            "descriptive_pattern": self.descriptive_pattern.to_dict(),
            "investigation_leads": [lead.to_dict() for lead in self.investigation_leads],
            "unknown_causes": self.unknown_causes,
        }
        if self.contributor_evidence:
            data["contributor_evidence"] = self.contributor_evidence.to_dict()
        return data


def classify_incident_shape(
    start_sec: int,
    end_sec: int,
    engine: TemporalEngine,
    scales_fired: List[int],
    latest_segment_end_sec: Optional[int] = None
) -> Tuple[str, str]:
    """Classify incident into one of the canonical shapes."""
    duration = max(1, end_sec - start_sec)

    # 1. Micro burst: very short duration (<= 5s)
    if duration <= 5:
        return "micro_burst", f"Transient high-intensity surge lasting {duration}s."

    # 2. Isolated spike: duration <= 25s
    if duration <= 25:
        return "isolated_spike", f"Short, self-contained spike spanning {duration}s."

    # Inspect second-by-second traffic in [start_sec, end_sec)
    second_counts: List[int] = []
    for s in range(start_sec, end_sec):
        cnt = engine.sparse_buckets[s].total_requests if s in engine.sparse_buckets else 0
        second_counts.append(cnt)

    # 3. Level shift candidate: elevation persists right up to segment boundary without recovery
    if latest_segment_end_sec is not None and abs(end_sec - (latest_segment_end_sec + 1)) <= 5:
        if len(second_counts) >= 10:
            tail_mean = sum(second_counts[-5:]) / 5.0
            overall_mean = sum(second_counts) / len(second_counts)
            if tail_mean >= 0.7 * overall_mean:
                return (
                    "level_shift_candidate",
                    "Elevated rate persists to segment boundary without returning to prior baseline."
                )

    # 4. Ramp: progressive increase over time
    if len(second_counts) >= 12:
        k = len(second_counts) // 3
        part1 = sum(second_counts[:k]) / max(1, k)
        part2 = sum(second_counts[k: 2 * k]) / max(1, k)
        part3 = sum(second_counts[2 * k:]) / max(1, len(second_counts) - 2 * k)
        if part1 < part2 < part3 and part3 >= 1.5 * part1:
            return "ramp", "Monotonically increasing request velocity across consecutive intervals."

    # 5. Sustained surge: longer duration (>= 45s)
    if duration >= 45:
        return "sustained_surge", f"Sustained elevated volume lasting {duration}s across multi-scale windows."

    return "unclassified", "General elevation without dominant specialized geometric profile."


class IncidentMerger:
    """Merges overlapping/contiguous candidate windows and extracts localized incident evidence."""

    def __init__(self, merge_gap_sec: int = 5):
        self.merge_gap_sec = merge_gap_sec

    def merge_candidates(
        self,
        candidates: List[DetectionCandidate],
        engine: TemporalEngine,
        median_rate_1s: float,
        latest_segment_end_sec: Optional[int] = None
    ) -> List[Incident]:
        if not candidates:
            return []

        sorted_cands = sorted(candidates, key=lambda c: (c.start_sec, c.end_sec))

        # Group overlapping or nearby candidates
        clusters: List[List[DetectionCandidate]] = []
        current_cluster: List[DetectionCandidate] = [sorted_cands[0]]

        for cand in sorted_cands[1:]:
            cluster_end = max(c.end_sec for c in current_cluster)
            if cand.start_sec <= cluster_end + self.merge_gap_sec:
                current_cluster.append(cand)
            else:
                clusters.append(current_cluster)
                current_cluster = [cand]

        if current_cluster:
            clusters.append(current_cluster)

        incidents: List[Incident] = []
        for idx, cluster in enumerate(clusters, start=1):
            raw_start_sec = min(c.start_sec for c in cluster)
            raw_end_sec = max(c.end_sec for c in cluster)

            # Localize incident boundaries to seconds with meaningful elevation
            elev_thresh = max(median_rate_1s + 2.0, median_rate_1s * 1.5)
            elev_secs = [
                s for s in range(raw_start_sec, raw_end_sec)
                if (engine.sparse_buckets[s].total_requests if s in engine.sparse_buckets else 0) >= elev_thresh
            ]

            if elev_secs:
                start_sec = max(raw_start_sec, min(elev_secs))
                end_sec = min(raw_end_sec, max(elev_secs) + 1)
            else:
                start_sec = raw_start_sec
                end_sec = raw_end_sec

            duration_sec = max(1, end_sec - start_sec)
            scales_fired = sorted(list(set(c.scale_sec for c in cluster)))
            max_z = max(c.z_score for c in cluster)
            max_elev = max(c.elevation_ratio for c in cluster)
            min_log_p = min(c.log_p_value for c in cluster)

            # Facts aggregation from engine
            total_obs = 0
            peak_rate = 0
            peak_sec = start_sec
            s2 = s4 = s5 = s_other = 0
            route_counts: Dict[str, int] = defaultdict(int)

            for s in range(start_sec, end_sec):
                if s in engine.sparse_buckets:
                    b = engine.sparse_buckets[s]
                    cnt = b.total_requests
                    total_obs += cnt
                    if cnt > peak_rate:
                        peak_rate = cnt
                        peak_sec = s
                    s2 += b.status_2xx
                    s4 += b.status_4xx
                    s5 += b.status_5xx
                    s_other += b.status_other
                    for r, c in b.routes.items():
                        route_counts[r] += c

            baseline_expected = median_rate_1s * duration_sec
            excess_requests = max(0, int(total_obs - baseline_expected))

            top_routes = dict(
                sorted(route_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            )

            start_dt = datetime.datetime.fromtimestamp(start_sec, tz=datetime.timezone.utc)
            end_dt = datetime.datetime.fromtimestamp(end_sec, tz=datetime.timezone.utc)

            shape, rationale = classify_incident_shape(
                start_sec=start_sec,
                end_sec=end_sec,
                engine=engine,
                scales_fired=scales_fired,
                latest_segment_end_sec=latest_segment_end_sec,
            )

            observed = ObservedFacts(
                start_sec=start_sec,
                end_sec=end_sec,
                start_iso=start_dt.isoformat(),
                end_iso=end_dt.isoformat(),
                duration_sec=duration_sec,
                peak_sec=peak_sec,
                peak_1s_rate=peak_rate,
                total_observed_requests=total_obs,
                estimated_excess_requests=excess_requests,
                top_routes=top_routes,
                status_distribution={
                    "2xx": s2,
                    "4xx": s4,
                    "5xx": s5,
                    "other": s_other,
                },
                merged_windows_count=len(cluster),
            )

            findings = StatisticalFindings(
                scales_fired=scales_fired,
                max_z_score=max_z,
                max_elevation_ratio=max_elev,
                min_log_p_value=min_log_p,
                baseline_expected_requests=baseline_expected,
                tail_probability_note=(
                    "PW-Scan probability evaluation under Poisson/Negative-Binomial dispersion model. "
                    "Reported upper-tail probabilities are bounded empirical tail assessments."
                ),
                observed_over_expected=total_obs / max(0.1, baseline_expected),
                min_p_value=min(c.p_value for c in cluster),
                all_tails_exact=all(c.tail_exact for c in cluster),
                max_omitted_mass_bound=max(c.omitted_mass_bound for c in cluster),
                detection_windows=[c.to_dict() for c in sorted(cluster, key=lambda c: -c.z_score)[:40]],
            )

            pattern = DescriptivePattern(shape=shape, rationale=rationale)

            incidents.append(
                Incident(
                    incident_id=f"INC-{idx:03d}",
                    observed_facts=observed,
                    statistical_findings=findings,
                    descriptive_pattern=pattern,
                )
            )

        return incidents


def compute_file_sha256(file_path: str) -> str:
    """Compute SHA-256 hash of file content to ensure unchanged input."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


SECOND_PASS_OK = "ok"
SECOND_PASS_NO_INCIDENTS = "no_incidents"
SECOND_PASS_INPUT_CHANGED = "input_changed"


def collect_second_pass_evidence(
    incidents: List[Incident],
    input_file: str,
    expected_file_hash: Optional[str] = None,
    retention: Optional[RetentionLog] = None
) -> str:
    """Bounded second pass over unchanged input to collect contributor evidence and concentration tags.

    The pass re-reads the source rather than retaining a second copy of the events. To keep
    the evidence consistent with the incident totals (which come from the deduplicated
    engine), only records the first pass retained are counted. If ``retention`` (the first
    pass's retain/drop log) is supplied, those decisions are replayed exactly; otherwise a
    fresh deduplicator is run, which is deterministic over the same byte stream and so
    retains the same records.

    The input's SHA-256 is computed *while* reading. If ``expected_file_hash`` is given and
    the bytes read differ, no evidence is attached and ``SECOND_PASS_INPUT_CHANGED`` is
    returned, so evidence from a different version of the source is never mixed into the
    report. Read errors (``OSError``) propagate to the caller.

    Returns one of ``SECOND_PASS_OK``, ``SECOND_PASS_NO_INCIDENTS`` or
    ``SECOND_PASS_INPUT_CHANGED``.
    """
    if not incidents:
        return SECOND_PASS_NO_INCIDENTS

    # Map intervals to incidents
    intervals = [(inc.observed_facts.start_sec, inc.observed_facts.end_sec, inc) for inc in incidents]

    incident_clients: Dict[str, Dict[str, int]] = {inc.incident_id: defaultdict(int) for inc in incidents}
    incident_routes: Dict[str, Dict[str, int]] = {inc.incident_id: defaultdict(int) for inc in incidents}
    incident_statuses: Dict[str, Dict[str, int]] = {inc.incident_id: defaultdict(int) for inc in incidents}

    try:
        with HashedTextFile(input_file) as src:
            valid_records = ingest_jsonl_stream(src.stream)
            if retention is not None:
                retained = iter_replayed(valid_records, retention)
            else:
                # throwaway accounting; the first pass's accounting is authoritative
                retained = iter_deduplicated(valid_records, DedupAccounting())
            for rec in retained:
                t = rec.timestamp_sec
                for start, end, inc in intervals:
                    if start <= t < end:
                        iid = inc.incident_id
                        incident_clients[iid][rec.client] += 1
                        incident_routes[iid][rec.route] += 1
                        status_group = f"{rec.status // 100}xx"
                        incident_statuses[iid][status_group] += 1
            observed_hash = src.sha256_hexdigest()
    except RetentionMismatch:
        return SECOND_PASS_INPUT_CHANGED

    if expected_file_hash and observed_hash != expected_file_hash:
        return SECOND_PASS_INPUT_CHANGED

    for inc in incidents:
        iid = inc.incident_id
        clients = incident_clients[iid]
        routes = incident_routes[iid]
        statuses = incident_statuses[iid]

        total_obs = max(1, inc.observed_facts.total_observed_requests)
        tags: List[str] = []

        # Top client concentration check
        if clients:
            top_client_count = max(clients.values())
            if top_client_count / float(total_obs) >= 0.60:
                tags.append("single_client_dominated")
            elif len(clients) >= 5 and (top_client_count / float(total_obs)) <= 0.25:
                tags.append("broad_based")

        # Route concentration check
        if routes:
            top_route_count = max(routes.values())
            if top_route_count / float(total_obs) >= 0.80:
                tags.append("route_concentrated")

        # Status shift check (error surge)
        error_count = statuses.get("4xx", 0) + statuses.get("5xx", 0)
        if error_count / float(total_obs) >= 0.40:
            tags.append("status_shifted")

        top_c = dict(sorted(clients.items(), key=lambda x: x[1], reverse=True)[:5])
        top_r = dict(sorted(routes.items(), key=lambda x: x[1], reverse=True)[:5])

        inc.contributor_evidence = ContributorEvidence(
            top_clients=top_c,
            top_routes=top_r,
            status_distribution=dict(statuses),
            concentration_tags=tags,
            share_basis="estimated_excess" if inc.observed_facts.estimated_excess_requests > 0 else "observed_volume",
        )

        # Deterministic operator investigation leads
        leads: List[InvestigationLead] = []
        if "single_client_dominated" in tags and top_c:
            top_client_name = next(iter(top_c.keys()))
            leads.append(InvestigationLead(
                trigger=f"Single client '{top_client_name}' generated {max(clients.values())}/{total_obs} ({max(clients.values())/total_obs*100:.1f}%) requests",
                suggested_action=f"Inspect client '{top_client_name}' synthetic load script, concurrency parameters, and retry backoff logic."
            ))
        elif "broad_based" in tags:
            leads.append(InvestigationLead(
                trigger=f"Dispersed load across {len(clients)} distinct clients without a single dominating caller",
                suggested_action="Check distributed load-test orchestration, multi-client test harness, or shared environment deployment."
            ))

        if "route_concentrated" in tags and top_r:
            top_route_name = next(iter(top_r.keys()))
            leads.append(InvestigationLead(
                trigger=f"Route '{top_route_name}' received {max(routes.values())}/{total_obs} ({max(routes.values())/total_obs*100:.1f}%) requests",
                suggested_action=f"Investigate endpoint '{top_route_name}' capacity, database query efficiency, and upstream dependency health."
            ))

        if "status_shifted" in tags:
            leads.append(InvestigationLead(
                trigger=f"Elevated failure proportion ({error_count}/{total_obs} requests, {error_count/total_obs*100:.1f}% 4xx/5xx)",
                suggested_action="Examine gateway error responses for rate limiting (429), gateway timeouts (504), or authentication failure spikes (401/403)."
            ))

        if inc.descriptive_pattern.shape == "level_shift_candidate":
            leads.append(InvestigationLead(
                trigger="Traffic elevation persisted to segment boundary without recovery to prior baseline",
                suggested_action="Confirm whether an intentional service scaling step occurred or collect extended steady-state observations."
            ))
        elif inc.descriptive_pattern.shape == "ramp":
            leads.append(InvestigationLead(
                trigger="Progressive velocity increase across consecutive sub-intervals",
                suggested_action="Check load-generator ramp-up profile or runaway client retry loop."
            ))

        inc.investigation_leads = leads

    return SECOND_PASS_OK
