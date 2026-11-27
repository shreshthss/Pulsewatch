"""Tests for evaluation metrics: interval IoU, strict matching, null-class handling, and Clopper-Pearson intervals."""

import math
import unittest
from traffic_lab.metrics import (
    clopper_pearson,
    compute_iou,
    evaluate_scenario_detections,
    garwood_poisson_interval,
    match_intervals_optimal,
    match_intervals_strict,
)


class TestIntervalIoU(unittest.TestCase):
    def test_identical_intervals_give_unit_iou(self):
        self.assertEqual(compute_iou(100, 200, 100, 200), 1.0)

    def test_disjoint_intervals_give_zero_iou(self):
        self.assertEqual(compute_iou(100, 200, 200, 300), 0.0)
        self.assertEqual(compute_iou(100, 200, 300, 400), 0.0)

    def test_half_overlap_iou(self):
        # [100, 200) and [150, 250): intersection = 50, union = 150 => 1/3
        self.assertAlmostEqual(compute_iou(100, 200, 150, 250), 1.0 / 3.0, places=6)

    def test_nested_intervals_iou(self):
        # [100, 300) and [150, 250): intersection = 100, union = 200 => 0.5
        self.assertEqual(compute_iou(100, 300, 150, 250), 0.5)


class TestStrictMatching(unittest.TestCase):
    def test_strict_one_to_one_prevents_double_matching(self):
        # Two detections overlap one ground truth
        detections = [(100, 200), (120, 220)]
        gt = [(100, 200)]
        res = match_intervals_strict(detections, gt, min_iou=0.05)
        self.assertEqual(res.true_positives, 1)
        self.assertEqual(res.false_positives, 1)  # Second detection counts as FP
        self.assertEqual(res.false_negatives, 0)
        self.assertEqual(len(res.pairs), 1)

    def test_descending_iou_priority(self):
        # Det 0 overlaps GT 0 with IoU 0.2
        # Det 1 overlaps GT 0 with IoU 0.8
        # Strict matching should pair Det 1 with GT 0 first!
        detections = [(100, 500), (100, 200)]
        gt = [(100, 200)]
        res = match_intervals_strict(detections, gt, min_iou=0.05)
        self.assertEqual(len(res.pairs), 1)
        self.assertEqual(res.pairs[0][0], 1)  # Det 1 matched
        self.assertEqual(res.pairs[0][1], 0)  # GT 0 matched
        self.assertEqual(res.pairs[0][2], 1.0)


class TestNullClassConventions(unittest.TestCase):
    def test_negative_scenario_f1_is_none(self):
        # No ground truth incidents, 0 detections
        res = evaluate_scenario_detections([], [], scenario_name="neg_clean")
        self.assertFalse(res.has_positive_class)
        self.assertIsNone(res.precision)
        self.assertIsNone(res.recall)
        self.assertIsNone(res.f1_score)
        self.assertEqual(res.false_positives, 0)
        self.assertEqual(res.outcome_label, "no_alert_success")

        # Serializes as null in JSON
        d = res.to_dict()
        self.assertIsNone(d["f1_score"])
        self.assertIsNone(d["precision"])

    def test_negative_scenario_with_false_alerts(self):
        # No ground truth incidents, 2 detections
        res = evaluate_scenario_detections([(100, 150), (200, 250)], [], scenario_name="neg_fp")
        self.assertFalse(res.has_positive_class)
        self.assertEqual(res.false_positives, 2)
        self.assertIsNone(res.f1_score)
        self.assertEqual(res.outcome_label, "false_alerts")
        self.assertGreater(res.false_incidents_per_day, 0.0)


class TestClopperPearson(unittest.TestCase):
    def test_zero_successes_bound(self):
        low, high = clopper_pearson(0, 100, confidence=0.95)
        self.assertEqual(low, 0.0)
        # Exact: 1 - 0.025^(1/100) for two-sided 95%
        self.assertAlmostEqual(high, 1.0 - math.pow(0.025, 0.01), places=4)

    def test_all_successes_bound(self):
        low, high = clopper_pearson(100, 100, confidence=0.95)
        self.assertEqual(high, 1.0)
        self.assertAlmostEqual(low, math.pow(0.025, 0.01), places=4)

    def test_intermediate_coverage(self):
        low, high = clopper_pearson(50, 100, confidence=0.95)
        self.assertLess(low, 0.50)
        self.assertGreater(high, 0.50)

    def test_degenerate_and_boundary_parameters(self):
        self.assertEqual(clopper_pearson(-1, 10), (0.0, 1.0))
        self.assertEqual(clopper_pearson(11, 10), (0.0, 1.0))
        self.assertEqual(clopper_pearson(0, 0), (0.0, 1.0))
        low1, high1 = clopper_pearson(1, 1)
        self.assertEqual(high1, 1.0)
        self.assertAlmostEqual(low1, 0.025, places=4)


class TestOptimalMatching(unittest.TestCase):
    def test_adversarial_matrix_optimal_vs_greedy(self):
        dets = [(100, 190), (105, 205)]
        gts = [(100, 200)]
        res_opt = match_intervals_optimal(dets, gts, min_iou=0.05)
        self.assertEqual(res_opt.true_positives, 1)
        self.assertEqual(res_opt.false_positives, 1)
        self.assertEqual(res_opt.false_negatives, 0)

    def test_equal_weight_ties_deterministic(self):
        # Two identical detections and one GT
        dets = [(100, 200), (100, 200)]
        gts = [(100, 200)]
        res1 = match_intervals_optimal(dets, gts)
        res2 = match_intervals_optimal(dets, gts)
        self.assertEqual(res1.pairs, res2.pairs)
        self.assertEqual(res1.true_positives, 1)
        self.assertEqual(res1.false_positives, 1)

    def test_disjoint_intervals(self):
        dets = [(100, 200), (300, 400)]
        gts = [(500, 600), (700, 800)]
        res = match_intervals_optimal(dets, gts)
        self.assertEqual(res.true_positives, 0)
        self.assertEqual(res.false_positives, 2)
        self.assertEqual(res.false_negatives, 2)
        self.assertEqual(res.pairs, [])

    def test_dense_candidate_graph(self):
        # 3 detections all overlapping with 3 ground truths
        dets = [(100, 150), (110, 160), (120, 170)]
        gts = [(105, 155), (115, 165), (125, 175)]
        res = match_intervals_optimal(dets, gts, min_iou=0.1)
        self.assertEqual(res.true_positives, 3)
        self.assertEqual(res.false_positives, 0)
        self.assertEqual(res.false_negatives, 0)
        self.assertGreater(res.mean_iou, 0.5)

    def test_large_incident_collections_scalability(self):
        import time
        # 200 incidents in sequence
        dets = [(i * 100, i * 100 + 50) for i in range(200)]
        gts = [(i * 100 + 5, i * 100 + 55) for i in range(200)]
        t0 = time.perf_counter()
        res = match_intervals_optimal(dets, gts)
        elapsed = time.perf_counter() - t0
        self.assertEqual(res.true_positives, 200)
        self.assertLess(elapsed, 0.5)  # must complete in < 500ms

    def test_exhaustive_brute_force_equivalence(self):
        import itertools
        import random
        from traffic_lab.metrics import compute_iou

        def brute_force(dets, gts, min_iou=0.05):
            n_d, n_g = len(dets), len(gts)
            best = (0, 0.0)
            for k in range(min(n_d, n_g), -1, -1):
                if k < best[0]:
                    break
                for d_sub in itertools.combinations(range(n_d), k):
                    for g_sub in itertools.permutations(range(n_g), k):
                        valid = True
                        tot = 0.0
                        for d, g in zip(d_sub, g_sub):
                            iou = compute_iou(dets[d][0], dets[d][1], gts[g][0], gts[g][1])
                            if iou <= min_iou:
                                valid = False
                                break
                            tot += iou
                        if valid and (k, round(tot, 6)) > best:
                            best = (k, round(tot, 6))
            return best

        rng = random.Random(999)
        for _ in range(25):
            n_d = rng.randint(2, 4)
            n_g = rng.randint(2, 4)
            dets = sorted([(rng.randint(0, 30), rng.randint(35, 80)) for _ in range(n_d)])
            gts = sorted([(rng.randint(0, 30), rng.randint(35, 80)) for _ in range(n_g)])
            bf_score = brute_force(dets, gts)
            opt_res = match_intervals_optimal(dets, gts)
            opt_score = (opt_res.true_positives, round(sum(p[2] for p in opt_res.pairs), 6))
            self.assertEqual(bf_score, opt_score)

    def test_evaluate_scenario_detections_with_optimal_mode(self):
        dets = [(100, 190), (105, 205)]
        gts = [(100, 200)]
        res_strict = evaluate_scenario_detections(dets, gts, matching_mode="strict")
        res_optimal = evaluate_scenario_detections(dets, gts, matching_mode="optimal")
        self.assertEqual(res_strict.strict_one_to_one["true_positives"], 1)
        self.assertEqual(res_optimal.strict_one_to_one["true_positives"], 1)


if __name__ == "__main__":
    unittest.main()
