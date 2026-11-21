"""Deterministic window selection and extraction for World Cup 1998 access logs.

Selects three contiguous 15-minute (900-second) windows from Day 20, Day 46, and Day 78,
converts each record to PulseWatch-compatible JSONL, profiles the traffic,
and generates the reproducible dataset manifest.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict

# Ensure repository root is on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from external_validation.worldcup98.adapter import (
    DatasetProfile,
    extract_window_to_jsonl,
    load_object_mappings,
    sha256_file,
)

# Defined 15-minute (900s) windows
SAMPLES = [
    {
        "id": "day20_early",
        "day": 20,
        "date_utc": "1998-05-15",
        "phase": "Early collection (pre-tournament baseline)",
        "source_file": "wc_day20_1.gz",
        "start_ts": 895183201,
        "end_ts": 895184101,  # 900 seconds
        "target_jsonl": "day20_sample.jsonl",
    },
    {
        "id": "day46_opening",
        "day": 46,
        "date_utc": "1998-06-10",
        "phase": "Mid-collection (tournament opening day: Brazil vs Scotland)",
        "source_file": "wc_day46_1.gz",
        "start_ts": 897429601,
        "end_ts": 897430501,  # 900 seconds
        "target_jsonl": "day46_sample.jsonl",
    },
    {
        "id": "day78_final",
        "day": 78,
        "date_utc": "1998-07-12",
        "phase": "Late collection (tournament final match day: France vs Brazil)",
        "source_file": "wc_day78_1.gz",
        "start_ts": 900194401,
        "end_ts": 900195301,  # 900 seconds
        "target_jsonl": "day78_sample.jsonl",
    },
]


def run_window_selection() -> Dict[str, Any]:
    base_dir = os.path.dirname(__file__)
    raw_dir = os.path.join(base_dir, "raw")
    scratch_dir = os.path.join(base_dir, "scratch")
    results_dir = os.path.join(base_dir, "results")
    mappings_file = os.path.join(scratch_dir, "tools", "ita_public_tools", "state", "object_mappings.sort")

    os.makedirs(scratch_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)

    mappings = {}
    if os.path.exists(mappings_file):
        print(f"Loading object mappings from {mappings_file}...")
        mappings = load_object_mappings(mappings_file)
        print(f"Loaded {len(mappings)} object mappings.")
    else:
        print("Notice: object_mappings.sort not found; falling back to /object/<objectID>")

    manifest: Dict[str, Any] = {
        "dataset_name": "1998 World Cup Web Site Access Logs",
        "source_citation": "M. Arlitt and T. Jin, '1998 World Cup Web Site Access Logs', August 1998.",
        "source_archive_url": "https://ita.ee.lbl.gov/html/contrib/WorldCup.html",
        "raw_files": {},
        "windows": [],
    }

    # Hash source raw files
    for sample in SAMPLES:
        fname = sample["source_file"]
        src_path = os.path.join(raw_dir, fname)
        if os.path.exists(src_path) and fname not in manifest["raw_files"]:
            print(f"Hashing raw file {fname}...")
            manifest["raw_files"][fname] = {
                "size_bytes": os.path.getsize(src_path),
                "sha256": sha256_file(src_path),
            }

    # Also record tools archive hash if available
    tools_archive = os.path.join(scratch_dir, "WorldCup_tools.tar.gz")
    if os.path.exists(tools_archive):
        manifest["raw_files"]["WorldCup_tools.tar.gz"] = {
            "size_bytes": os.path.getsize(tools_archive),
            "sha256": sha256_file(tools_archive),
        }

    for sample in SAMPLES:
        fname = sample["source_file"]
        src_path = os.path.join(raw_dir, fname)
        out_path = os.path.join(scratch_dir, sample["target_jsonl"])

        print(f"\nExtracting window for {sample['id']} ({sample['phase']}):")
        print(f"  Source: {src_path}")
        print(f"  Time range: {sample['start_ts']} to {sample['end_ts']} (span={sample['end_ts'] - sample['start_ts']}s)")
        print(f"  Target: {out_path}")

        t0 = time.time()
        profile, out_hash = extract_window_to_jsonl(
            source_path=src_path,
            output_path=out_path,
            start_ts=sample["start_ts"],
            end_ts=sample["end_ts"],
            object_mappings=mappings,
        )
        t_elapsed = time.time() - t0

        print(f"  Extracted {profile.total_records} records in {t_elapsed:.2f}s")
        print(f"  Valid PulseWatch events: {profile.valid_pulsewatch_records}")
        print(f"  Invalid / unmapped events: {profile.invalid_or_unmapped_records}")
        print(f"  Unique clients: {profile.unique_clients_count}")
        print(f"  Unique routes: {profile.unique_objects_count}")
        print(f"  Output SHA-256: {out_hash}")

        window_entry = {
            "id": sample["id"],
            "day": sample["day"],
            "date_utc": sample["date_utc"],
            "phase": sample["phase"],
            "source_file": sample["source_file"],
            "source_file_sha256": manifest["raw_files"].get(sample["source_file"], {}).get("sha256"),
            "target_jsonl": sample["target_jsonl"],
            "target_sha256": out_hash,
            "target_size_bytes": os.path.getsize(out_path),
            "window_start_utc_ts": sample["start_ts"],
            "window_end_utc_ts": sample["end_ts"],
            "window_duration_seconds": sample["end_ts"] - sample["start_ts"],
            "extraction_seconds": round(t_elapsed, 3),
            "profile": profile.to_dict(),
        }
        manifest["windows"].append(window_entry)

    manifest_path = os.path.join(results_dir, "dataset_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest written to: {manifest_path}")

    return manifest


if __name__ == "__main__":
    run_window_selection()
