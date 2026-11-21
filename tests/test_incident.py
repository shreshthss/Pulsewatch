"""Tests for pulsewatch.incident."""

import unittest
from pulsewatch.detector import DetectionCandidate
from pulsewatch.incident import IncidentMerger, classify_incident_shape
from pulsewatch.ingest import EventIdentity, EventRecord
from pulsewatch.temporal import TemporalEngine


class TestIncident(unittest.TestCase):
    def test_micro_burst_shape(self):
        engine = TemporalEngine()
        id_ = EventIdentity()
        for t in range(10, 13):
            for _ in range(20):
                engine.ingest_record(EventRecord(t, "GET", "/api/fast", "c1", 200, id_))

        shape, rationale = classify_incident_shape(10, 13, engine, scales_fired=[10])
        self.assertEqual(shape, "micro_burst")

    def test_sustained_surge_shape(self):
        engine = TemporalEngine()
        id_ = EventIdentity()
        for t in range(100, 200):
            for _ in range(5):
                engine.ingest_record(EventRecord(t, "GET", "/api/heavy", "c1", 200, id_))

        shape, rationale = classify_incident_shape(100, 200, engine, scales_fired=[60, 300])
        self.assertEqual(shape, "sustained_surge")

    def test_ramp_shape(self):
        engine = TemporalEngine()
        id_ = EventIdentity()
        # Monotonically increasing from t=0 to 30: 1/s, then 5/s, then 15/s
        for t in range(0, 10):
            engine.ingest_record(EventRecord(t, "GET", "/api/ramp", "c1", 200, id_))
        for t in range(10, 20):
            for _ in range(5):
                engine.ingest_record(EventRecord(t, "GET", "/api/ramp", "c1", 200, id_))
        for t in range(20, 30):
            for _ in range(15):
                engine.ingest_record(EventRecord(t, "GET", "/api/ramp", "c1", 200, id_))

        shape, _ = classify_incident_shape(0, 30, engine, scales_fired=[10, 60])
        self.assertEqual(shape, "ramp")

    def test_candidate_merging_and_evidence_separation(self):
        engine = TemporalEngine()
        id_ = EventIdentity()
        for t in range(100, 125):
            for _ in range(10):
                engine.ingest_record(EventRecord(t, "POST", "/submit", "c1", 200, id_))

        c1 = DetectionCandidate(100, 110, 10, 100, 10.0, 90, 5.0, 10.0)
        c2 = DetectionCandidate(105, 115, 10, 100, 10.0, 90, 5.0, 10.0)
        c3 = DetectionCandidate(112, 122, 10, 100, 10.0, 90, 5.0, 10.0)

        merger = IncidentMerger(merge_gap_sec=5)
        incidents = merger.merge_candidates([c1, c2, c3], engine, median_rate_1s=1.0)

        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        self.assertEqual(inc.observed_facts.start_sec, 100)
        self.assertEqual(inc.observed_facts.end_sec, 122)
        self.assertEqual(inc.observed_facts.duration_sec, 22)
        self.assertIn("/submit", inc.observed_facts.top_routes)

        # Verify separation of findings, patterns, unknown causes
        self.assertEqual(inc.descriptive_pattern.shape, "isolated_spike")
        self.assertIn("cannot infer user intent", inc.unknown_causes)
        self.assertTrue(inc.statistical_findings.max_z_score >= 5.0)


if __name__ == "__main__":
    unittest.main()
