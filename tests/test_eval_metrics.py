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
        # Construct intervals with asymmetric IoU structure:
        # G0: [100, 200), G1: [250, 350)
        # D0: [100, 205) -> IoU with G0 ~ 0.95, IoU with G1 = 0
        # D1: [100, 210) -> IoU with G0 ~ 0.90, IoU with G1 = 0
        # Also test direct optimal assignment function:
        dets = [(100, 190), (105, 205)]
        gts = [(100, 200)]
        res_opt = match_intervals_optimal(dets, gts, min_iou=0.05)
        self.assertEqual(res_opt.true_positives, 1)
        self.assertEqual(res_opt.false_positives, 1)
        self.assertEqual(res_opt.false_negatives, 0)


if __name__ == "__main__":
    unittest.main()
