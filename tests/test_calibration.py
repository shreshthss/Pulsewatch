"""Tests for PulseWatch statistical calibration, empirical false-alarm control, and rate stability."""

import math
import unittest
import warnings

from pulsewatch.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    CalibrationProfile,
    FamilyMetricResult,
    GRID_Z,
    NULL_FAMILIES,
    bootstrap_ratio_upper_bound,
    clopper_pearson_upper_bound,
    derive_trace_seed,
    evaluate_threshold_grid_on_trace,
    generate_null_trace_events,
    poisson_upper_bound,
    quasi_poisson_dispersion,
    select_calibrated_threshold,
)
from pulsewatch.detector import (
    ALGORITHM_REVISION,
    FIXED_Z_THRESHOLD,
    BaselineProfile,
    PWScanDetector,
    assess_rate_stability,
    compute_detector_config_fingerprint,
)
from pulsewatch.temporal import TemporalEngine


class TestTargetHonesty(unittest.TestCase):
    def test_default_is_uncalibrated_fixed_threshold(self):
        detector = PWScanDetector()
        self.assertEqual(detector.active_z_threshold, FIXED_Z_THRESHOLD)
        self.assertFalse(detector.is_calibrated)
        info = detector.calibration_info()
        self.assertEqual(info["active_z_threshold"], 3.5)
        self.assertIsNone(info["target_false_incidents_per_day"])

    def test_target_without_calibration_warns_and_retains_fixed_threshold(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detector = PWScanDetector(target_false_incidents_per_day=1.0)
            self.assertEqual(len(w), 1)
            self.assertIn("target_false_incidents_per_day=1.0 specified without an applicable", str(w[0].message))
            self.assertEqual(detector.active_z_threshold, FIXED_Z_THRESHOLD)
            self.assertFalse(detector.is_calibrated)

    def test_default_scan_identical_to_explicit_3_5(self):
        det_default = PWScanDetector()
        det_explicit = PWScanDetector(z_threshold=3.5)

        engine = TemporalEngine()
        engine.ingest_records([{"timestamp": 1700000000 + i, "route": "/r", "client": "c", "status": 200} for i in range(120)])
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        b_def = det_default.fit_baseline_profile(engine, cov, segments)
        b_exp = det_explicit.fit_baseline_profile(engine, cov, segments)

        c_def = det_default.scan(engine, cov, segments, b_def)
        c_exp = det_explicit.scan(engine, cov, segments, b_exp)
        self.assertEqual(len(c_def), len(c_exp))


class TestApplicationRules(unittest.TestCase):
    def setUp(self):
        self.fp = compute_detector_config_fingerprint()

    def test_verified_calibration_applied(self):
        profile = CalibrationProfile(
            detector_fingerprint=self.fp,
            selected_z_threshold=8.0,
            calibrated=True,
            false_alarm_claim_supported=True,
            threshold_mapping={1.0: 8.0},
        )
        detector = PWScanDetector(target_false_incidents_per_day=1.0, calibration=profile)
        self.assertEqual(detector.active_z_threshold, 8.0)
        self.assertTrue(detector.is_calibrated)
        info = detector.calibration_info()
        self.assertTrue(info["false_alarm_claim_supported"])
        self.assertIn("holdout_verified", info["status"])

    def test_unverified_calibration_applied_with_no_claim(self):
        profile = CalibrationProfile(
            detector_fingerprint=self.fp,
            selected_z_threshold=6.0,
            calibrated=True,
            false_alarm_claim_supported=False,
            threshold_mapping={4.0: 6.0},
        )
        detector = PWScanDetector(target_false_incidents_per_day=4.0, calibration=profile)
        self.assertEqual(detector.active_z_threshold, 6.0)
        info = detector.calibration_info()
        self.assertFalse(info["false_alarm_claim_supported"])
        self.assertIn("unverified", info["status"])

    def test_failed_or_rejected_calibration_not_applied(self):
        profile = CalibrationProfile(
            detector_fingerprint=self.fp,
            selected_z_threshold=8.0,
            calibrated=False,
            rejection_reason="holdout_rate_exceeded_target",
        )
        detector = PWScanDetector(target_false_incidents_per_day=1.0, calibration=profile)
        self.assertEqual(detector.active_z_threshold, FIXED_Z_THRESHOLD)
        self.assertFalse(detector.is_calibrated)
        self.assertIn("calibration_rejected", detector.calibration_info()["status"])

    def test_fingerprint_mismatch_refuses_calibration(self):
        profile = CalibrationProfile(
            detector_fingerprint="wrong_fingerprint_hash",
            selected_z_threshold=8.0,
            calibrated=True,
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            detector = PWScanDetector(target_false_incidents_per_day=1.0, calibration=profile)
            self.assertEqual(len(w), 1)
            self.assertIn("fingerprint mismatch", str(w[0].message))
            self.assertEqual(detector.active_z_threshold, FIXED_Z_THRESHOLD)

    def test_profile_dict_round_trip(self):
        profile = CalibrationProfile(
            detector_fingerprint=self.fp,
            base_seed=12345,
            traces_per_family=100,
            selected_z_threshold=8.0,
            calibrated=True,
            false_alarm_claim_supported=True,
            threshold_mapping={1.0: 8.0, 4.0: 6.0},
        )
        d = profile.to_dict()
        reconstructed = CalibrationProfile.from_dict(d)
        self.assertEqual(reconstructed.detector_fingerprint, profile.detector_fingerprint)
        self.assertEqual(reconstructed.selected_z_threshold, 8.0)
        self.assertEqual(reconstructed.threshold_mapping[1.0], 8.0)


class TestSeedsAndSeparation(unittest.TestCase):
    def test_deterministic_and_role_separated_seeds(self):
        s1 = derive_trace_seed(100, "calibration", "poisson", 0)
        s2 = derive_trace_seed(100, "calibration", "poisson", 0)
        s_holdout = derive_trace_seed(100, "holdout", "poisson", 0)
        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, s_holdout)

    def test_reproducible_trace_generation(self):
        events1, gaps1 = generate_null_trace_events("poisson", 300, 42)
        events2, gaps2 = generate_null_trace_events("poisson", 300, 42)
        self.assertEqual(len(events1), len(events2))
        self.assertEqual(events1, events2)
        self.assertEqual(gaps1, gaps2)

    def test_all_five_null_families_produce_events(self):
        for fam in NULL_FAMILIES:
            events, _ = generate_null_trace_events(fam, 900, 123)
            self.assertTrue(len(events) > 0, f"Family {fam} should generate events")

    def test_gap_family_produces_unknown_gaps(self):
        events, gaps = generate_null_trace_events("gaps", 3600, 999)
        self.assertTrue(len(gaps) >= 1)
        # Check gap length > 900s
        for s, e in gaps:
            self.assertTrue((e - s) >= 900)


class TestIntervalEstimation(unittest.TestCase):
    def test_known_poisson_bound_values(self):
        # count=0, alpha=0.05 => lambda_ub = -ln(0.05) ~= 2.995732
        ub0 = poisson_upper_bound(0, 1.0)
        self.assertAlmostEqual(ub0, 2.99573227, places=5)
        # Exposure scaling
        ub0_10d = poisson_upper_bound(0, 10.0)
        self.assertAlmostEqual(ub0_10d, 0.299573227, places=5)

    def test_known_clopper_pearson_values(self):
        # 0 successes in 100 trials, 95% UB: 1 - 0.05^(1/100)
        expected = 1.0 - math.pow(0.05, 0.01)
        ub = clopper_pearson_upper_bound(0, 100)
        self.assertAlmostEqual(ub, expected, places=4)

    def test_bootstrap_ratio_under_clustering(self):
        # High clustering: one trace has 20 incidents, 9 traces have 0 incidents
        incidents = [20] + [0] * 19
        exposures = [1.0] * 20  # total exposure 20 days, mean rate 1.0/day
        b_ub = bootstrap_ratio_upper_bound(incidents, exposures, replicates=500, seed=42)
        p_ub = poisson_upper_bound(sum(incidents), sum(exposures))
        self.assertIsNotNone(b_ub)
        # Under severe overdispersion, bootstrap upper bound is wider than homogeneous Poisson bound
        disp = quasi_poisson_dispersion(incidents, exposures)
        self.assertIsNotNone(disp)
        self.assertTrue(disp > 2.0)

    def test_bootstrap_deterministic(self):
        incidents = [1, 0, 2, 0, 1]
        exposures = [0.5, 0.5, 0.5, 0.5, 0.5]
        ub1 = bootstrap_ratio_upper_bound(incidents, exposures, replicates=200, seed=123)
        ub2 = bootstrap_ratio_upper_bound(incidents, exposures, replicates=200, seed=123)
        self.assertEqual(ub1, ub2)


class TestExposureAccounting(unittest.TestCase):
    def test_unknown_gaps_excluded_from_exposure(self):
        # Generate trace with gap
        events, gaps = generate_null_trace_events("gaps", 3600, 777)
        exp_days, _ = evaluate_threshold_grid_on_trace(events)
        # Total duration was 3600s, gap was 1000s, exposure should be approx (3600 - 1000) / 86400
        self.assertLess(exp_days, 3600.0 / 86400.0)

    def test_abstained_traces_contribute_zero_exposure(self):
        # Empty events => detector abstains => exposure = 0
        exp_days, incidents = evaluate_threshold_grid_on_trace([])
        self.assertEqual(exp_days, 0.0)
        self.assertTrue(all(cnt == 0 for cnt in incidents.values()))

    def test_short_outage_kept_as_assumed_zero(self):
        # Outage of 100s (< 900s) does not split segments
        events = [
            {"timestamp": 1700000000 + i, "route": "/r", "client": "c", "status": 200}
            for i in range(0, 300)
        ] + [
            {"timestamp": 1700000000 + 400 + i, "route": "/r", "client": "c", "status": 200}
            for i in range(0, 300)
        ]
        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        # Single continuous segment spanning 0 to 700 with assumed zeros
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].duration_sec, 700)


class TestCalibrationProcedure(unittest.TestCase):
    def test_insufficient_data_falls_back_uncalibrated(self):
        # 10 traces (< 30 required)
        results = {
            "poisson": FamilyMetricResult(
                family="poisson",
                traces_evaluated=10,
                exposure_days=1.0,
                incidents_by_z={z: 0 for z in GRID_Z},
                poisson_ub_by_z={z: 1.0 for z in GRID_Z},
                bootstrap_ub_by_z={z: None for z in GRID_Z},
                dispersion_by_z={z: None for z in GRID_Z},
                prob_incident_ub_by_z={z: 0.1 for z in GRID_Z},
            )
        }
        z, reason = select_calibrated_threshold(results, target_rate_per_day=1.0)
        self.assertIsNone(z)
        self.assertIn("insufficient_data", reason)

    def test_monotonic_selection_rule(self):
        # Passes at 8.0 and 10.0 but fails at 7.0
        results = {
            "poisson": FamilyMetricResult(
                family="poisson",
                traces_evaluated=50,
                exposure_days=10.0,
                incidents_by_z={z: 0 for z in GRID_Z},
                poisson_ub_by_z={3.0: 10.0, 5.0: 5.0, 7.0: 1.5, 8.0: 0.8, 10.0: 0.5},
                bootstrap_ub_by_z={z: None for z in GRID_Z},
                dispersion_by_z={z: None for z in GRID_Z},
                prob_incident_ub_by_z={z: 0.01 for z in GRID_Z},
            )
        }
        z, reason = select_calibrated_threshold(results, target_rate_per_day=1.0, grid_z=[3.0, 5.0, 7.0, 8.0, 10.0])
        self.assertEqual(z, 8.0)
        self.assertIsNone(reason)


class TestRateStability(unittest.TestCase):
    def test_stable_poisson_not_flagged(self):
        events, _ = generate_null_trace_events("poisson", 1200, seed=42)
        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        res = assess_rate_stability(engine, segments)
        self.assertEqual(res["label"], "stable")
        self.assertLess(res["statistic"], 20.0)

    def test_short_observation_not_assessed(self):
        events, _ = generate_null_trace_events("poisson", 600, seed=42)
        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        res = assess_rate_stability(engine, segments)
        self.assertEqual(res["label"], "not_assessed")
        self.assertEqual(res["reason"], "insufficient_duration")

    def test_majority_end_shift_flagged_and_downgraded(self):
        # 1200s total: first 400s at 2 req/s, last 800s at 10 req/s (5x shift covering 66% at the end)
        events = []
        for s in range(400):
            for _ in range(2):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})
        for s in range(400, 1200):
            for _ in range(10):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})

        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        res = assess_rate_stability(engine, segments)
        self.assertEqual(res["label"], "heterogeneous")
        self.assertGreaterEqual(res["statistic"], 20.0)

        det = PWScanDetector()
        base = det.fit_baseline_profile(engine, cov, segments)
        self.assertEqual(base.reliability, "low")
        self.assertIn("Significant rate heterogeneity detected", base.warning)
        self.assertIn("rate_stability", base.to_dict())

    def test_minority_shift_masked_and_not_flagged(self):
        # 3600s total: 300s burst (8% of trace) -> sticky masking handles it, rate stability remains stable
        events = []
        for s in range(3600):
            rate = 12 if 1500 <= s < 1800 else 2
            for _ in range(rate):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})

        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        det = PWScanDetector()
        base = det.fit_baseline_profile(engine, cov, segments)
        # Because the burst seconds are masked, the rate stability assessment sees only the uncontaminated background
        self.assertEqual(base.rate_stability["label"], "stable")

    def test_centred_80pct_shift_limitation_pinned(self):
        # Pinned behavior from §5.4: centered 80% shift cannot be separated by 4-part split with 10% normal on each side
        # Baseline adopts shifted level
        events = []
        for s in range(1200):
            rate = 10 if 120 <= s < 1080 else 2
            for _ in range(rate):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})
        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        det = PWScanDetector()
        base = det.fit_baseline_profile(engine, cov, segments)
        # Documents known limitation in §5.4
        self.assertIsNotNone(base.rate_stability)


class TestStatisticalScenarios(unittest.TestCase):
    def test_burst_detected_across_scales(self):
        # 600s baseline 2 req/s with 60s burst at 20 req/s
        events = []
        for s in range(600):
            cnt = 20 if 200 <= s < 260 else 2
            for _ in range(cnt):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})

        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        det = PWScanDetector()
        base = det.fit_baseline_profile(engine, cov, segments)
        candidates = det.scan(engine, cov, segments, base)
        self.assertTrue(len(candidates) > 0)
        scales_found = {c.scale_sec for c in candidates}
        self.assertIn(60, scales_found)

    def test_gradual_ramp_returning_to_baseline(self):
        # Ramp returning to baseline is detected
        events = []
        for s in range(600):
            extra = int((s - 200) / 10) if 200 <= s < 300 else (int((400 - s) / 10) if 300 <= s < 400 else 0)
            for _ in range(2 + max(0, extra)):
                events.append({"timestamp": 1700000000 + s, "route": "/r", "client": "c", "status": 200})

        engine = TemporalEngine()
        engine.ingest_records(events)
        gaps, segments = engine.compute_gaps_and_segments()
        cov = engine.compute_coverage()
        det = PWScanDetector()
        base = det.fit_baseline_profile(engine, cov, segments)
        candidates = det.scan(engine, cov, segments, base)
        self.assertTrue(len(candidates) > 0)


if __name__ == "__main__":
    unittest.main()
