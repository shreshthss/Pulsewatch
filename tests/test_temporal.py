"""Tests for pulsewatch.temporal."""

import unittest
from pulsewatch.ingest import EventIdentity, EventRecord
from pulsewatch.temporal import TemporalEngine


class TestTemporal(unittest.TestCase):
    def test_sparse_aggregation_no_dense_allocation(self):
        engine = TemporalEngine(gap_threshold_sec=10)
        id1 = EventIdentity(source="s1", seq=1)
        # Two events separated by 100,000 seconds
        r1 = EventRecord(1000, "GET", "/a", "c1", 200, id1)
        r2 = EventRecord(101000, "POST", "/b", "c1", 201, id1)

        engine.ingest_record(r1)
        engine.ingest_record(r2)

        # sparse_buckets should only contain 2 entries!
        self.assertEqual(len(engine.sparse_buckets), 2)
        coverage = engine.compute_coverage()
        self.assertEqual(coverage.active_seconds, 2)
        self.assertEqual(len(coverage.unknown_gaps), 1)
        self.assertEqual(coverage.unknown_gaps[0].duration_sec, 101000 - 1000 - 1)

    def test_gap_and_continuous_segments(self):
        engine = TemporalEngine(gap_threshold_sec=5)
        # Events at 10, 12, 14 (within 5s), then gap until 30, 31
        id_ = EventIdentity()
        for t in [10, 12, 14, 30, 31]:
            engine.ingest_record(EventRecord(t, "GET", "/a", "c1", 200, id_))

        gaps, segments = engine.compute_gaps_and_segments()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].start_sec, 15)
        self.assertEqual(gaps[0].end_sec, 29)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].start_sec, 10)
        self.assertEqual(segments[0].end_sec, 14)
        self.assertEqual(segments[1].start_sec, 30)
        self.assertEqual(segments[1].end_sec, 31)

    def test_60s_windows_utc_aligned(self):
        engine = TemporalEngine(gap_threshold_sec=20)
        # Event at 125 (00:02:05) and 130 (00:02:10)
        id_ = EventIdentity()
        engine.ingest_record(EventRecord(125, "GET", "/home", "c1", 200, id_))
        engine.ingest_record(EventRecord(130, "POST", "/login", "c1", 500, id_))

        windows = engine.generate_60s_windows()
        # [120, 180) -> contains 125, 130
        self.assertEqual(len(windows), 1)
        w = windows[0]
        self.assertEqual(w.start_sec, 120)
        self.assertEqual(w.end_sec, 180)
        self.assertEqual(w.total_requests, 2)
        self.assertEqual(w.status_2xx, 1)
        self.assertEqual(w.status_5xx, 1)
        # Since earliest is 125 (> 120) and latest is 130 (< 179), boundary is True
        self.assertTrue(w.is_boundary)
        self.assertFalse(w.is_complete)


if __name__ == "__main__":
    unittest.main()
