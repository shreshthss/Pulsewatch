"""Tests for pulsewatch.dedup."""

import unittest
from pulsewatch.dedup import DedupAccounting, Deduplicator, deduplicate_records
from pulsewatch.ingest import EventIdentity, EventRecord


class TestDedup(unittest.TestCase):
    def test_dedup_exact_duplicate_with_identity(self):
        id1 = EventIdentity(source="s1", seq=100)
        rec1 = EventRecord(1000, "GET", "/api/data", "c1", 200, id1)
        rec2 = EventRecord(1000, "GET", "/api/data", "c1", 200, id1)

        accounting = DedupAccounting()
        retained, acc = deduplicate_records([rec1, rec2], accounting)
        self.assertEqual(len(retained), 1)
        self.assertEqual(acc.duplicate_records_removed, 1)
        self.assertEqual(acc.verified_unique_records, 1)

    def test_no_dedup_without_usable_identity(self):
        # Two records identical in timestamp, method, route, client, status, but no usable identity
        id_none = EventIdentity(source=None)
        rec1 = EventRecord(1000, "GET", "/api/data", "c1", 200, id_none)
        rec2 = EventRecord(1000, "GET", "/api/data", "c1", 200, id_none)

        accounting = DedupAccounting()
        retained, acc = deduplicate_records([rec1, rec2], accounting)
        self.assertEqual(len(retained), 2)
        self.assertEqual(acc.duplicate_records_removed, 0)
        self.assertEqual(acc.unverified_identity_records, 2)

    def test_identity_conflict_not_dropped(self):
        # Same identity, different status
        id1 = EventIdentity(source="s1", request_id="req-1")
        rec1 = EventRecord(1000, "GET", "/api/data", "c1", 200, id1)
        rec2 = EventRecord(1000, "GET", "/api/data", "c1", 500, id1)

        accounting = DedupAccounting()
        retained, acc = deduplicate_records([rec1, rec2], accounting)
        self.assertEqual(len(retained), 2)
        self.assertEqual(acc.duplicate_records_removed, 0)
        self.assertEqual(acc.identity_conflict_records, 1)

    def test_bounded_memory_eviction(self):
        accounting = DedupAccounting()
        dedup = Deduplicator(max_keys=2)

        id1 = EventIdentity(source="s1", seq=1)
        id2 = EventIdentity(source="s1", seq=2)
        id3 = EventIdentity(source="s1", seq=3)

        r1 = EventRecord(1000, "GET", "/a", "c1", 200, id1)
        r2 = EventRecord(1001, "GET", "/b", "c1", 200, id2)
        r3 = EventRecord(1002, "GET", "/c", "c1", 200, id3)

        dedup.process_record(r1, accounting)
        dedup.process_record(r2, accounting)
        self.assertFalse(accounting.dedup_degraded)

        # 3rd key should trigger eviction
        dedup.process_record(r3, accounting)
        self.assertTrue(accounting.dedup_degraded)
        self.assertTrue(accounting.memory_cap_hit)
        self.assertEqual(accounting.evicted_keys_count, 1)


if __name__ == "__main__":
    unittest.main()
