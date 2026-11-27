"""Regression tests for the streaming ingest -> dedup -> temporal handoff.

Covers: iterator-based deduplication semantics, one-shot generator behaviour,
equivalence with the previous materialising flow, declared-coverage ordering,
second-pass evidence consistency, input-change detection, read-error handling,
and a deterministic proof that retained events are not accumulated.
"""

import contextlib
import io
import json
import os
import random
import tempfile
import unittest
import warnings
import weakref
from unittest import mock

from pulsewatch import cli as cli_module
from pulsewatch.cli import _stream_into_engine, main, run_pipeline
from pulsewatch.dedup import (
    DedupAccounting,
    RetentionLog,
    RetentionMismatch,
    deduplicate_records,
    iter_deduplicated,
    iter_replayed,
)
from pulsewatch.detector import PWScanDetector
from pulsewatch.incident import (
    SECOND_PASS_INPUT_CHANGED,
    SECOND_PASS_NO_INCIDENTS,
    SECOND_PASS_OK,
    IncidentMerger,
    collect_second_pass_evidence,
    compute_file_sha256,
)
from pulsewatch.ingest import (
    EventIdentity,
    EventRecord,
    IngestAccounting,
    ingest_jsonl_stream,
)
from pulsewatch.report import generate_canonical_report
from pulsewatch.stream_io import HashedTextFile
from pulsewatch.temporal import DEFAULT_GAP_THRESHOLD_SEC, TemporalEngine


# --------------------------------------------------------------------------- helpers
def rec(ts, route="/a", client="c1", status=200, **identity):
    return EventRecord(ts, "GET", route, client, status, EventIdentity(**identity))


def build_mixed_lines(seed=11, n=600):
    """Deterministic JSONL lines covering every identity level and every awkward case."""
    rng = random.Random(seed)
    lines = []
    t = 1_700_000_000
    for i in range(n):
        t += int(rng.random() * 3)
        kind = rng.random()
        ev = {
            "timestamp": t, "method": "GET",
            "route": "/r%d" % int(rng.random() * 6),
            "client": "c%d" % int(rng.random() * 9),
            "status": [200, 200, 200, 404, 503][int(rng.random() * 5)],
        }
        if kind < 0.35:
            ev.update(source="gw1", seq=i)                              # level 1: (source, seq)
        elif kind < 0.5:
            ev.update(source="gw2", trace_id="t%d" % i, span_id="s%d" % i)  # level 2
        elif kind < 0.65:
            ev.update(source="gw3", request_id="r%d" % i)               # level 3
        # else: no usable identity
        line = json.dumps(ev)
        lines.append(line)
        u = rng.random()
        if "source" in ev and u < 0.15:
            lines.append(line)                                          # exact replay
        elif "source" in ev and u < 0.20:
            c = dict(ev)
            c["status"] = 500 if ev["status"] != 500 else 200
            lines.append(json.dumps(c))                                 # identity conflict
        elif u < 0.23:
            lines.append("{broken")                                     # malformed JSON
        elif u < 0.25:
            lines.append("")                                            # blank line
        elif u < 0.28 and len(lines) > 3:
            lines[-1], lines[-2] = lines[-2], lines[-1]                 # local out-of-order
        if i == n // 2:
            lines.append(json.dumps({"_pw": "coverage", "start": t - 100, "end": t + 50}))
    # Coverage declared only AFTER every event: the engine can only learn it post-exhaustion.
    lines.append(json.dumps({"_pw": "coverage", "start": t - 30, "end": t + 400}))
    return lines


def engine_state(e):
    gaps, segs = e.compute_gaps_and_segments()
    return {
        "buckets": {
            t: (b.total_requests, b.status_2xx, b.status_4xx, b.status_5xx, b.status_other, dict(b.routes))
            for t, b in e.sparse_buckets.items()
        },
        "earliest": e.earliest_sec, "latest": e.latest_sec, "declared": list(e.declared_coverage),
        "coverage": e.compute_coverage().to_dict(),
        "gaps": [g.to_dict() for g in gaps],
        "segments": [(s.start_sec, s.end_sec) for s in segs],
        "windows": [w.to_dict() for w in e.generate_60s_windows()],
    }


def legacy_state(path):
    """The previous flow: materialise the retained list, then build the engine from it."""
    ingest_acc = IngestAccounting()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        retained, dedup_acc = deduplicate_records(ingest_jsonl_stream(f, ingest_acc))
    engine = TemporalEngine(declared_coverage_intervals=ingest_acc.declared_coverage_intervals)
    engine.ingest_records(iter(retained))
    return engine, ingest_acc, dedup_acc


def legacy_run_pipeline(input_path, gap_threshold=DEFAULT_GAP_THRESHOLD_SEC):
    """The previous run_pipeline control flow, built from public functions."""
    file_hash = compute_file_sha256(input_path)
    engine, ingest_acc, dedup_acc = legacy_state(input_path)
    coverage = engine.compute_coverage()
    _gaps, segments = engine.compute_gaps_and_segments()
    windows_60s = engine.generate_60s_windows()
    detector = PWScanDetector()
    baseline = detector.fit_baseline_profile(engine, coverage, segments, dedup_acc)
    candidates = detector.scan(engine, coverage, segments, baseline)
    incidents = IncidentMerger().merge_candidates(
        candidates, engine, baseline.fitted_rate_1s,
        latest_segment_end_sec=segments[-1].end_sec if segments else None,
    )
    if incidents:
        collect_second_pass_evidence(incidents, input_path, file_hash)
    return generate_canonical_report(
        input_file=input_path, input_sha256=file_hash, ingest_acc=ingest_acc,
        dedup_acc=dedup_acc, coverage=coverage, windows_60s=windows_60s,
        baseline=baseline, candidates=candidates, incidents=incidents,
    )


def canon(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"))


def write_burst_fixture(path, dup_times):
    """120 s of 1 req/s baseline with exact replays at ``dup_times``, plus 40 distinct requests at t=1050."""
    with open(path, "w", encoding="utf-8") as f:
        for t in range(1000, 1120):
            base = {"timestamp": t, "method": "GET", "route": "/api/v1/users", "client": "10.0.0.1",
                    "status": 200, "source": "proxy-1", "seq": t}
            f.write(json.dumps(base) + "\n")
            if t in dup_times:
                f.write(json.dumps(base) + "\n")
        for i in range(40):
            f.write(json.dumps({"timestamp": 1050, "method": "POST", "route": "/api/v1/login",
                                "client": "192.168.1.%d" % i, "status": 401,
                                "source": "proxy-1", "seq": 20000 + i}) + "\n")


def incidents_for(path):
    engine, _ingest_acc, dedup_acc, file_hash, retention = _stream_into_engine(path, DEFAULT_GAP_THRESHOLD_SEC)
    coverage = engine.compute_coverage()
    _gaps, segments = engine.compute_gaps_and_segments()
    detector = PWScanDetector()
    baseline = detector.fit_baseline_profile(engine, coverage, segments, dedup_acc)
    candidates = detector.scan(engine, coverage, segments, baseline)
    incidents = IncidentMerger().merge_candidates(
        candidates, engine, baseline.fitted_rate_1s,
        latest_segment_end_sec=segments[-1].end_sec if segments else None,
    )
    return incidents, file_hash, retention


class TmpDirTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)


# --------------------------------------------------------------------------- iter_deduplicated
class TestIterDeduplicated(unittest.TestCase):
    def _records(self):
        return list(ingest_jsonl_stream(io.StringIO("\n".join(build_mixed_lines()) + "\n"), IngestAccounting()))

    def test_identical_to_list_wrapper_including_eviction_paths(self):
        records = self._records()
        # Late replays of early records: with a tiny cap their keys have been evicted by the
        # time they recur, which exercises the "unverifiable due to eviction" path.
        records = records + [r for r in records if r.identity.has_usable_identity][:20]
        for max_keys in (200_000, 3):
            acc_it, acc_list = DedupAccounting(), DedupAccounting()
            streamed = list(iter_deduplicated(iter(records), acc_it, max_keys=max_keys))
            listed, _ = deduplicate_records(iter(records), acc_list, max_keys=max_keys)
            self.assertEqual(streamed, listed)
            self.assertEqual(acc_it.to_dict(), acc_list.to_dict())
        # The small-cap run must really have exercised eviction, otherwise the loop proves less.
        self.assertTrue(acc_it.cap_degraded)
        self.assertGreater(acc_it.unverifiable_due_to_eviction, 0)

    def test_all_identity_levels_and_conflicts_and_ineligible_counted(self):
        acc = DedupAccounting()
        out = list(iter_deduplicated(iter(self._records()), acc))
        self.assertGreater(acc.duplicates_removed, 0)
        self.assertGreater(acc.identity_conflicts, 0)
        self.assertGreater(acc.identity_ineligible, 0)
        self.assertEqual(acc.retained_records, len(out))
        self.assertEqual(acc.total_input_records, acc.retained_records + acc.duplicates_removed)

    def test_identity_hierarchy_precedence_unchanged(self):
        # Level 1 key is (source, seq): same seq + same attributes is a duplicate even if request_id differs.
        a = rec(1000, source="s", seq=1, request_id="x")
        b = rec(1000, source="s", seq=1, request_id="y")
        # Level 2: (source, trace, span)
        c = rec(1001, source="s", trace_id="t", span_id="p")
        d = rec(1001, source="s", trace_id="t", span_id="p")
        # Level 3: (source, request_id)
        e = rec(1002, source="s", request_id="r")
        f = rec(1002, source="s", request_id="r")
        acc = DedupAccounting()
        out = list(iter_deduplicated(iter([a, b, c, d, e, f]), acc))
        self.assertEqual(out, [a, c, e])
        self.assertEqual(acc.duplicates_removed, 3)

    def test_records_without_usable_identity_are_never_dropped_or_given_identity(self):
        originals = [rec(1000, source=None) for _ in range(3)]
        partial = rec(1000, source="s")  # source but no seq/trace/request_id: still unusable
        acc = DedupAccounting()
        out = list(iter_deduplicated(iter(originals + [partial]), acc))
        self.assertEqual(len(out), 4)
        self.assertEqual(acc.identity_ineligible, 4)
        self.assertEqual(acc.duplicates_removed, 0)
        self.assertEqual(acc.exactness_status, "partial_identity_coverage")
        for original, yielded in zip(originals + [partial], out):
            self.assertIs(original, yielded)               # same object, nothing rewritten
            self.assertIs(original.identity, yielded.identity)
            self.assertIsNone(yielded.identity.request_id)  # no request id was fabricated
            self.assertIsNone(yielded.identity.seq)

    def test_input_order_preserved_including_out_of_order_timestamps(self):
        recs = [rec(1005, source="s", seq=1), rec(1001, source="s", seq=2), rec(1003, source="s", seq=3)]
        out = list(iter_deduplicated(iter(recs), DedupAccounting()))
        self.assertEqual([r.timestamp_sec for r in out], [1005, 1001, 1003])

    def test_first_occurrence_wins_under_replay_order(self):
        first = rec(1000, source="s", seq=1, status=200)
        replay = rec(1000, source="s", seq=1, status=200)
        out = list(iter_deduplicated(iter([first, replay]), DedupAccounting()))
        self.assertIs(out[0], first)

    def test_generator_is_lazy_single_use_and_counters_are_partial_until_exhausted(self):
        recs = [rec(1000 + i, source="s", seq=i) for i in range(5)]
        acc = DedupAccounting()
        gen = iter_deduplicated(iter(recs), acc)
        self.assertEqual(acc.total_input_records, 0)        # nothing happens until iterated
        next(gen)
        self.assertEqual(acc.total_input_records, 1)        # counters are PARTIAL mid-stream
        rest = list(gen)
        self.assertEqual(len(rest), 4)
        self.assertEqual(acc.total_input_records, 5)        # final only after exhaustion
        before = acc.to_dict()
        self.assertEqual(list(gen), [])                     # exhausted generator yields nothing
        self.assertEqual(acc.to_dict(), before)             # and cannot change the accounting

    def test_source_error_propagates_and_leaves_only_partial_accounting(self):
        def source():
            yield rec(1000, source="s", seq=1)
            yield rec(1001, source="s", seq=2)
            raise OSError("simulated read failure")
        acc = DedupAccounting()
        with self.assertRaises(OSError):
            list(iter_deduplicated(source(), acc))
        self.assertEqual(acc.total_input_records, 2)        # partial: callers must not treat as complete

    def test_accepts_any_iterable_not_only_iterators(self):
        recs = (rec(1000 + i, source="s", seq=i) for i in range(3))
        self.assertEqual(len(list(iter_deduplicated(recs, DedupAccounting()))), 3)
        self.assertEqual(len(list(iter_deduplicated([rec(1, source="s", seq=1)], DedupAccounting()))), 1)


# --------------------------------------------------------------------------- temporal handoff
class TestTemporalHandoff(unittest.TestCase):
    def _recs(self):
        return list(ingest_jsonl_stream(io.StringIO("\n".join(build_mixed_lines()) + "\n"), IngestAccounting()))

    def test_one_shot_generator_equals_list(self):
        recs = self._recs()
        a, b = TemporalEngine(), TemporalEngine()
        a.ingest_records(recs)
        b.ingest_records(r for r in recs)
        self.assertEqual(engine_state(a), engine_state(b))

    def test_aggregation_is_order_independent(self):
        recs = self._recs()
        shuffled = list(recs)
        random.Random(5).shuffle(shuffled)
        a, b = TemporalEngine(), TemporalEngine()
        a.ingest_records(recs)
        b.ingest_records(shuffled)
        self.assertEqual(engine_state(a), engine_state(b))

    def test_reingesting_an_exhausted_generator_adds_nothing(self):
        gen = (r for r in self._recs())
        e = TemporalEngine()
        e.ingest_records(gen)
        before = engine_state(e)
        e.ingest_records(gen)
        self.assertEqual(engine_state(e), before)

    def test_set_declared_coverage_matches_constructor_and_copies(self):
        recs = self._recs()
        intervals = [(1_700_000_000, 1_700_000_100), (1_700_000_200, 1_700_000_250)]
        a = TemporalEngine(declared_coverage_intervals=intervals)
        a.ingest_records(recs)
        b = TemporalEngine()
        b.ingest_records(recs)
        b.set_declared_coverage(intervals)
        self.assertEqual(engine_state(a), engine_state(b))
        intervals.append((1, 2))
        self.assertNotIn((1, 2), b.declared_coverage)         # defensive copy

    def test_binding_coverage_at_construction_would_lose_late_declarations(self):
        """Why the setter exists: the constructor copies the list, so a list that is still
        empty at construction time (streaming) silently drops coverage declared later."""
        late = []
        naive = TemporalEngine(declared_coverage_intervals=late)
        naive.ingest_records([rec(1_700_000_000, source="s", seq=1)])
        late.append((1_700_000_000, 1_700_000_100))           # control record seen after
        self.assertEqual(naive.compute_coverage().known_zero_seconds, 0)  # lost
        fixed = TemporalEngine()
        fixed.ingest_records([rec(1_700_000_000, source="s", seq=1)])
        fixed.set_declared_coverage(late)
        self.assertGreater(fixed.compute_coverage().known_zero_seconds, 0)


# --------------------------------------------------------------------------- pipeline equivalence
class TestPipelineEquivalence(TmpDirTestCase):
    def test_streaming_state_identical_to_materialised_state(self):
        path = self.path("mixed.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(build_mixed_lines()) + "\n")
        legacy_engine, legacy_ingest, legacy_dedup = legacy_state(path)
        engine, ingest_acc, dedup_acc, digest, retention = _stream_into_engine(path, DEFAULT_GAP_THRESHOLD_SEC)

        self.assertEqual(engine_state(engine), engine_state(legacy_engine))
        self.assertEqual(ingest_acc.to_dict(), legacy_ingest.to_dict())
        self.assertEqual(dedup_acc.to_dict(), legacy_dedup.to_dict())
        self.assertEqual(digest, compute_file_sha256(path))

        # Guard against a vacuous pass: the fixture really contains each awkward case.
        d = dedup_acc.to_dict()
        self.assertGreater(d["duplicates_removed"], 0)
        self.assertGreater(d["identity_conflicts"], 0)
        self.assertGreater(d["identity_ineligible"], 0)
        self.assertGreater(ingest_acc.malformed_json_lines, 0)
        self.assertGreater(ingest_acc.empty_lines, 0)
        self.assertGreater(ingest_acc.out_of_order_records, 0)
        self.assertEqual(len(ingest_acc.declared_coverage_intervals), 2)
        self.assertGreater(engine.compute_coverage().known_zero_seconds, 0)

    def test_run_pipeline_report_byte_identical_to_previous_flow(self):
        # Replays only outside the incident window, so the corrected second pass cannot differ.
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set(range(1000, 1040)) | set(range(1060, 1120)))
        new_report, *_ = run_pipeline(path)
        old_report = legacy_run_pipeline(path)
        self.assertGreaterEqual(len(new_report["incidents"]), 1)
        self.assertGreater(new_report["deduplication_accounting"]["duplicate_records_removed"], 0)
        self.assertEqual(canon(new_report), canon(old_report))

    def test_run_pipeline_return_shape_unchanged(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        result = run_pipeline(path)
        self.assertEqual(len(result), 5)
        report, engine, windows, coverage, dedup_acc = result
        self.assertIsInstance(engine, TemporalEngine)
        self.assertTrue(engine.sparse_buckets)               # traffic_lab.evaluate reuses the engine's buckets
        self.assertIsInstance(dedup_acc, DedupAccounting)


# --------------------------------------------------------------------------- second pass
class TestSecondPassEvidence(TmpDirTestCase):
    def test_evidence_matches_deduplicated_engine_counts(self):
        """Regression: the second pass used to count exact replays that the engine had removed."""
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set(range(1000, 1120)))   # replay inside the incident window too
        report, engine, *_ = run_pipeline(path)
        self.assertGreaterEqual(len(report["incidents"]), 1)
        for inc in report["incidents"]:
            of, ce = inc["observed_facts"], inc["contributor_evidence"]
            s, e = of["start_sec"], of["end_sec"]
            engine_total = sum(b.total_requests for t, b in engine.sparse_buckets.items() if s <= t < e)
            engine_routes = {}
            for t, b in engine.sparse_buckets.items():
                if s <= t < e:
                    for r, c in b.routes.items():
                        engine_routes[r] = engine_routes.get(r, 0) + c
            self.assertEqual(of["total_observed_requests"], engine_total)
            self.assertEqual(sum(ce["status_distribution"].values()), engine_total)
            for route, count in ce["top_routes"].items():
                self.assertEqual(count, engine_routes[route])
            self.assertLessEqual(max(ce["top_clients"].values()), engine_total)

    def test_second_pass_status_values(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        self.assertEqual(collect_second_pass_evidence([], path, "irrelevant"), SECOND_PASS_NO_INCIDENTS)
        incidents, digest, _log = incidents_for(path)
        self.assertTrue(incidents)
        self.assertEqual(collect_second_pass_evidence(incidents, path, digest), SECOND_PASS_OK)
        self.assertIsNotNone(incidents[0].contributor_evidence)

    def test_changed_input_yields_status_and_attaches_no_evidence(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        incidents, digest, _log = incidents_for(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": 1050, "method": "GET", "route": "/x", "client": "z",
                                "status": 200, "source": "proxy-1", "seq": 99999}) + "\n")
        self.assertEqual(collect_second_pass_evidence(incidents, path, digest), SECOND_PASS_INPUT_CHANGED)
        for inc in incidents:
            self.assertIsNone(inc.contributor_evidence)
            self.assertEqual(inc.investigation_leads, [])

    def test_no_expected_hash_means_no_verification(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        incidents, _digest, _log = incidents_for(path)
        self.assertEqual(collect_second_pass_evidence(incidents, path, None), SECOND_PASS_OK)

    def test_run_pipeline_warns_and_omits_evidence_when_input_changes_between_passes(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        original_digest = compute_file_sha256(path)
        real = cli_module.collect_second_pass_evidence

        def mutate_then_run(incidents, p, h, retention=None):
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": 1050, "method": "GET", "route": "/x", "client": "z",
                                    "status": 200, "source": "proxy-1", "seq": 99999}) + "\n")
            return real(incidents, p, h, retention=retention)

        with mock.patch.object(cli_module, "collect_second_pass_evidence", mutate_then_run):
            with self.assertWarns(RuntimeWarning):
                report, *_ = run_pipeline(path)
        # Reported hash is the hash of what was analysed, not of the later, mutated file.
        self.assertEqual(report["input_metadata"]["source_file_sha256"], original_digest)
        self.assertGreaterEqual(len(report["incidents"]), 1)
        for inc in report["incidents"]:
            self.assertNotIn("contributor_evidence", inc)

    def test_reported_hash_describes_analysed_bytes_even_if_file_changes_right_after_pass_1(self):
        """The old flow hashed the file in a separate earlier read, so a change between the hash
        and the analysis went unnoticed. The digest must be that of the bytes actually consumed."""
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        original_digest = compute_file_sha256(path)
        real = cli_module.ingest_jsonl_stream

        def mutate_after_last_record(stream, acc=None, **kw):
            for r in real(stream, acc, **kw):
                yield r
            with open(path, "a", encoding="utf-8") as f:       # runs once the last record was consumed
                f.write(json.dumps({"timestamp": 1050, "method": "GET", "route": "/late", "client": "z",
                                    "status": 200, "source": "proxy-1", "seq": 424242}) + "\n")

        with mock.patch.object(cli_module, "ingest_jsonl_stream", mutate_after_last_record):
            engine, _ingest_acc, _dedup_acc, digest, _log = _stream_into_engine(path, DEFAULT_GAP_THRESHOLD_SEC)
        self.assertNotEqual(compute_file_sha256(path), original_digest)        # the file really did change
        self.assertEqual(digest, original_digest)                              # but the digest is of what was analysed
        self.assertNotIn("/late", {r for b in engine.sparse_buckets.values() for r in b.routes})

    def test_run_pipeline_degrades_with_warning_if_second_pass_cannot_read(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        with mock.patch.object(cli_module, "collect_second_pass_evidence", side_effect=OSError("gone")):
            with self.assertWarns(RuntimeWarning):
                report, *_ = run_pipeline(path)
        self.assertGreaterEqual(len(report["incidents"]), 1)


# --------------------------------------------------------------------------- errors via the CLI
class TestCliReadErrors(TmpDirTestCase):
    def test_unreadable_input_exits_1_with_message_and_writes_no_report(self):
        out = self.path("analysis.json")
        for cmd in (["analyze", self.dir, "--json", out], ["summarize", self.dir]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = main(cmd)
            self.assertEqual(code, 1)
            self.assertIn("could not read input", err.getvalue())
        self.assertFalse(os.path.exists(out))

    def test_mid_stream_read_failure_is_not_reported_as_a_complete_analysis(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        out = self.path("analysis.json")
        real = cli_module.ingest_jsonl_stream

        def failing(stream, acc=None, **kw):
            for i, r in enumerate(real(stream, acc, **kw)):
                if i == 10:
                    raise OSError("simulated mid-stream failure")
                yield r

        err = io.StringIO()
        with mock.patch.object(cli_module, "ingest_jsonl_stream", failing):
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = main(["analyze", path, "--json", out])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out))


# --------------------------------------------------------------------------- HashedTextFile
class TestHashedTextFile(TmpDirTestCase):
    def _write(self, name, data):
        p = self.path(name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_digest_equals_independent_sha256_after_full_read(self):
        p = self._write("a.bin", b'{"a":1}\n' * 20000)
        with HashedTextFile(p) as src:
            for _ in src.stream:
                pass
            self.assertEqual(src.sha256_hexdigest(), compute_file_sha256(p))

    def test_digest_covers_unread_remainder_when_consumer_stops_early(self):
        p = self._write("b.bin", b'{"a":1}\n' * 50000)             # larger than any buffer
        with HashedTextFile(p) as src:
            next(iter(src.stream))
            self.assertEqual(src.sha256_hexdigest(), compute_file_sha256(p))

    def test_digest_covers_whole_file_when_ingestion_hits_its_record_cap(self):
        lines = "".join(json.dumps({"timestamp": 1000 + i, "method": "GET", "route": "/a", "client": "c",
                                    "status": 200}) + "\n" for i in range(500))
        p = self._write("c.jsonl", lines.encode())
        acc = IngestAccounting()
        with HashedTextFile(p) as src:
            n = len(list(ingest_jsonl_stream(src.stream, acc, max_records=5)))
            digest = src.sha256_hexdigest()
        self.assertEqual(n, 5)
        self.assertTrue(acc.record_cap_reached)
        self.assertEqual(digest, compute_file_sha256(p))

    def test_decoding_identical_to_plain_text_open(self):
        payload = (b'{"x":"\xff\xfe bad bytes"}\r\n' b'line2\rline3\n' + b"A" * 70000 + b"\n" + b"no trailing newline")
        p = self._write("d.bin", payload)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            expected = list(f)
        with HashedTextFile(p) as src:
            self.assertEqual(list(src.stream), expected)

    def test_bytes_appended_after_end_of_file_was_seen_are_not_hashed(self):
        p = self._write("e.jsonl", b'{"a":1}\n' * 10)
        original = compute_file_sha256(p)
        with HashedTextFile(p) as src:
            for _ in src.stream:
                pass
            with open(p, "ab") as f:
                f.write(b'{"late":1}\n')
            self.assertEqual(src.sha256_hexdigest(), original)   # describes what was analysed

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            HashedTextFile(self.path("does-not-exist.jsonl"))


# --------------------------------------------------------------------------- retention proof
class _AliveTracker:
    """Counts how many yielded records are simultaneously alive (CPython refcounting)."""

    def __init__(self):
        self.alive = 0
        self.peak = 0

    def _dec(self):
        self.alive -= 1

    def wrap(self, it):
        for r in it:
            self.alive += 1
            self.peak = max(self.peak, self.alive)
            weakref.finalize(r, self._dec)
            yield r


class TestNoRetainedEventList(TmpDirTestCase):
    N = 3000

    def _write(self):
        path = self.path("many.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(self.N):
                f.write(json.dumps({"timestamp": 1000 + i // 20, "method": "GET", "route": "/r%d" % (i % 7),
                                    "client": "c%d" % (i % 50), "status": 200, "source": "s", "seq": i}) + "\n")
        return path

    def test_materialising_flow_holds_every_event(self):
        """Sanity check that the tracker can see retention at all (so the next test means something)."""
        path = self._write()
        tracker = _AliveTracker()
        with open(path, "r", encoding="utf-8") as f:
            retained, _ = deduplicate_records(ingest_jsonl_stream(f, IngestAccounting()))
        TemporalEngine().ingest_records(tracker.wrap(iter(retained)))
        self.assertEqual(tracker.peak, self.N)

    def test_cli_streaming_path_never_accumulates_events(self):
        path = self._write()
        tracker = _AliveTracker()
        real = cli_module.iter_deduplicated

        def tracked(records, accounting, **kw):
            return tracker.wrap(real(records, accounting, **kw))

        with mock.patch.object(cli_module, "iter_deduplicated", tracked):
            engine, _ingest_acc, dedup_acc, _, _ = _stream_into_engine(path, DEFAULT_GAP_THRESHOLD_SEC)
        self.assertEqual(dedup_acc.retained_records, self.N)     # all events really flowed through
        self.assertEqual(sum(b.total_requests for b in engine.sparse_buckets.values()), self.N)
        self.assertLessEqual(tracker.peak, 3)                    # O(1) live events, not O(N)


# --------------------------------------------------------------------------- retention log
class TestRetentionLog(unittest.TestCase):
    def test_bit_packing_round_trip_across_byte_boundaries(self):
        rng = random.Random(3)
        for n in (0, 1, 7, 8, 9, 15, 16, 17, 1000):
            bits = [rng.random() < 0.5 for _ in range(n)]
            log = RetentionLog()
            for b in bits:
                log.append(b)
            self.assertEqual(len(log), n)
            self.assertEqual([log.is_retained(i) for i in range(n)], bits)
            self.assertEqual(len(log._bits), (n + 7) // 8)    # N/8 bytes, as documented

    def test_log_has_exactly_one_bit_per_input_record_and_matches_decisions(self):
        records = list(ingest_jsonl_stream(io.StringIO("\n".join(build_mixed_lines()) + "\n"), IngestAccounting()))
        acc, log = DedupAccounting(), RetentionLog()
        out = list(iter_deduplicated(iter(records), acc, retention=log))
        self.assertEqual(len(log), len(records))
        self.assertEqual(sum(log.is_retained(i) for i in range(len(log))), len(out))
        self.assertEqual(len(records) - len(out), acc.duplicates_removed)

    def test_replay_reproduces_first_pass_exactly_and_without_rehashing(self):
        records = list(ingest_jsonl_stream(io.StringIO("\n".join(build_mixed_lines()) + "\n"), IngestAccounting()))
        log = RetentionLog()
        first = list(iter_deduplicated(iter(records), DedupAccounting(), retention=log))
        with mock.patch("pulsewatch.dedup.hash_identity_key", side_effect=AssertionError("must not rehash")):
            replayed = list(iter_replayed(iter(records), log))
        self.assertEqual(replayed, first)

    def test_replay_refuses_a_different_sequence(self):
        records = [rec(1000 + i, source="s", seq=i) for i in range(10)]
        log = RetentionLog()
        list(iter_deduplicated(iter(records), DedupAccounting(), retention=log))
        with self.assertRaises(RetentionMismatch):
            list(iter_replayed(iter(records + [rec(2000, source="s", seq=99)]), log))   # grew
        with self.assertRaises(RetentionMismatch):
            list(iter_replayed(iter(records[:-1]), log))                                  # shrank


class TestSecondPassWithRetention(TmpDirTestCase):
    def test_replayed_evidence_equals_rededuplicated_evidence(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set(range(1000, 1120)))
        with_log, digest, log = incidents_for(path)
        without_log, _, _ = incidents_for(path)
        self.assertTrue(with_log)
        self.assertEqual(collect_second_pass_evidence(with_log, path, digest, retention=log), SECOND_PASS_OK)
        self.assertEqual(collect_second_pass_evidence(without_log, path, digest), SECOND_PASS_OK)
        self.assertEqual([i.to_dict() for i in with_log], [i.to_dict() for i in without_log])

    def test_cli_report_identical_with_and_without_retention_log(self):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set(range(1000, 1120)))
        with_log, *_ = run_pipeline(path)
        real = cli_module.collect_second_pass_evidence
        with mock.patch.object(cli_module, "collect_second_pass_evidence",
                               lambda inc, p, h, retention=None: real(inc, p, h)):
            without_log, *_ = run_pipeline(path)
        self.assertEqual(canon(with_log), canon(without_log))

    def _assert_changed(self, mutate):
        path = self.path("burst.jsonl")
        write_burst_fixture(path, dup_times=set())
        incidents, digest, log = incidents_for(path)
        mutate(path)
        self.assertEqual(collect_second_pass_evidence(incidents, path, digest, retention=log),
                         SECOND_PASS_INPUT_CHANGED)
        for inc in incidents:
            self.assertIsNone(inc.contributor_evidence)

    def test_replay_detects_appended_record(self):
        def mutate(p):
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": 1050, "method": "GET", "route": "/x", "client": "z",
                                    "status": 200, "source": "proxy-1", "seq": 99999}) + "\n")
        self._assert_changed(mutate)

    def test_replay_detects_truncated_file(self):
        def mutate(p):
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines[:-5])
        self._assert_changed(mutate)

    def test_replay_detects_same_length_edit_via_hash(self):
        def mutate(p):
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
            with open(p, "w", encoding="utf-8") as f:
                f.write(text.replace("192.168.1.7", "192.168.1.8", 1))     # same record count, same length
        self._assert_changed(mutate)


# --------------------------------------------------------------------------- route pooling
class TestRoutePooling(unittest.TestCase):
    def test_equal_routes_share_one_string_object_across_buckets(self):
        e = TemporalEngine()
        for t in (1000, 1001, 1002):
            e.ingest_record(rec(t, route="".join(["/api/", "v1/", "items"])))   # distinct str objects
        keys = [next(iter(b.routes)) for b in e.sparse_buckets.values()]
        self.assertEqual(len(keys), 3)
        self.assertTrue(keys[0] is keys[1] is keys[2])

    def test_pooling_does_not_change_counts(self):
        e = TemporalEngine()
        for _ in range(3):
            e.ingest_record(rec(1000, route="/a"))
        e.ingest_record(rec(1000, route="/b"))
        self.assertEqual(dict(e.sparse_buckets[1000].routes), {"/a": 3, "/b": 1})


if __name__ == "__main__":
    unittest.main()
