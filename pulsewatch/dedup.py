"""Event normalization and identity-aware bounded deduplication with 16-byte BLAKE2b hashing."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

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


class RetentionLog:
    """One bit per input record: 1 if the record was retained, 0 if it was dropped as a duplicate.

    Lets a later pass over the *same, unchanged* source reproduce the first pass's retain/drop
    decisions without re-hashing every record. Memory is N/8 bytes for N input records
    (125 KB per million), independent of record size.
    """

    __slots__ = ("_bits", "_n")

    def __init__(self) -> None:
        self._bits = bytearray()
        self._n = 0

    def append(self, retained: bool) -> None:
        n = self._n
        if not n & 7:
            self._bits.append(0)
        if retained:
            self._bits[n >> 3] |= 1 << (n & 7)
        self._n = n + 1

    def __len__(self) -> int:
        return self._n

    def is_retained(self, index: int) -> bool:
        return bool(self._bits[index >> 3] & (1 << (index & 7)))


class RetentionMismatch(Exception):
    """The replayed source does not have the same record sequence the log was built from."""


def iter_replayed(records: Iterable[EventRecord], log: RetentionLog) -> Iterator[EventRecord]:
    """Yield the records the first pass retained, using its recorded decisions.

    ``records`` must be the same ordered valid-record sequence the log was built from. If it
    has more or fewer records than the log, ``RetentionMismatch`` is raised rather than
    guessing, because the source must have changed.
    """
    n = len(log)
    i = 0
    for r in records:
        if i >= n:
            raise RetentionMismatch("source has more records than the first pass")
        if log.is_retained(i):
            yield r
        i += 1
    if i != n:
        raise RetentionMismatch("source has fewer records than the first pass")


def iter_deduplicated(
    records: Iterable[EventRecord],
    accounting: DedupAccounting,
    max_keys: int = DEFAULT_MAX_DEDUP_KEYS,
    retention: Optional[RetentionLog] = None
) -> Iterator[EventRecord]:
    """Lazily yield retained records, applying exactly the semantics of ``Deduplicator``.

    This is the single implementation of the retain/drop decision; the list-returning
    ``deduplicate_records`` is a wrapper over it, so the two cannot diverge.

    Contract for callers:

    * ``accounting`` is mutated live while the iterator is consumed. Its counters are
      **final only after the iterator is exhausted**; mid-stream values are partial.
    * The iterator is single-use. Records are yielded in input order, which is also the
      order in which the "first occurrence wins" rule is applied.
    * If the source iterable raises, the exception propagates from ``next()`` and
      ``accounting`` reflects only the records processed so far; callers must not report
      it as a complete result.
    * Records without usable identity are always yielded (never dropped on attributes
      alone), exactly as in ``Deduplicator.process_record``.
    * State held is the bounded ``Deduplicator`` table (at most ``max_keys`` live
      identity hashes plus the bounded evicted-history set); no yielded record is retained.
    * If ``retention`` is given, one retain/drop bit per input record is appended to it
      (N/8 bytes total) so a later pass over the unchanged source can replay the decisions
      with ``iter_replayed``.
    """
    dedup = Deduplicator(max_keys=max_keys)
    for r in records:
        out = dedup.process_record(r, accounting)
        if retention is not None:
            retention.append(out is not None)
        if out is not None:
            yield out


def deduplicate_records(
    records: Iterator[EventRecord],
    accounting: Optional[DedupAccounting] = None,
    max_keys: int = DEFAULT_MAX_DEDUP_KEYS
) -> Tuple[List[EventRecord], DedupAccounting]:
    """Deduplicate records stream and return retained records and accounting.

    Compatibility wrapper: materialises ``iter_deduplicated`` into a list. Prefer
    ``iter_deduplicated`` when the consumer can process records incrementally.
    """
    if accounting is None:
        accounting = DedupAccounting()

    retained: List[EventRecord] = list(iter_deduplicated(records, accounting, max_keys))
    return retained, accounting
