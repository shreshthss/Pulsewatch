"""Tests for pulsewatch.ingest."""

import io
import unittest
from pulsewatch.ingest import (
    EventIdentity,
    IngestAccounting,
    ingest_jsonl_stream,
    parse_record_dict,
    parse_timestamp_to_sec,
)


class TestIngest(unittest.TestCase):
    def test_parse_timestamp_iso(self):
        ts = parse_timestamp_to_sec("2026-11-21T05:00:00Z")
        self.assertIsNotNone(ts)
        self.assertEqual(ts, 1795237200)

        ts_offset = parse_timestamp_to_sec("2026-11-21T06:00:00+01:00")
        self.assertEqual(ts, ts_offset)

    def test_parse_timestamp_epoch(self):
        self.assertEqual(parse_timestamp_to_sec(1700000000), 1700000000)
        self.assertEqual(parse_timestamp_to_sec("1700000000"), 1700000000)
        # ms
        self.assertEqual(parse_timestamp_to_sec(1700000000000), 1700000000)
        self.assertEqual(parse_timestamp_to_sec("1700000000000"), 1700000000)

    def test_parse_invalid_timestamp(self):
        self.assertIsNone(parse_timestamp_to_sec("invalid-time"))
        self.assertIsNone(parse_timestamp_to_sec(""))
        self.assertIsNone(parse_timestamp_to_sec(None))

    def test_ingest_stream_valid_and_invalid(self):
        jsonl_data = """
{"timestamp": "2026-11-21T05:00:00Z", "method": "GET", "route": "/api/v1/items", "client": "192.168.1.1", "status": 200, "source": "gw1", "seq": 101}

{"timestamp": 1795237201, "method": "post", "route": "/api/v1/checkout", "client": "10.0.0.5", "status": 201, "source": "gw1", "request_id": "req-xyz"}
not valid json
{"incomplete": "record"}
{"timestamp": "2026-11-21T05:00:02Z", "method": "GET", "route": "/api/v1/test", "client": "c1", "status": 999}
"""
        stream = io.StringIO(jsonl_data)
        accounting = IngestAccounting()
        records = list(ingest_jsonl_stream(stream, accounting))

        self.assertEqual(len(records), 2)
        self.assertEqual(accounting.valid_records, 2)
        self.assertEqual(accounting.empty_lines, 2)
        self.assertEqual(accounting.malformed_json_lines, 1)
        self.assertEqual(accounting.schema_violations, 2)  # incomplete + status 999

        rec1 = records[0]
        self.assertEqual(rec1.method, "GET")
        self.assertEqual(rec1.route, "/api/v1/items")
        self.assertTrue(rec1.identity.has_usable_identity)
        self.assertEqual(rec1.identity.identity_key(), ("source_seq", "gw1", "101"))

        rec2 = records[1]
        self.assertEqual(rec2.method, "POST")
        self.assertEqual(rec2.identity.identity_key(), ("source_req", "gw1", "req-xyz"))


if __name__ == "__main__":
    unittest.main()
