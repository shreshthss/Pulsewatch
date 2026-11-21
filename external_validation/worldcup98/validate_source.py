"""Validate World Cup 1998 binary reader against official ITA tools and check files.

This script executes the Python adapter against the official 1,000-request fixture
(`test_log.gz`) and verifies that all computed values match the reference output
in `test_log.check` exactly.
"""

import os
import sys

# Ensure repository root is on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from external_validation.worldcup98.adapter import (
    NO_SIZE,
    WorldCupStreamReader,
    compute_profile,
    load_object_mappings,
)


def validate_test_fixture(fixture_path: str, check_path: str) -> bool:
    print(f"Validating fixture: {fixture_path}")
    print(f"Against check file: {check_path}")

    if not os.path.exists(fixture_path):
        print(f"ERROR: Fixture not found at {fixture_path}")
        return False

    with WorldCupStreamReader(fixture_path) as reader:
        profile = compute_profile(reader, track_unique_cardinalities=True)

    # Reference values from test_log.check:
    expected = {
        "total_requests": 1000,
        "total_bytes": 5410216,
        "min_client": 0,
        "max_client": 34607,
        "min_object": 0,
        "max_object": 4505,
        "min_size": 0,
        "max_size": 60349,
        "min_time": 893971817,
        "max_time": 893973655,
        "out_of_order": 0,
        "unique_servers": 3,
        "servers": {32: 664, 96: 247, 97: 89},
    }

    mismatches = []
    if profile.total_records != expected["total_requests"]:
        mismatches.append(f"total_records: got {profile.total_records}, expected {expected['total_requests']}")
    if profile.total_response_bytes != expected["total_bytes"]:
        mismatches.append(f"total_bytes: got {profile.total_response_bytes}, expected {expected['total_bytes']}")
    if profile.min_client_id != expected["min_client"]:
        mismatches.append(f"min_client: got {profile.min_client_id}, expected {expected['min_client']}")
    if profile.max_client_id != expected["max_client"]:
        mismatches.append(f"max_client: got {profile.max_client_id}, expected {expected['max_client']}")
    if profile.min_object_id != expected["min_object"]:
        mismatches.append(f"min_object: got {profile.min_object_id}, expected {expected['min_object']}")
    if profile.max_object_id != expected["max_object"]:
        mismatches.append(f"max_object: got {profile.max_object_id}, expected {expected['max_object']}")
    if profile.min_transfer_size != expected["min_size"]:
        mismatches.append(f"min_size: got {profile.min_transfer_size}, expected {expected['min_size']}")
    if profile.max_transfer_size != expected["max_size"]:
        mismatches.append(f"max_size: got {profile.max_transfer_size}, expected {expected['max_size']}")
    if profile.min_timestamp != expected["min_time"]:
        mismatches.append(f"min_time: got {profile.min_timestamp}, expected {expected['min_time']}")
    if profile.max_timestamp != expected["max_time"]:
        mismatches.append(f"max_time: got {profile.max_timestamp}, expected {expected['max_time']}")
    if profile.out_of_order_timestamps != expected["out_of_order"]:
        mismatches.append(f"out_of_order: got {profile.out_of_order_timestamps}, expected {expected['out_of_order']}")
    if profile.unique_servers_count != expected["unique_servers"]:
        mismatches.append(f"unique_servers: got {profile.unique_servers_count}, expected {expected['unique_servers']}")

    for s_id, s_cnt in expected["servers"].items():
        if profile.servers_distribution.get(s_id) != s_cnt:
            mismatches.append(f"server {s_id} count: got {profile.servers_distribution.get(s_id)}, expected {s_cnt}")

    if mismatches:
        print("VALIDATION FAILED with mismatches:")
        for m in mismatches:
            print("  -", m)
        return False

    print("SUCCESS: Adapter outputs match official checklog reference exactly!")
    print(f"  Total records: {profile.total_records}")
    print(f"  Valid PulseWatch records: {profile.valid_pulsewatch_records}")
    print(f"  Invalid / unmapped records: {profile.invalid_or_unmapped_records}")
    print(f"  Total bytes: {profile.total_response_bytes}")
    print(f"  Mean transfer size: {profile.mean_transfer_size:.6f}")
    print(f"  Timestamp range: {profile.min_timestamp} to {profile.max_timestamp} ({profile.elapsed_seconds}s)")
    return True


if __name__ == "__main__":
    tools_dir = os.path.join(os.path.dirname(__file__), "scratch", "tools", "ita_public_tools")
    fixture = os.path.join(tools_dir, "input", "test_log.gz")
    check = os.path.join(tools_dir, "check", "test_log.check")
    ok = validate_test_fixture(fixture, check)
    sys.exit(0 if ok else 1)
