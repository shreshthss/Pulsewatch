"""Unit tests for the 1998 World Cup binary adapter and PulseWatch converter."""

import io
import json
import os
import struct
import sys
import tempfile
import unittest
import importlib.util

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_adapter_path = os.path.join(_repo_root, "external_validation", "worldcup98", "adapter.py")
_spec = importlib.util.spec_from_file_location("wc98_adapter", _adapter_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["wc98_adapter"] = _mod
_spec.loader.exec_module(_mod)

NO_SIZE = _mod.NO_SIZE
RECORD_FORMAT = _mod.RECORD_FORMAT
RECORD_SIZE = _mod.RECORD_SIZE
RawWorldCupRecord = _mod.RawWorldCupRecord
WorldCupStreamReader = _mod.WorldCupStreamReader
compute_profile = _mod.compute_profile
decode_record = _mod.decode_record
extract_window_to_jsonl = _mod.extract_window_to_jsonl
sha256_file = _mod.sha256_file


class TestWorldCupAdapter(unittest.TestCase):
    """Test suite covering binary decoding, official mappings, and PulseWatch formatting."""

    def _pack_record(
        self,
        ts: int = 895183201,
        client: int = 12345,
        obj: int = 6789,
        size: int = 4096,
        method: int = 0,      # GET
        status_raw: int = 66,  # 0x40 (HTTP/1.0) | 2 (SC_200) -> 66
        ftype: int = 0,       # HTML
        server: int = 33,     # region 1 (PLANO, 0x20) | server 1 -> 33
    ) -> bytes:
        return struct.pack(RECORD_FORMAT, ts, client, obj, size, method, status_raw, ftype, server)

    def test_record_size_invariant(self):
        """Invariant: exact 20-byte big-endian record size."""
        self.assertEqual(RECORD_SIZE, 20)
        packed = self._pack_record()
        self.assertEqual(len(packed), 20)

    def test_big_endian_decoding_and_utc_preservation(self):
        """Verify big-endian byte decoding and exact UTC Unix timestamp preservation."""
        ts = 900258200
        client = 0x12345678
        obj = 0x0ABCDEF0
        size = 1048576
        method = 0  # GET
        status_raw = (1 << 6) | 2  # HTTP/1.0 + 200 OK -> 66
        ftype = 1  # IMAGE
        server = (3 << 5) | 5  # PARIS + server 5 -> 101

        raw = struct.pack(">IIII4B", ts, client, obj, size, method, status_raw, ftype, server)
        rec = decode_record(raw)

        self.assertEqual(rec.timestamp, ts)
        self.assertEqual(rec.client_id, client)
        self.assertEqual(rec.object_id, obj)
        self.assertEqual(rec.size, size)
        self.assertEqual(rec.method_name, "GET")
        self.assertEqual(rec.http_version, "HTTP/1.0")
        self.assertEqual(rec.status_code, 200)
        self.assertEqual(rec.file_type, "IMAGE")
        self.assertEqual(rec.region_name, "PARIS")
        self.assertEqual(rec.server_id, 5)

    def test_official_status_and_method_mappings(self):
        """Verify mapped methods and status codes according to definitions.h."""
        # Test methods: GET(0), HEAD(1), POST(2), OTHER(8)
        methods = {0: "GET", 1: "HEAD", 2: "POST", 8: "OTHER"}
        for code, expected_name in methods.items():
            b = self._pack_record(method=code)
            rec = decode_record(b)
            self.assertEqual(rec.method_name, expected_name)

        # Test statuses: 200 (index 2), 304 (index 13), 404 (index 19), 500 (index 31), other (37)
        statuses = {
            2: 200,
            13: 304,
            19: 404,
            31: 500,
            37: None,  # OTHER_CODES
        }
        for code_idx, expected_status in statuses.items():
            raw_st = (1 << 6) | code_idx  # HTTP/1.0
            b = self._pack_record(status_raw=raw_st)
            rec = decode_record(b)
            self.assertEqual(rec.status_code, expected_status)

    def test_no_size_handling(self):
        """Verify NO_SIZE (0xffffffff) is recognized and excluded from byte totals."""
        b = self._pack_record(size=NO_SIZE)
        rec = decode_record(b)
        self.assertFalse(rec.has_valid_size)
        self.assertEqual(rec.size, 0xFFFFFFFF)

    def test_truncated_and_malformed_records(self):
        """Ensure reader gracefully reports truncated tail bytes and rejects malformed inputs."""
        # 2 complete records (40 bytes) + 7 stray bytes = 47 bytes
        rec1 = self._pack_record(ts=1000)
        rec2 = self._pack_record(ts=1001)
        truncated_bytes = b"1234567"
        data = rec1 + rec2 + truncated_bytes

        stream = io.BytesIO(data)
        reader = WorldCupStreamReader(stream, is_gzip=False)
        records = list(reader.iter_records())

        self.assertEqual(len(records), 2)
        self.assertEqual(reader.truncated_bytes, 7)
        self.assertEqual(reader.records_read, 2)

        # decode_record on too-short data raises ValueError
        with self.assertRaises(ValueError):
            decode_record(b"short")

    def test_absence_of_fabricated_identity_and_valid_pulsewatch_schema(self):
        """Verify converted events contain no fabricated identity or coverage fields."""
        rec_bytes = self._pack_record(ts=895183200, client=999, obj=42, status_raw=(1 << 6) | 2)

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = os.path.join(tmpdir, "input.bin")
            with open(bin_path, "wb") as f:
                f.write(rec_bytes)

            jsonl_path = os.path.join(tmpdir, "output.jsonl")
            profile, _ = extract_window_to_jsonl(
                source_path=bin_path,
                output_path=jsonl_path,
                start_ts=895183200,
                end_ts=895183200,
                object_mappings={42: "/path/to/resource.html"},
            )

            self.assertEqual(profile.total_records, 1)
            self.assertEqual(profile.valid_pulsewatch_records, 1)

            with open(jsonl_path, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                event = json.loads(line)

            # Invariant: Must contain standard fields
            self.assertEqual(event["timestamp"], 895183200)
            self.assertEqual(event["method"], "GET")
            self.assertEqual(event["route"], "/path/to/resource.html")
            self.assertEqual(event["client"], "client:999")
            self.assertEqual(event["status"], 200)

            # Invariant: Must NOT contain any fabricated request-level identity fields
            forbidden_fields = {"source", "seq", "trace_id", "span_id", "request_id", "_pw"}
            present_forbidden = forbidden_fields.intersection(event.keys())
            self.assertEqual(
                present_forbidden,
                set(),
                f"Forbidden fabricated fields present in output: {present_forbidden}",
            )

    def test_window_extraction_filtering_and_determinism(self):
        """Verify deterministic interval selection by timestamp bounds."""
        records = [
            self._pack_record(ts=100, client=1),
            self._pack_record(ts=200, client=2),
            self._pack_record(ts=300, client=3),
            self._pack_record(ts=400, client=4),
            self._pack_record(ts=500, client=5),
        ]
        data = b"".join(records)

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = os.path.join(tmpdir, "test.bin")
            with open(bin_path, "wb") as f:
                f.write(data)

            # Extract window [200, 400] -> should get records 2, 3, 4
            out_path1 = os.path.join(tmpdir, "out1.jsonl")
            prof1, hash1 = extract_window_to_jsonl(bin_path, out_path1, start_ts=200, end_ts=400)
            self.assertEqual(prof1.total_records, 3)
            self.assertEqual(prof1.min_timestamp, 200)
            self.assertEqual(prof1.max_timestamp, 400)

            # Re-run extraction -> output hash must be identical
            out_path2 = os.path.join(tmpdir, "out2.jsonl")
            prof2, hash2 = extract_window_to_jsonl(bin_path, out_path2, start_ts=200, end_ts=400)
            self.assertEqual(hash1, hash2)


if __name__ == "__main__":
    unittest.main()
