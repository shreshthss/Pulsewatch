"""PulseWatch Run Manifest generator: reproducible metadata and canonical cryptographic digests.

Implements schema pulsewatch.manifest/1 with canonical JSON serialization,
system environment recording, and cross-artifact digest linkage.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

MANIFEST_SCHEMA_VERSION = "pulsewatch.manifest/1"
MANIFEST_SPEC_VERSION = "1.0.0"


def canonical_json_bytes(obj: Any, exclude_keys: Optional[Set[str]] = None) -> bytes:
    """Deterministically serialize Python objects to UTF-8 JSON bytes with sorted keys and compact separators."""
    exclude = exclude_keys or set()

    def _normalize(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: _normalize(v) for k, v in sorted(val.items()) if k not in exclude}
        if isinstance(val, (list, tuple)):
            return [_normalize(x) for x in val]
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                raise ValueError(f"Non-finite float value not allowed in canonical serialization: {val}")
            # Format float consistently
            return float(f"{val:.8g}")
        return val

    normalized = _normalize(obj)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def canonical_sha256(obj: Any, exclude_keys: Optional[Set[str]] = None) -> str:
    """Compute deterministic SHA-256 hex digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(obj, exclude_keys=exclude_keys)).hexdigest()


def compute_file_sha256(file_path: str) -> Tuple[str, int]:
    """Compute SHA-256 and byte length of a file on disk."""
    h = hashlib.sha256()
    size = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def get_git_info() -> Tuple[str, bool]:
    """Best-effort git commit SHA and dirty flag detection."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode("ascii").strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL
        ).decode("ascii").strip()
        is_dirty = len(status) > 0
        return commit, is_dirty
    except Exception:
        return "unknown", False


def build_run_manifest(
    report_dict: Dict[str, Any],
    report_file_path: str,
    input_file_path: Optional[str] = None,
    dataset_id: Optional[str] = None,
    run_id: Optional[str] = None,
    record_timestamp: bool = False,
) -> Dict[str, Any]:
    """Construct a pulsewatch.manifest/1 payload linking report, input, and environment."""
    report_sha, report_size = compute_file_sha256(report_file_path)

    # Input file digest
    input_sec = report_dict.get("input", {})
    src_label = input_sec.get("source_label", input_file_path or "unknown")
    if input_file_path and os.path.exists(input_file_path):
        input_sha, input_size = compute_file_sha256(input_file_path)
    else:
        input_sha = input_sec.get("source_sha256", "")
        input_size = input_sec.get("file_size_bytes", 0)

    # Git environment
    git_commit, git_dirty = get_git_info()

    # Derived run_id if not supplied
    if not run_id:
        h = hashlib.blake2b(digest_size=8)
        h.update(report_sha.encode("ascii"))
        h.update(input_sha.encode("ascii"))
        run_id = f"pw-run-{h.hexdigest()}"

    # Build manifest body
    manifest: Dict[str, Any] = {
        "manifest_schema": MANIFEST_SCHEMA_VERSION,
        "manifest_version": MANIFEST_SPEC_VERSION,
        "run_id": run_id,
        "analysis_timestamp_utc": report_dict.get("generated_at_utc") if record_timestamp else None,
        "supported_by": "pulsewatch-verify",
        "integrity_note": "Digests and counts in this manifest show agreement with recorded values, not ground-truth correctness.",
        "report": {
            "file_name": os.path.basename(report_file_path),
            "schema_version": report_dict.get("schema_version", "pulsewatch.report/1"),
            "report_revision": report_dict.get("report_revision", 1),
            "pulsewatch_version": report_dict.get("pulsewatch_version", "0.2.0"),
            "analysis_hash": report_dict.get("analysis_hash", ""),
            "file_sha256": report_sha,
            "file_size_bytes": report_size,
        },
        "input": {
            "source_label": src_label,
            "file_sha256": input_sha,
            "file_size_bytes": input_size,
            "dataset_id": dataset_id or "default",
            "lines_read": input_sec.get("total_lines_read", 0),
            "valid_records": input_sec.get("valid_records", 0),
            "retained_records": input_sec.get("retained_records", 0),
            "duplicates_removed": input_sec.get("duplicates_removed", 0),
        },
        "analysis": {
            "detector": "PWScanDetector",
            "config_digest": report_dict.get("detector_config_digest", ""),
            "analysis_config": report_dict.get("analysis_config", {}),
            "calibration_status": report_dict.get("calibration_status", {}),
            "exactness_status": input_sec.get("exactness_status", "unknown"),
            "coverage_ratio": report_dict.get("coverage", {}).get("coverage_ratio", 0.0),
            "incident_count": len(report_dict.get("incidents", [])),
        },
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
        },
    }

    # Compute manifest self-digest excluding manifest_sha256
    m_digest = canonical_sha256(manifest, exclude_keys={"manifest_sha256"})
    manifest["manifest_sha256"] = m_digest
    return manifest


def write_manifest_file(manifest: Dict[str, Any], path: str) -> None:
    """Atomically write manifest JSON to disk."""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
