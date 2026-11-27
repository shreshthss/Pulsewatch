"""Command-line interface for PulseWatch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import warnings
from typing import Any, Optional

from pulsewatch.calibration import load_calibration_profile
from pulsewatch.dedup import DedupAccounting, RetentionLog, iter_deduplicated
from pulsewatch.detector import PWScanDetector
from pulsewatch.html_report import render_html_report
from pulsewatch.incident import (
    SECOND_PASS_INPUT_CHANGED,
    IncidentMerger,
    collect_second_pass_evidence,
)
from pulsewatch.ingest import IngestAccounting, ingest_jsonl_stream
from pulsewatch.manifest import build_run_manifest, write_manifest_file
from pulsewatch.report import format_cli_summary, generate_canonical_report
from pulsewatch.stream_io import HashedTextFile
from pulsewatch.temporal import DEFAULT_GAP_THRESHOLD_SEC, TemporalEngine
from pulsewatch.verify import verify_analysis


def _stream_into_engine(input_path: str, gap_threshold: int):
    """Single streaming pass: ingest -> deduplicate -> temporal aggregation.

    Retained events are never collected into a list; each is folded into the engine's
    sparse buckets and released. Accounting is read only after the stream is exhausted.
    Declared coverage control records can appear anywhere in the file, so they are handed
    to the engine after exhaustion (the engine copies the list it is given).

    The SHA-256 is computed over the bytes actually consumed (and any unread remainder),
    so it always describes the analysed content.

    A one-bit-per-record retention log is kept so the evidence pass can replay the
    retain/drop decisions instead of re-deduplicating.

    Returns (engine, ingest_acc, dedup_acc, input_sha256, retention). Read errors
    (``OSError``) propagate; no partial result is returned.
    """
    ingest_acc = IngestAccounting()
    dedup_acc = DedupAccounting()
    retention = RetentionLog()
    engine = TemporalEngine(gap_threshold_sec=gap_threshold)
    with HashedTextFile(input_path) as src:
        records_iter = ingest_jsonl_stream(src.stream, ingest_acc)
        engine.ingest_records(iter_deduplicated(records_iter, dedup_acc, retention=retention))
        input_sha256 = src.sha256_hexdigest()
    engine.set_declared_coverage(ingest_acc.declared_coverage_intervals)
    return engine, ingest_acc, dedup_acc, input_sha256, retention


def run_pipeline(
    input_path: str,
    gap_threshold: int = DEFAULT_GAP_THRESHOLD_SEC,
    calibration: Optional[Any] = None,
    z_threshold: Optional[float] = None,
    target_false_incidents_per_day: Optional[float] = None,
):
    """Core analysis pipeline execution."""
    engine, ingest_acc, dedup_acc, file_hash, retention = _stream_into_engine(input_path, gap_threshold)

    coverage = engine.compute_coverage()
    gaps, segments = engine.compute_gaps_and_segments()
    windows_60s = engine.generate_60s_windows()

    detector = PWScanDetector(
        calibration=calibration,
        z_threshold=z_threshold,
        target_false_incidents_per_day=target_false_incidents_per_day,
    )
    baseline = detector.fit_baseline_profile(engine, coverage, segments, dedup_acc)
    candidates = detector.scan(engine, coverage, segments, baseline)

    merger = IncidentMerger()
    latest_seg_end = segments[-1].end_sec if segments else None
    incidents = merger.merge_candidates(
        candidates,
        engine,
        baseline.fitted_rate_1s,
        latest_segment_end_sec=latest_seg_end
    )

    # Second pass for contributor evidence & concentration tags. It re-reads the source
    # (rather than holding a second copy of the events) and verifies the bytes it reads
    # against the hash of the first pass.
    second_pass_status = "none"
    if incidents:
        try:
            second_pass_status = collect_second_pass_evidence(
                incidents, input_path, file_hash, retention=retention
            )
        except OSError as exc:
            second_pass_status = f"error: {exc}"
            warnings.warn(
                f"Contributor evidence unavailable: could not re-read '{input_path}' ({exc}).",
                RuntimeWarning,
                stacklevel=2,
            )
        else:
            if second_pass_status == SECOND_PASS_INPUT_CHANGED:
                warnings.warn(
                    f"Input '{input_path}' changed after it was analysed; contributor evidence "
                    "omitted so that evidence from a different version is not mixed into the report.",
                    RuntimeWarning,
                    stacklevel=2,
                )

    analysis_config = {
        "screening_z_threshold": detector.screening_z_threshold,
        "min_elevation_ratio": detector.min_elevation_ratio,
        "min_excess_counts": detector.min_excess_counts,
        "scales": [10, 60, 300],
        "gap_threshold_sec": gap_threshold,
    }
    calibration_status = {
        "is_calibrated": detector.is_calibrated,
        "status_message": detector.calibration_status_message,
        "active_z_threshold": detector.active_z_threshold,
    }

    report = generate_canonical_report(
        input_file=input_path,
        input_sha256=file_hash,
        ingest_acc=ingest_acc,
        dedup_acc=dedup_acc,
        coverage=coverage,
        windows_60s=windows_60s,
        baseline=baseline,
        candidates=candidates,
        incidents=incidents,
        report_revision=2,
        detector_config_digest=detector.config_fingerprint(),
        analysis_config=analysis_config,
        calibration_status=calibration_status,
        second_pass_status=second_pass_status,
    )
    return report, engine, windows_60s, coverage, dedup_acc


def cmd_summarize(args: argparse.Namespace) -> int:
    input_path = args.input
    if not os.path.exists(input_path):
        sys.stderr.write(f"Error: input file '{input_path}' not found.\n")
        return 1

    gap_thresh = args.gap_threshold if hasattr(args, "gap_threshold") and args.gap_threshold else DEFAULT_GAP_THRESHOLD_SEC
    try:
        engine, ingest_acc, dedup_acc, _, _ = _stream_into_engine(input_path, gap_thresh)
    except OSError as exc:
        sys.stderr.write(f"Error: could not read input '{input_path}': {exc}\n")
        return 1
    coverage = engine.compute_coverage()
    windows_60s = engine.generate_60s_windows()

    print("=" * 76)
    print(f"PULSEWATCH TRAFFIC SUMMARY: {input_path}")
    print("=" * 76)
    print(f"Total lines read:    {ingest_acc.total_lines_read}")
    print(f"Valid records:       {ingest_acc.valid_records}")
    print(f"Duplicates removed:  {dedup_acc.duplicates_removed}")
    print(f"Retained records:    {dedup_acc.retained_records}")
    print(f"Exactness status:    {dedup_acc.exactness_status}")
    print(f"Active seconds:      {coverage.active_seconds}")
    print(f"Known zeros:         {coverage.known_zero_seconds}s")
    print(f"Assumed zeros:       {coverage.assumed_zero_seconds}s")
    print(f"Coverage ratio:      {coverage.coverage_ratio * 100:.1f}%")
    print(f"Unknown gaps:        {len(coverage.unknown_gaps)} (threshold: {gap_thresh}s)")
    print("-" * 76)
    print(f"UTC 60s WINDOWS ({len(windows_60s)} total):")
    print(f"{'WINDOW START (UTC)':<24} {'REQS':<8} {'ACTIVE':<9} {'STATUS 2xx/4xx/5xx':<20} {'COMPLETE?'}")
    print("-" * 76)
    for w in windows_60s[: args.limit]:
        status_str = f"{w.status_2xx}/{w.status_4xx}/{w.status_5xx}"
        comp_str = "Yes" if w.is_complete else "No (Partial/Gap)"
        print(f"{w.start_iso:<24} {w.total_requests:<8} {w.active_seconds_count:<9} {status_str:<20} {comp_str}")
    if len(windows_60s) > args.limit:
        print(f"... and {len(windows_60s) - args.limit} more windows.")
    print("=" * 76)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    input_path = args.input
    if not os.path.exists(input_path):
        sys.stderr.write(f"Error: input file '{input_path}' not found.\n")
        return 1

    gap_thresh = args.gap_threshold if hasattr(args, "gap_threshold") and args.gap_threshold else DEFAULT_GAP_THRESHOLD_SEC

    calib_obj = None
    if getattr(args, "calibration", None):
        if not os.path.exists(args.calibration):
            sys.stderr.write(f"Error: calibration file '{args.calibration}' not found.\n")
            return 1
        try:
            calib_obj = load_calibration_profile(args.calibration)
        except Exception as exc:
            sys.stderr.write(f"Error loading calibration profile '{args.calibration}': {exc}\n")
            return 1

    try:
        report, _, _, _, _ = run_pipeline(
            input_path,
            gap_threshold=gap_thresh,
            calibration=calib_obj,
        )
    except OSError as exc:
        sys.stderr.write(f"Error: could not read input '{input_path}': {exc}\n")
        return 1

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
        print(f"Canonical report written to: {args.json}")

        write_manifest = getattr(args, "manifest", True)
        if write_manifest:
            manifest_path = os.path.splitext(args.json)[0] + ".manifest.json"
            manifest = build_run_manifest(
                report_dict=report,
                report_file_path=args.json,
                input_file_path=input_path,
                dataset_id=getattr(args, "dataset_id", None),
                run_id=getattr(args, "run_id", None),
                record_timestamp=getattr(args, "record_timestamp", False),
            )
            write_manifest_file(manifest, manifest_path)
            print(f"Sidecar manifest written to: {manifest_path}")

    print(format_cli_summary(report))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    report_path = args.report_json
    if not os.path.exists(report_path):
        sys.stderr.write(f"Error: report JSON file '{report_path}' not found.\n")
        return 1

    with open(report_path, "r", encoding="utf-8") as f:
        try:
            report_data = json.load(f)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Error reading JSON report: {e}\n")
            return 1

    manifest_data = None
    manifest_arg = getattr(args, "manifest", None)
    if manifest_arg and os.path.exists(manifest_arg):
        with open(manifest_arg, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    else:
        cand_manifest = os.path.splitext(report_path)[0] + ".manifest.json"
        if os.path.exists(cand_manifest):
            try:
                with open(cand_manifest, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
            except Exception:
                pass

    ablation_data = None
    ablation_arg = getattr(args, "ablation", None)
    if ablation_arg and os.path.exists(ablation_arg):
        with open(ablation_arg, "r", encoding="utf-8") as f:
            ablation_data = json.load(f)

    if args.html:
        html_out = render_html_report(
            report_data,
            manifest_data=manifest_data,
            ablation_data=ablation_data,
        )
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"HTML report rendered to: {args.html}")

    print(format_cli_summary(report_data))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report_path = args.report_json
    summary = verify_analysis(
        report_path=report_path,
        manifest_path=args.manifest,
        input_path=args.input,
        reproduce=args.reproduce,
    )
    summary.print_summary()
    return summary.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pulsewatch", description="PulseWatch API Traffic Monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # summarize
    p_sum = subparsers.add_parser("summarize", help="Show deduplicated window summaries")
    p_sum.add_argument("input", help="Path to input JSONL file")
    p_sum.add_argument("--limit", type=int, default=50, help="Max windows to display (default: 50)")
    p_sum.add_argument("--gap-threshold", type=int, default=DEFAULT_GAP_THRESHOLD_SEC, help=f"Unknown gap threshold in seconds (default: {DEFAULT_GAP_THRESHOLD_SEC})")
    p_sum.set_defaults(func=cmd_summarize)

    # analyze
    p_ana = subparsers.add_parser("analyze", help="Analyze traffic for anomalies and generate report")
    p_ana.add_argument("input", help="Path to input JSONL file")
    p_ana.add_argument("--json", help="Path to write canonical JSON report")
    p_ana.add_argument("--gap-threshold", type=int, default=DEFAULT_GAP_THRESHOLD_SEC, help=f"Unknown gap threshold in seconds (default: {DEFAULT_GAP_THRESHOLD_SEC})")
    p_ana.add_argument("--manifest", action=argparse.BooleanOptionalAction, default=True, help="Generate sidecar .manifest.json (default: True)")
    p_ana.add_argument("--run-id", help="Explicit run identifier for manifest")
    p_ana.add_argument("--dataset-id", help="Dataset identifier for manifest")
    p_ana.add_argument("--record-timestamp", action="store_true", help="Record wall-clock timestamp in manifest")
    p_ana.add_argument("--calibration", help="Path to calibration profile JSON (pulsewatch.calibration/1)")
    p_ana.set_defaults(func=cmd_analyze)

    # report
    p_rep = subparsers.add_parser("report", help="Render report from analysis JSON")
    p_rep.add_argument("report_json", help="Path to canonical analysis.json file")
    p_rep.add_argument("--html", help="Path to write HTML report")
    p_rep.add_argument("--manifest", help="Path to sidecar manifest file")
    p_rep.add_argument("--ablation", help="Path to ablation results JSON file")
    p_rep.set_defaults(func=cmd_report)

    # verify
    p_ver = subparsers.add_parser("verify", help="Verify cryptographic and structural integrity of reports and manifests")
    p_ver.add_argument("report_json", help="Path to canonical report.json file")
    p_ver.add_argument("--manifest", help="Path to sidecar manifest JSON (default: <report>.manifest.json if exists)")
    p_ver.add_argument("--input", help="Path to raw input JSONL file for byte and reproduction verification")
    p_ver.add_argument("--reproduce", action="store_true", help="Execute bit-identical reproduction check against input file")
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
