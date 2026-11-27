"""Tests for pipeline harness equivalence and ablation switch isolation."""

import unittest
from traffic_lab.pipeline_harness import HarnessConfig, PipelineHarness
from traffic_lab.scenarios_v2 import generate_scenario_v2_trace, get_all_scenarios_v2


class TestHarnessEquivalence(unittest.TestCase):
    def setUp(self):
        all_specs = {s.scenario_id: s for s in get_all_scenarios_v2()}
        self.surge_spec = all_specs["POS_surge_long"]
        self.dup_spec = all_specs["NEG_dup_replay"]

    def test_default_harness_matches_expected_surge(self):
        events, gt = generate_scenario_v2_trace(self.surge_spec, seed=41001)
        harness = PipelineHarness(HarnessConfig())
        incidents = harness.run_on_events(events)
        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        # Should overlap ground truth anomaly [600, 690)
        gt_inc = gt.incidents[0]
        self.assertLessEqual(inc.observed_facts.start_sec, gt_inc.end_sec)
        self.assertGreaterEqual(inc.observed_facts.end_sec, gt_inc.start_sec)

    def test_dedup_toggle_isolation(self):
        # Normal traffic with a sudden duplicate replay storm at t=500..560
        events = []
        for s in range(1000):
            for i in range(2):
                ev = {
                    "timestamp": 1700000000 + s,
                    "method": "GET",
                    "route": "/r",
                    "client": "c",
                    "status": 200,
                    "source": "gw",
                    "request_id": f"r-{s}-{i}",
                }
                events.append(ev)
                if 500 <= s < 560:
                    for _ in range(25):
                        events.append(dict(ev))

        h_with_dedup = PipelineHarness(HarnessConfig(dedup_enabled=True))
        h_without_dedup = PipelineHarness(HarnessConfig(dedup_enabled=False))

        inc_with = h_with_dedup.run_on_events(events)
        inc_without = h_without_dedup.run_on_events(events)

        # With dedup on pure duplicate replay: 0 incidents (filtered by dedup)
        self.assertEqual(len(inc_with), 0)
        # Without dedup: proxy duplicate storm creates an apparent spike, raising false alarm
        self.assertGreaterEqual(len(inc_without), 1)

    def test_dispersion_scaling_toggle(self):
        all_specs = {s.scenario_id: s for s in get_all_scenarios_v2()}
        gamma_spec = all_specs["NEG_overdispersed_gamma"]
        events, _ = generate_scenario_v2_trace(gamma_spec, seed=41001)

        # With dispersion scaling: absorbs overdispersion into phi, preventing false alarms
        h_disp = PipelineHarness(HarnessConfig(dispersion_scaling=True))
        # Without dispersion scaling: forces Poisson phi=1, causing false alarms on overdispersed noise
        h_poisson = PipelineHarness(HarnessConfig(dispersion_scaling=False))

        inc_disp = h_disp.run_on_events(events)
        inc_poiss = h_poisson.run_on_events(events)
        self.assertLessEqual(len(inc_disp), len(inc_poiss))


if __name__ == "__main__":
    unittest.main()
