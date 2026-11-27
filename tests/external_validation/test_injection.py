"""Tests for WorldCup98 semi-synthetic perturbation injection layer."""

import unittest
from external_validation.worldcup98.inject import inject_perturbation


class TestInjectionLayer(unittest.TestCase):
    def setUp(self):
        # 600s background trace at 5 req/s
        self.bg_events = []
        for s in range(600):
            for i in range(5):
                self.bg_events.append({
                    "timestamp": 1700000000 + s,
                    "method": "GET",
                    "route": f"/item/{i}",
                    "client": f"10.0.0.{i}",
                    "status": 200,
                })

    def test_warmup_violation_rejected(self):
        with self.assertRaises(ValueError):
            inject_perturbation(self.bg_events, "burst", start_offset_sec=100, duration_sec=50)

    def test_overflow_violation_rejected(self):
        with self.assertRaises(ValueError):
            inject_perturbation(self.bg_events, "burst", start_offset_sec=500, duration_sec=200)

    def test_stream_contains_only_standard_detector_fields(self):
        inj_events, manifest = inject_perturbation(self.bg_events, "burst", start_offset_sec=350, duration_sec=30)
        allowed = {"timestamp", "method", "route", "client", "status"}
        for ev in inj_events:
            self.assertTrue(set(ev.keys()).issubset(allowed))

    def test_manifest_is_separate(self):
        inj_events, manifest = inject_perturbation(self.bg_events, "surge", start_offset_sec=350, duration_sec=60)
        self.assertEqual(manifest.duration_sec, 60)
        self.assertEqual(manifest.perturbation_kind, "surge")
        self.assertGreater(manifest.records_added, 0)

    def test_composition_perturbation_preserves_total_volume(self):
        inj_events, manifest = inject_perturbation(
            self.bg_events,
            "route_concentration",
            start_offset_sec=350,
            duration_sec=60,
            target_route="/concentrated/route",
        )
        self.assertEqual(len(inj_events), len(self.bg_events))
        self.assertEqual(manifest.records_added, 0)
        self.assertGreater(manifest.records_modified, 0)

    def test_injection_is_deterministic_and_seed_sensitive(self):
        ev1, _ = inject_perturbation(self.bg_events, "burst", start_offset_sec=350, duration_sec=30, seed=101)
        ev2, _ = inject_perturbation(self.bg_events, "burst", start_offset_sec=350, duration_sec=30, seed=101)
        ev3, _ = inject_perturbation(self.bg_events, "burst", start_offset_sec=350, duration_sec=30, seed=102)

        self.assertEqual(ev1, ev2)
        self.assertNotEqual(ev1, ev3)


if __name__ == "__main__":
    unittest.main()
