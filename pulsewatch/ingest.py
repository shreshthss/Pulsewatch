"""JSONL ingestion, schema validation, nanosecond time normalization, and control records."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, TextIO, Tuple, Union

DEFAULT_MAX_LINE_BYTES = 8192  # Researched 8KB line limit
MAX_WARNINGS_SAVED = 100
MAX_RECORD_COUNT_LIMIT = 5_000_000

VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class EventIdentity:
    """Identity attributes for deduplication."""
    source: Optional[str] = None
    seq: Optional[int] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    request_id: Optional[str] = None

    @property
    def has_usable_identity(self) -> bool:
        """A record has usable identity if it has (source, seq), (source, trace_id, span_id), or (source, request_id)."""
        if not self.source:
            return False
        if self.seq is not None:
            return True
        if self.trace_id and self.span_id:
            return True
        if self.request_id:
            return True
        return False

    def identity_key(self) -> Optional[Tuple[str, ...]]:
        """Return canonical identity tuple based on hierarchy, or None."""
        if not self.source:
            return None
        if self.seq is not None:
            return ("source_seq", self.source, str(self.seq))
        if self.trace_id and self.span_id:
            return ("source_trace_span", self.source, self.trace_id, self.span_id)
        if self.request_id:
            return ("source_req", self.source, self.request_id)
        return None


@dataclass(frozen=True)
class EventRecord:
    """Normalized API request event with integer nanosecond internal time."""
    timestamp_sec: int
    method: str
    route: str
    client: str
    status: int
    identity: EventIdentity
    raw_timestamp: Union[str, int, float] = ""
    timestamp_ns: int = 0

    def __post_init__(self):
        if self.timestamp_ns == 0 and self.timestamp_sec != 0:
            object.__setattr__(self, "timestamp_ns", self.timestamp_sec * 1_000_000_000)

    def normalized_attributes(self) -> Tuple[int, str, str, str, int]:
        return (self.timestamp_sec, self.method, self.route, self.client, self.status)


@dataclass
class IngestAccounting:
    total_lines_read: int = 0
    valid_records: int = 0
    control_records: int = 0
    empty_lines: int = 0
    oversized_lines: int = 0
    malformed_json_lines: int = 0
    schema_violations: int = 0
    timestamp_parse_failures: int = 0
    out_of_order_records: int = 0
    record_cap_reached: bool = False
    declared_coverage_intervals: List[Tuple[int, int]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        if len(self.warnings) < MAX_WARNINGS_SAVED:
            self.warnings.append(msg)
        elif len(self.warnings) == MAX_WARNINGS_SAVED:
            self.warnings.append("Further ingestion warnings truncated...")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_lines_read": self.total_lines_read,
            "valid_records": self.valid_records,
            "control_records": self.control_records,
            "empty_lines": self.empty_lines,
            "oversized_lines": self.oversized_lines,
            "malformed_json_lines": self.malformed_json_lines,
            "schema_violations": self.schema_violations,
            "timestamp_parse_failures": self.timestamp_parse_failures,
            "out_of_order_records": self.out_of_order_records,
            "record_cap_reached": self.record_cap_reached,
            "declared_coverage_count": len(self.declared_coverage_intervals),
            "warning_count": len(self.warnings),
            "warnings_sample": self.warnings[:10],
        }


def parse_timestamp_to_ns(val: Any) -> Optional[int]:
    """Parse various timestamp formats to integer UTC nanoseconds."""
    if isinstance(val, int):
        if val < 0:
            return None
        if val > 10**16:  # Nanoseconds
            return val
        elif val > 10**13:  # Microseconds
            return val * 1_000
        elif val > 10**10:  # Milliseconds
            return val * 1_000_000
        else:  # Seconds
            return val * 1_000_000_000
    elif isinstance(val, float):
        t = val
        if t < 0:
            return None
        if t > 1e16:
            return int(t)
        elif t > 1e13:
            return int(t * 1_000)
        elif t > 1e10:
            return int(t * 1_000_000)
        else:
            return int(t * 1_000_000_000)
    elif isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # Try numeric string
        try:
            num = float(val)
            return parse_timestamp_to_ns(num)
        except ValueError:
            pass

        # Parse ISO-8601 string
        iso_str = val
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            epoch_sec = dt.astimezone(datetime.timezone.utc).timestamp()
            return int(epoch_sec * 1_000_000_000)
        except (ValueError, OverflowError):
            return None
    return None


def parse_timestamp_to_sec(val: Any) -> Optional[int]:
    """Parse timestamp to integer UTC seconds."""
    ns = parse_timestamp_to_ns(val)
    if ns is None:
        return None
    return int(ns // 1_000_000_000)


def parse_record_dict(
    data: Any,
    line_num: int,
    accounting: IngestAccounting
) -> Optional[Union[EventRecord, Tuple[str, Any]]]:
    """Parse a decoded JSON dict into an EventRecord or control record tuple."""
    if not isinstance(data, dict):
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: JSON value is not an object")
        return None

    # Check for PulseWatch control records: {"_pw": "coverage", "start": ..., "end": ...}
    if "_pw" in data:
        pw_op = str(data["_pw"]).strip().lower()
        if pw_op == "coverage":
            start_ts = parse_timestamp_to_sec(data.get("start"))
            end_ts = parse_timestamp_to_sec(data.get("end"))
            if start_ts is not None and end_ts is not None and start_ts <= end_ts:
                accounting.declared_coverage_intervals.append((start_ts, end_ts))
                accounting.control_records += 1
                return ("control", ("coverage", start_ts, end_ts))
            else:
                accounting.schema_violations += 1
                accounting.add_warning(f"Line {line_num}: Invalid coverage control record boundaries")
                return None
        else:
            accounting.control_records += 1
            return ("control", (pw_op, data))

    # Required fields for event records
    required = ("timestamp", "method", "route", "client", "status")
    missing = [f for f in required if f not in data]
    if missing:
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: Missing required fields: {', '.join(missing)}")
        return None

    # Timestamp nanoseconds and seconds
    raw_ts = data["timestamp"]
    ts_ns = parse_timestamp_to_ns(raw_ts)
    if ts_ns is None:
        accounting.timestamp_parse_failures += 1
        accounting.add_warning(f"Line {line_num}: Invalid or unparseable timestamp: {raw_ts!r}")
        return None
    ts_sec = int(ts_ns // 1_000_000_000)

    # Method
    raw_method = data["method"]
    if not isinstance(raw_method, str):
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: method must be a string")
        return None
    method = raw_method.strip().upper()
    if not method:
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: method cannot be empty")
        return None

    # Route
    raw_route = data["route"]
    if not isinstance(raw_route, str) or not raw_route.strip():
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: route must be a non-empty string")
        return None
    route = raw_route.strip()

    # Client
    raw_client = data["client"]
    if not isinstance(raw_client, str) or not raw_client.strip():
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: client must be a non-empty string")
        return None
    client = raw_client.strip()

    # Status
    raw_status = data["status"]
    try:
        status = int(raw_status)
        if not (100 <= status <= 599):
            raise ValueError()
    except (ValueError, TypeError):
        accounting.schema_violations += 1
        accounting.add_warning(f"Line {line_num}: status must be an HTTP integer between 100 and 599, got {raw_status!r}")
        return None

    # Optional Identity fields
    source = data.get("source")
    source_str = str(source).strip() if source is not None and str(source).strip() else None

    seq = None
    if "seq" in data and data["seq"] is not None:
        try:
            seq = int(data["seq"])
            if seq < 0:
                seq = None
        except (ValueError, TypeError):
            seq = None

    trace_id = data.get("trace_id")
    trace_id_str = str(trace_id).strip() if trace_id is not None and str(trace_id).strip() else None

    span_id = data.get("span_id")
    span_id_str = str(span_id).strip() if span_id is not None and str(span_id).strip() else None

    request_id = data.get("request_id")
    request_id_str = str(request_id).strip() if request_id is not None and str(request_id).strip() else None

    identity = EventIdentity(
        source=source_str,
        seq=seq,
        trace_id=trace_id_str,
        span_id=span_id_str,
        request_id=request_id_str,
    )

    record = EventRecord(
        timestamp_sec=ts_sec,
        method=method,
        route=route,
        client=client,
        status=status,
        identity=identity,
        raw_timestamp=raw_ts,
        timestamp_ns=ts_ns,
    )
    accounting.valid_records += 1
    return record


def ingest_jsonl_stream(
    stream: TextIO,
    accounting: Optional[IngestAccounting] = None,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_records: int = MAX_RECORD_COUNT_LIMIT
) -> Iterator[EventRecord]:
    """Stream event records from a JSONL file stream."""
    if accounting is None:
        accounting = IngestAccounting()

    line_num = 0
    prev_ts_ns: Optional[int] = None

    for line in stream:
        line_num += 1
        accounting.total_lines_read += 1

        if accounting.valid_records >= max_records:
            accounting.record_cap_reached = True
            accounting.add_warning(f"Ingestion record limit ({max_records}) reached; remaining lines skipped.")
            break

        line_bytes = line.encode("utf-8", errors="replace")
        if len(line_bytes) > max_line_bytes:
            accounting.oversized_lines += 1
            accounting.add_warning(f"Line {line_num}: Oversized line (> {max_line_bytes} bytes) skipped")
            continue

        stripped = line.strip()
        if not stripped:
            accounting.empty_lines += 1
            continue

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            accounting.malformed_json_lines += 1
            accounting.add_warning(f"Line {line_num}: JSON syntax error: {e.msg}")
            continue

        parsed = parse_record_dict(data, line_num, accounting)
        if parsed is not None:
            if isinstance(parsed, EventRecord):
                if prev_ts_ns is not None and parsed.timestamp_ns < prev_ts_ns:
                    accounting.out_of_order_records += 1
                prev_ts_ns = parsed.timestamp_ns
                yield parsed
            # Control records are recorded in accounting.declared_coverage_intervals


def ingest_jsonl_file(
    file_path: str,
    accounting: Optional[IngestAccounting] = None,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_records: int = MAX_RECORD_COUNT_LIMIT
) -> Tuple[List[EventRecord], IngestAccounting]:
    """Read an entire JSONL file into memory."""
    if accounting is None:
        accounting = IngestAccounting()

    records: List[EventRecord] = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for rec in ingest_jsonl_stream(f, accounting, max_line_bytes=max_line_bytes, max_records=max_records):
            records.append(rec)

    return records, accounting
