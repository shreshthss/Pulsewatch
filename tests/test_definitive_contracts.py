"""Tests for definitive PulseWatch contracts: nanosecond time, 16-byte BLAKE2b dedup, control records, PW-Scan NB tails, sticky masking, and second-pass evidence."""

import io
import json
import unittest

from pulsewatch.dedup import DedupAccounting, Deduplicator, hash_identity_key
from pulsewatch.detector import (
    DispersionProfile,
    PWScanDetector,
    compute_upper_tail_prob,
    log_nb_pmf,
    log_poisson_pmf,
)
from pulsewatch.incident import (
    Incident,
    IncidentMerger,
    ObservedFacts,
    StatisticalFindings,
    DescriptivePattern,
    collect_second_pass_evidence,
)
from pulsewatch.ingest import (
    EventIdentity,
    EventRecord,
    IngestAccounting,
    ingest_jsonl_stream,
    parse_record_dict,
    parse_timestamp_to_ns,
)
from pulsewatch.temporal import TemporalEngine


class TestDefinitiveContracts(unittest.TestCase):
    def test_nanosecond_parsing(self):
        # Seconds
        self.assertEqual(parse_timestamp_to_ns(1700000000), 1700000000_000000000)
        # Milliseconds
        self.assertEqual(parse_timestamp_to_ns(1700000000000), 1700000000_000000000)
        # Nanoseconds directly
        self.assertEqual(parse_timestamp_to_ns(1700000000_123456789), 1700000000_123456789)
        # ISO-8601 with fractional seconds
        ns = parse_timestamp_to_ns("2026-11-21T05:00:00.500Z")
        self.assertEqual(ns, 1795237200_500000000)

    def test_control_record_coverage(self):
        lines = (
            '{"_pw": "coverage", "start": "2026-11-21T05:00:00Z", "end": "2026-11-21T05:10:00Z"}\n'
            '{"timestamp": "2026-11-21T05:01:00Z", "method": "GET", "route": "/api/v1", "client": "c1", "status": 200, "source": "gw1", "seq": 1}\n'
        )
        stream = io.StringIO(lines)
        accounting = IngestAccounting()
        records = list(ingest_jsonl_stream(stream, accounting))

        self.assertEqual(len(records), 1)
        self.assertEqual(accounting.control_records, 1)
        self.assertEqual(len(accounting.declared_coverage_intervals), 1)
        self.assertEqual(accounting.declared_coverage_intervals[0], (1795237200, 1795237800))

        # Pass declared coverage to temporal engine
        engine = TemporalEngine(declared_coverage_intervals=accounting.declared_coverage_intervals)
        engine.ingest_record(records[0])
        cov = engine.compute_coverage()
        # Active: 1s, Known zeros: declared coverage minus active!
        self.assertEqual(cov.active_seconds, 1)
        self.assertTrue(cov.known_zero_seconds > 0)

    def test_blake2b_identity_dedup_and_eviction(self):
        h1 = hash_identity_key(("source_seq", "gw1", "101"))
        self.assertEqual(len(h1), 16)

        # Verify eviction and unverifiable_due_to_eviction
        dedup = Deduplicator(max_keys=2, max_evicted_history=5)
        acc = DedupAccounting()

        id1 = EventIdentity(source="gw1", seq=1)
        id2 = EventIdentity(source="gw1", seq=2)
        id3 = EventIdentity(source="gw1", seq=3)

        r1 = EventRecord(1000, "GET", "/a", "c1", 200, id1)
        r2 = EventRecord(1001, "GET", "/b", "c1", 200, id2)
        r3 = EventRecord(1002, "GET", "/c", "c1", 200, id3)

        dedup.process_record(r1, acc)
        dedup.process_record(r2, acc)
        # r3 causes r1 to be evicted
        dedup.process_record(r3, acc)
        self.assertTrue(acc.cap_degraded)
        self.assertEqual(acc.evicted_keys_count, 1)

        # Now re-present r1 (which was evicted)
        r1_replayed = EventRecord(1000, "GET", "/a", "c1", 200, id1)
        out = dedup.process_record(r1_replayed, acc)
        # Must retain record and record unverifiable_due_to_eviction!
        self.assertIsNotNone(out)
        self.assertEqual(acc.unverifiable_due_to_eviction, 1)

    def test_negative_binomial_tail_computation(self):
        disp = DispersionProfile(scale_sec=60, phi=2.0, is_poisson=False, nb_r=10.0, nb_q=0.5)
        # E = 10, Var = 20. Upper tail for observed 30
        log_p, p_val, is_exact, omitted_bound = compute_upper_tail_prob(30, 10.0, disp)
        self.assertTrue(p_val < 0.001)
        self.assertTrue(is_exact)
        self.assertTrue(omitted_bound < 1e-6)

    def test_sticky_contamination_masking(self):
        engine = TemporalEngine()
        id_ = EventIdentity(source="gw1", seq=1)
        # 400 seconds of baseline ~2 req/s
        for t in range(400):
            engine.ingest_record(EventRecord(t, "GET", "/api/data", "c1", 200, id_))
            engine.ingest_record(EventRecord(t, "GET", "/api/data", "c2", 200, id_))

        # Inject surge at 200..219 of 40 req/s
        for t in range(200, 220):
            for i in range(40):
                engine.ingest_record(EventRecord(t, "POST", "/api/flood", f"attacker-{i}", 500, id_))

        coverage = engine.compute_coverage()
        _, segments = engine.compute_gaps_and_segments()
        detector = PWScanDetector()
        baseline = detector.fit_baseline_profile(engine, coverage, segments)

        # Baseline rate should stay close to 2.0 despite the surge due to sticky masking
        self.assertAlmostEqual(baseline.fitted_rate_1s, 2.0, delta=0.5)
        self.assertTrue(len(baseline.masked_intervals) > 0)
        self.assertTrue(baseline.fraction_time_masked > 0.0)


if __name__ == "__main__":
    unittest.main()
