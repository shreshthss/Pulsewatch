"""Tests for pulsewatch.detector."""

import unittest
from pulsewatch.dedup import DedupAccounting
from pulsewatch.detector import PWScanDetector
from pulsewatch.ingest import EventIdentity, EventRecord
from pulsewatch.temporal import TemporalEngine


class TestDetector(unittest.TestCase):
    def test_baseline_abstention_on_short_duration(self):
        engine = TemporalEngine()
        # Only 5 seconds of observations
        id_ = EventIdentity()
        for t in range(100, 105):
            engine.ingest_record(EventRecord(t, "GET", "/api/v1", "c1", 200, id_))

        coverage = engine.compute_coverage()
        gaps, segments = engine.compute_gaps_and_segments()
        detector = PWScanDetector()
        baseline = detector.evaluate_baseline(engine, coverage, segments)

        self.assertEqual(baseline.reliability, "abstain")
        self.assertIn("Insufficient observation duration", baseline.abstain_reason)

    def test_detection_on_traffic_spike(self):
        engine = TemporalEngine()
        id_ = EventIdentity()
        # Normal steady baseline: 1 request per second for 300 seconds (seconds 0 to 299)
        for t in range(300):
            engine.ingest_record(EventRecord(t, "GET", "/api/normal", "c1", 200, id_))

        # Inject an isolated spike at t = 150..154 with 50 requests per second
        for t in range(150, 155):
            for i in range(50):
                engine.ingest_record(EventRecord(t, "POST", "/api/attack", "c2", 500, id_))

        coverage = engine.compute_coverage()
        gaps, segments = engine.compute_gaps_and_segments()
        detector = PWScanDetector()
        baseline = detector.evaluate_baseline(engine, coverage, segments)

        self.assertIn(baseline.reliability, ["high", "medium"])
        self.assertAlmostEqual(baseline.median_rate_1s, 1.0, delta=0.5)

        candidates = detector.scan(engine, coverage, segments, baseline)
        self.assertTrue(len(candidates) > 0)

        # Ensure candidates overlap the spike range [150, 155]
        spike_candidates = [c for c in candidates if 150 <= c.start_sec <= 155 or 150 <= c.end_sec <= 165]
        self.assertTrue(len(spike_candidates) > 0)
        self.assertTrue(any(c.excess_count >= 100 for c in spike_candidates))


if __name__ == "__main__":
    unittest.main()
