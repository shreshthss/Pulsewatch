"""Tests for benchmark conventions: scenario taxonomy, disjoint seed sets, and fingerprint stability."""

import unittest
from traffic_lab.scenarios_v2 import (
    SEED_SETS,
    assert_seed_discipline,
    fingerprint_scenarios_v2,
    generate_scenario_v2_trace,
    get_all_scenarios_v2,
)


class TestBenchmarkConventions(unittest.TestCase):
    def test_scenario_count_and_categories(self):
        scenarios = get_all_scenarios_v2()
        self.assertEqual(len(scenarios), 39)
        cats = {s.category for s in scenarios}
        expected_cats = {"negative", "hard_negative", "telemetry_negative", "positive", "borderline", "composition_only"}
        self.assertEqual(cats, expected_cats)

    def test_seed_sets_are_strictly_disjoint(self):
        seen_seeds = set()
        for role, seeds in SEED_SETS.items():
            self.assertTrue(len(seeds) > 0, f"Role {role} has empty seed set")
            s_set = set(seeds)
            overlap = seen_seeds.intersection(s_set)
            self.assertEqual(len(overlap), 0, f"Seed overlap found for role {role}: {overlap}")
            seen_seeds.update(s_set)

    def test_seed_discipline_assertions(self):
        # Valid evaluation seed
        assert_seed_discipline(41001, "evaluation")
        # Invalid seed for role
        with self.assertRaises(ValueError):
            assert_seed_discipline(31001, "evaluation")

    def test_fingerprint_deterministic(self):
        fp1 = fingerprint_scenarios_v2()
        fp2 = fingerprint_scenarios_v2()
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)

    def test_negative_scenarios_have_no_ground_truth_incidents(self):
        scenarios = get_all_scenarios_v2()
        neg_specs = [s for s in scenarios if s.category in ("negative", "hard_negative", "telemetry_negative")]
        for s in neg_specs:
            events, gt = generate_scenario_v2_trace(s, seed=41001)
            self.assertEqual(len(gt.incidents), 0, f"Negative scenario {s.scenario_id} must have 0 ground truth incidents")


if __name__ == "__main__":
    unittest.main()
