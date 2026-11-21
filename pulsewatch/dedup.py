"""Event normalization and identity-aware bounded deduplication with 16-byte BLAKE2b hashing."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from pulsewatch.ingest import EventRecord

DEFAULT_MAX_DEDUP_KEYS = 200_000
DEFAULT_MAX_EVICTED_HISTORY = 100_000


def hash_identity_key(key_tuple: Tuple[str, ...]) -> bytes:
    """Compute 16-byte BLAKE2b digest for an identity tuple."""
    h = hashlib.blake2b(digest_size=16)
    for part in key_tuple:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.digest()


def hash_normalized_attributes(attr: Tuple[int, str, str, str, int]) -> bytes:
    """Compute 16-byte BLAKE2b digest for event attributes."""
    ts_sec, method, route, client, status = attr
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{ts_sec}|{method}|{route}|{client}|{status}".encode("utf-8"))
    return h.digest()


class DedupAccounting:
    def __init__(
        self,
        total_input_records: int = 0,
        retained_records: int = 0,
        verified_unique_records: int = 0,
        duplicates_removed: int = 0,
        identity_ineligible: int = 0,
        identity_conflicts: int = 0,
        unverifiable_due_to_eviction: int = 0,
        cap_degraded: bool = False,
        evicted_keys_count: int = 0,
        warnings: Optional[List[str]] = None,
        # Legacy keyword compatibility
        duplicate_records_removed: Optional[int] = None,
        unverified_identity_records: Optional[int] = None,
        identity_conflict_records: Optional[int] = None,
        dedup_degraded: Optional[bool] = None,
    ):
        self.total_input_records = total_input_records
        self.retained_records = retained_records
        self.verified_unique_records = verified_unique_records
        self.duplicates_removed = duplicate_records_removed if duplicate_records_removed is not None else duplicates_removed
        self.identity_ineligible = unverified_identity_records if unverified_identity_records is not None else identity_ineligible
        self.identity_conflicts = identity_conflict_records if identity_conflict_records is not None else identity_conflicts
        self.unverifiable_due_to_eviction = unverifiable_due_to_eviction
        self.cap_degraded = dedup_degraded if dedup_degraded is not None else cap_degraded
        self.evicted_keys_count = evicted_keys_count
        self.warnings = list(warnings or [])

    @property
    def duplicate_records_removed(self) -> int:
        return self.duplicates_removed

    @duplicate_records_removed.setter
    def duplicate_records_removed(self, val: int) -> None:
        self.duplicates_removed = val

    @property
    def unverified_identity_records(self) -> int:
        return self.identity_ineligible

    @unverified_identity_records.setter
    def unverified_identity_records(self, val: int) -> None:
        self.identity_ineligible = val

    @property
    def identity_conflict_records(self) -> int:
        return self.identity_conflicts

    @identity_conflict_records.setter
    def identity_conflict_records(self, val: int) -> None:
        self.identity_conflicts = val

    @property
    def dedup_degraded(self) -> bool:
        return self.cap_degraded

    @dedup_degraded.setter
    def dedup_degraded(self, val: bool) -> None:
        self.cap_degraded = val

    @property
    def memory_cap_hit(self) -> bool:
        return self.cap_degraded

    @memory_cap_hit.setter
    def memory_cap_hit(self, val: bool) -> None:
        self.cap_degraded = val

    @property
    def memory_cap_hit(self) -> bool:
        return self.cap_degraded

    @memory_cap_hit.setter
    def memory_cap_hit(self, val: bool) -> None:
        self.cap_degraded = val

    @property
    def exactness_status(self) -> str:
        if self.cap_degraded:
            return "degraded_due_to_capacity_eviction"
        if self.identity_ineligible > 0:
            return "partial_identity_coverage"
        if self.identity_conflicts > 0:
            return "conflicts_detected"
        return "fully_verified_exact"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_input_records": self.total_input_records,
            "retained_records": self.retained_records,
            "verified_unique_records": self.verified_unique_records,
            "duplicates_removed": self.duplicates_removed,
            "identity_ineligible": self.identity_ineligible,
            "identity_conflicts": self.identity_conflicts,
            "unverifiable_due_to_eviction": self.unverifiable_due_to_eviction,
            "cap_degraded": self.cap_degraded,
            "evicted_keys_count": self.evicted_keys_count,
            "exactness_status": self.exactness_status,
            # Backwards-compatible keys
            "duplicate_records_removed": self.duplicates_removed,
            "unverified_identity_records": self.identity_ineligible,
            "identity_conflict_records": self.identity_conflicts,
            "dedup_degraded": self.cap_degraded,
            "warnings_sample": self.warnings[:5],
        }


class Deduplicator:
    """Bounded-memory identity deduplicator respecting identity hierarchy with 16-byte BLAKE2b hashing."""

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_DEDUP_KEYS,
        max_evicted_history: int = DEFAULT_MAX_EVICTED_HISTORY
    ):
        self.max_keys = max_keys
        self.max_evicted_history = max_evicted_history
        # Cache stores: 16-byte identity_hash -> 16-byte attr_hash
        self._seen: OrderedDict[bytes, bytes] = OrderedDict()
        # Bounded set of evicted identity hashes to detect recurrences
        self._evicted: OrderedDict[bytes, bool] = OrderedDict()

    def process_record(
        self,
        record: EventRecord,
        accounting: DedupAccounting
    ) -> Optional[EventRecord]:
        """Process one record. Returns record if retained, None if deduplicated."""
        accounting.total_input_records += 1

        if not record.identity.has_usable_identity:
            # Cannot verify identity; do NOT drop based solely on attributes
            accounting.identity_ineligible += 1
            accounting.retained_records += 1
            return record

        key_tuple = record.identity.identity_key()
        if key_tuple is None:
            accounting.identity_ineligible += 1
            accounting.retained_records += 1
            return record

        id_hash = hash_identity_key(key_tuple)
        attr_hash = hash_normalized_attributes(record.normalized_attributes())

        if id_hash in self._seen:
            existing_attr_hash = self._seen[id_hash]
            self._seen.move_to_end(id_hash)
            if existing_attr_hash == attr_hash:
                # Genuine duplicate
                accounting.duplicates_removed += 1
                return None
            else:
                # Same identity, but conflicting event attributes!
                accounting.identity_conflicts += 1
                if len(accounting.warnings) < 10:
                    accounting.warnings.append(
                        f"Identity collision with divergent attributes for key {key_tuple}"
                    )
                accounting.retained_records += 1
                return record

        # Not in current cache; check if it was previously evicted
        if id_hash in self._evicted:
            accounting.unverifiable_due_to_eviction += 1
            accounting.cap_degraded = True
            # Cannot safely claim duplicate or unique; retain record
            accounting.retained_records += 1
            return record

        # New key: check capacity
        if len(self._seen) >= self.max_keys:
            evicted_id, _ = self._seen.popitem(last=False)
            accounting.cap_degraded = True
            accounting.evicted_keys_count += 1
            # Add to evicted history tracking
            if len(self._evicted) >= self.max_evicted_history:
                self._evicted.popitem(last=False)
            self._evicted[evicted_id] = True

            if len(accounting.warnings) < 10:
                accounting.warnings.append(
                    f"Deduplication capacity ({self.max_keys} keys) exceeded; eviction triggered."
                )

        self._seen[id_hash] = attr_hash
        accounting.verified_unique_records += 1
        accounting.retained_records += 1
        return record


def deduplicate_records(
    records: Iterator[EventRecord],
    accounting: Optional[DedupAccounting] = None,
    max_keys: int = DEFAULT_MAX_DEDUP_KEYS
) -> Tuple[List[EventRecord], DedupAccounting]:
    """Deduplicate records stream and return retained records and accounting."""
    if accounting is None:
        accounting = DedupAccounting()

    dedup = Deduplicator(max_keys=max_keys)
    retained: List[EventRecord] = []

    for r in records:
        out = dedup.process_record(r, accounting)
        if out is not None:
            retained.append(out)

    return retained, accounting
