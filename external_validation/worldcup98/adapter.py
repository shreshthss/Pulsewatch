"""Official 1998 World Cup Web Site Access Logs binary adapter for PulseWatch.

Decodes 20-byte big-endian binary records from the official 1998 World Cup
trace files (wc_dayX_Y.gz) and maps them into PulseWatch JSONL format
without fabricating request-level identities or coverage declarations.

Source: M. Arlitt and T. Jin, "1998 World Cup Web Site Access Logs", August 1998.
ITA: https://ita.ee.lbl.gov/html/contrib/WorldCup.html
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Dict, Iterator, List, Optional, Set, TextIO, Tuple, Union

# Binary record struct layout: >IIII4B (20 bytes big-endian)
RECORD_FORMAT = ">IIII4B"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)
assert RECORD_SIZE == 20, f"Record size must be 20 bytes, got {RECORD_SIZE}"

NO_SIZE = 0xFFFFFFFF

# Mappings from official definitions.h and recreate.c
PROTOCOL_NAMES = {
    0: "HTTP/0.9",
    1: "HTTP/1.0",
    2: "HTTP/1.1",
    3: "HTTP/X.X",
}

METHOD_NAMES = {
    0: "GET",
    1: "HEAD",
    2: "POST",
    3: "PUT",
    4: "DELETE",
    5: "TRACE",
    6: "OPTIONS",
    7: "CONNECT",
    8: "OTHER",
}

# Supported standard HTTP methods in PulseWatch ingest
PULSEWATCH_STANDARD_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})

# Status code mappings from definitions.h (SC_100 to SC_505, OTHER_CODES=37)
STATUS_CODE_MAP: Dict[int, Optional[int]] = {
    0: 100,
    1: 101,
    2: 200,
    3: 201,
    4: 202,
    5: 203,
    6: 204,
    7: 205,
    8: 206,
    9: 300,
    10: 301,
    11: 302,
    12: 303,
    13: 304,
    14: 305,
    15: 400,
    16: 401,
    17: 402,
    18: 403,
    19: 404,
    20: 405,
    21: 406,
    22: 407,
    23: 408,
    24: 409,
    25: 410,
    26: 411,
    27: 412,
    28: 413,
    29: 414,
    30: 415,
    31: 500,
    32: 501,
    33: 502,
    34: 503,
    35: 504,
    36: 505,
    37: None,  # OTHER_CODES ("-")
}

FILE_TYPE_NAMES = {
    0: "HTML",
    1: "IMAGE",
    2: "AUDIO",
    3: "VIDEO",
    4: "JAVA",
    5: "FORMATTED",
    6: "DYNAMIC",
    7: "TEXT",
    8: "COMPRESSED",
    9: "PROGRAMS",
    10: "DIRECTORY",
    11: "ICL",
    12: "OTHER",
}

REGION_NAMES = {
    0: "SANTA_CLARA",
    1: "PLANO",
    2: "HERNDON",
    3: "PARIS",
}


@dataclass(frozen=True)
class RawWorldCupRecord:
    """Decoded 20-byte record from World Cup access logs."""
    timestamp: int
    client_id: int
    object_id: int
    size: int
    method_code: int
    status_raw: int
    type_code: int
    server_raw: int

    @property
    def http_version_code(self) -> int:
        return (self.status_raw & 0xC0) >> 6

    @property
    def status_code_index(self) -> int:
        return self.status_raw & 0x3F

    @property
    def http_version(self) -> str:
        return PROTOCOL_NAMES.get(self.http_version_code, "UNKNOWN")

    @property
    def method_name(self) -> str:
        return METHOD_NAMES.get(self.method_code, "UNKNOWN")

    @property
    def status_code(self) -> Optional[int]:
        return STATUS_CODE_MAP.get(self.status_code_index)

    @property
    def file_type(self) -> str:
        return FILE_TYPE_NAMES.get(self.type_code, "UNKNOWN")

    @property
    def region_code(self) -> int:
        return (self.server_raw & 0xE0) >> 5

    @property
    def server_id(self) -> int:
        return self.server_raw & 0x1F

    @property
    def region_name(self) -> str:
        return REGION_NAMES.get(self.region_code, "UNKNOWN")

    @property
    def has_valid_size(self) -> bool:
        return self.size != NO_SIZE

    @property
    def is_valid_pulsewatch_event(self) -> bool:
        """True if method and status conform to HTTP expectations."""
        return (
            self.method_name in PULSEWATCH_STANDARD_METHODS
            and self.status_code is not None
            and 100 <= self.status_code <= 599
        )


def decode_record(data: bytes, offset: int = 0) -> RawWorldCupRecord:
    """Decode a single 20-byte record at the given byte offset."""
    if len(data) - offset < RECORD_SIZE:
        raise ValueError(f"Need at least {RECORD_SIZE} bytes to decode record, have {len(data) - offset}")
    fields = struct.unpack_from(RECORD_FORMAT, data, offset)
    return RawWorldCupRecord(*fields)


def load_object_mappings(mapping_file_path: str) -> Dict[int, str]:
    """Load official object_mappings.sort file mapping objectID -> URL path."""
    mappings: Dict[int, str] = {}
    with open(mapping_file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    obj_id = int(parts[0])
                    mappings[obj_id] = parts[1]
                except ValueError:
                    continue
    return mappings


class WorldCupStreamReader:
    """Incremental streaming reader for .gz or raw binary World Cup access logs."""

    def __init__(self, file_path_or_stream: Union[str, BinaryIO], is_gzip: Optional[bool] = None):
        self.path: Optional[str] = None
        self._close_stream = False
        if isinstance(file_path_or_stream, str):
            self.path = file_path_or_stream
            if is_gzip is None:
                is_gzip = file_path_or_stream.endswith(".gz")
            if is_gzip:
                self.stream: BinaryIO = gzip.open(file_path_or_stream, "rb")
            else:
                self.stream = open(file_path_or_stream, "rb")
            self._close_stream = True
        else:
            self.stream = file_path_or_stream
            if is_gzip:
                self.stream = gzip.GzipFile(fileobj=self.stream, mode="rb")

        self.records_read = 0
        self.truncated_bytes = 0

    def close(self) -> None:
        if self._close_stream and self.stream:
            self.stream.close()

    def __enter__(self) -> "WorldCupStreamReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def iter_records(self, chunk_records: int = 32768) -> Iterator[RawWorldCupRecord]:
        """Yield RawWorldCupRecord objects incrementally in chunks."""
        chunk_bytes = chunk_records * RECORD_SIZE
        buf = bytearray()
        while True:
            read_bytes = self.stream.read(chunk_bytes)
            if not read_bytes:
                break
            buf.extend(read_bytes)
            num_available = len(buf) // RECORD_SIZE
            if num_available > 0:
                bytes_to_process = num_available * RECORD_SIZE
                view = memoryview(buf)
                for offset in range(0, bytes_to_process, RECORD_SIZE):
                    fields = struct.unpack_from(RECORD_FORMAT, view, offset)
                    self.records_read += 1
                    yield RawWorldCupRecord(*fields)
                view.release()
                del buf[:bytes_to_process]

        if len(buf) > 0:
            self.truncated_bytes = len(buf)


@dataclass
class DatasetProfile:
    """Summary profile computed directly from binary World Cup trace."""
    total_records: int = 0
    valid_pulsewatch_records: int = 0
    invalid_or_unmapped_records: int = 0
    truncated_tail_bytes: int = 0
    min_timestamp: int = 0
    max_timestamp: int = 0
    elapsed_seconds: int = 0
    min_client_id: int = 0
    max_client_id: int = 0
    unique_clients_count: int = 0
    min_object_id: int = 0
    max_object_id: int = 0
    unique_objects_count: int = 0
    total_response_bytes: int = 0
    no_size_records_count: int = 0
    min_transfer_size: int = 0
    max_transfer_size: int = 0
    mean_transfer_size: float = 0.0
    out_of_order_timestamps: int = 0
    unique_servers_count: int = 0
    methods_distribution: Dict[str, int] = field(default_factory=dict)
    status_codes_distribution: Dict[str, int] = field(default_factory=dict)
    http_versions_distribution: Dict[str, int] = field(default_factory=dict)
    file_types_distribution: Dict[str, int] = field(default_factory=dict)
    regions_distribution: Dict[str, int] = field(default_factory=dict)
    servers_distribution: Dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_records": self.total_records,
            "valid_pulsewatch_records": self.valid_pulsewatch_records,
            "invalid_or_unmapped_records": self.invalid_or_unmapped_records,
            "truncated_tail_bytes": self.truncated_tail_bytes,
            "min_timestamp": self.min_timestamp,
            "max_timestamp": self.max_timestamp,
            "elapsed_seconds": self.elapsed_seconds,
            "min_client_id": self.min_client_id,
            "max_client_id": self.max_client_id,
            "unique_clients_count": self.unique_clients_count,
            "min_object_id": self.min_object_id,
            "max_object_id": self.max_object_id,
            "unique_objects_count": self.unique_objects_count,
            "total_response_bytes": self.total_response_bytes,
            "no_size_records_count": self.no_size_records_count,
            "min_transfer_size": self.min_transfer_size,
            "max_transfer_size": self.max_transfer_size,
            "mean_transfer_size": round(self.mean_transfer_size, 6),
            "out_of_order_timestamps": self.out_of_order_timestamps,
            "unique_servers_count": self.unique_servers_count,
            "methods_distribution": self.methods_distribution,
            "status_codes_distribution": self.status_codes_distribution,
            "http_versions_distribution": self.http_versions_distribution,
            "file_types_distribution": self.file_types_distribution,
            "regions_distribution": self.regions_distribution,
            "servers_distribution": {str(k): v for k, v in sorted(self.servers_distribution.items())},
        }


def compute_profile(
    reader: WorldCupStreamReader,
    max_records: Optional[int] = None,
    track_unique_cardinalities: bool = True,
) -> DatasetProfile:
    """Compute dataset profile matching checklog.c behavior and PulseWatch attributes."""
    profile = DatasetProfile()
    total_records = 0
    total_bytes = 0
    no_size_count = 0
    min_size = float("inf")
    max_size = float("-inf")
    min_time = float("inf")
    max_time = float("-inf")
    min_client = float("inf")
    max_client = float("-inf")
    min_object = float("inf")
    max_object = float("-inf")
    out_of_order = 0
    last_time = -1

    unique_clients: Set[int] = set()
    unique_objects: Set[int] = set()
    servers: Dict[int, int] = {}
    methods: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    versions: Dict[str, int] = {}
    file_types: Dict[str, int] = {}
    regions: Dict[str, int] = {}

    for rec in reader.iter_records():
        total_records += 1
        ts = rec.timestamp
        if ts < last_time:
            out_of_order += 1
        last_time = ts

        if ts < min_time:
            min_time = ts
        if ts > max_time:
            max_time = ts

        if rec.client_id < min_client:
            min_client = rec.client_id
        if rec.client_id > max_client:
            max_client = rec.client_id

        if rec.object_id < min_object:
            min_object = rec.object_id
        if rec.object_id > max_object:
            max_object = rec.object_id

        if track_unique_cardinalities:
            unique_clients.add(rec.client_id)
            unique_objects.add(rec.object_id)

        if rec.size != NO_SIZE:
            total_bytes += rec.size
            if rec.size < min_size:
                min_size = rec.size
            if rec.size > max_size:
                max_size = rec.size
        else:
            no_size_count += 1

        servers[rec.server_raw] = servers.get(rec.server_raw, 0) + 1
        m_name = rec.method_name
        methods[m_name] = methods.get(m_name, 0) + 1

        st_str = str(rec.status_code) if rec.status_code is not None else "OTHER/NONE"
        statuses[st_str] = statuses.get(st_str, 0) + 1

        v_name = rec.http_version
        versions[v_name] = versions.get(v_name, 0) + 1

        ft_name = rec.file_type
        file_types[ft_name] = file_types.get(ft_name, 0) + 1

        reg_name = rec.region_name
        regions[reg_name] = regions.get(reg_name, 0) + 1

        if rec.is_valid_pulsewatch_event:
            profile.valid_pulsewatch_records += 1
        else:
            profile.invalid_or_unmapped_records += 1

        if max_records and total_records >= max_records:
            break

    profile.total_records = total_records
    profile.truncated_tail_bytes = reader.truncated_bytes
    if total_records > 0:
        profile.min_timestamp = int(min_time)
        profile.max_timestamp = int(max_time)
        profile.elapsed_seconds = int(max_time - min_time)
        profile.min_client_id = int(min_client)
        profile.max_client_id = int(max_client)
        profile.min_object_id = int(min_object)
        profile.max_object_id = int(max_object)
        profile.unique_clients_count = len(unique_clients)
        profile.unique_objects_count = len(unique_objects)
        profile.total_response_bytes = total_bytes
        profile.no_size_records_count = no_size_count
        profile.min_transfer_size = int(min_size) if min_size != float("inf") else 0
        profile.max_transfer_size = int(max_size) if max_size != float("-inf") else 0
        profile.mean_transfer_size = total_bytes / total_records if total_records else 0.0
        profile.out_of_order_timestamps = out_of_order
        profile.unique_servers_count = len(servers)
        profile.methods_distribution = methods
        profile.status_codes_distribution = statuses
        profile.http_versions_distribution = versions
        profile.file_types_distribution = file_types
        profile.regions_distribution = regions
        profile.servers_distribution = servers

    return profile


def sha256_file(path: str) -> str:
    """Compute hex SHA-256 digest of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extract_window_to_jsonl(
    source_path: str,
    output_path: str,
    start_ts: int,
    end_ts: int,
    object_mappings: Optional[Dict[int, str]] = None,
    max_records: Optional[int] = 350000,
) -> Tuple[DatasetProfile, str]:
    """Extract a contiguous time window [start_ts, end_ts] to a PulseWatch-compatible JSONL file.

    Returns (window_profile, output_sha256).
    """
    profile = DatasetProfile()
    total_records = 0
    total_bytes = 0
    no_size_count = 0
    min_size = float("inf")
    max_size = float("-inf")
    min_time = float("inf")
    max_time = float("-inf")
    min_client = float("inf")
    max_client = float("-inf")
    min_object = float("inf")
    max_object = float("-inf")
    out_of_order = 0
    last_time = -1

    unique_clients: Set[int] = set()
    unique_objects: Set[int] = set()
    servers: Dict[int, int] = {}
    methods: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    versions: Dict[str, int] = {}
    file_types: Dict[str, int] = {}
    regions: Dict[str, int] = {}

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with WorldCupStreamReader(source_path) as reader, open(output_path, "w", encoding="utf-8") as out:
        for rec in reader.iter_records():
            ts = rec.timestamp
            if ts < start_ts:
                continue
            if ts > end_ts:
                # Traces are ordered chronologically; break when past end_ts
                break

            total_records += 1
            if ts < last_time:
                out_of_order += 1
            last_time = ts

            if ts < min_time:
                min_time = ts
            if ts > max_time:
                max_time = ts

            if rec.client_id < min_client:
                min_client = rec.client_id
            if rec.client_id > max_client:
                max_client = rec.client_id

            if rec.object_id < min_object:
                min_object = rec.object_id
            if rec.object_id > max_object:
                max_object = rec.object_id

            unique_clients.add(rec.client_id)
            unique_objects.add(rec.object_id)

            if rec.size != NO_SIZE:
                total_bytes += rec.size
                if rec.size < min_size:
                    min_size = rec.size
                if rec.size > max_size:
                    max_size = rec.size
            else:
                no_size_count += 1

            servers[rec.server_raw] = servers.get(rec.server_raw, 0) + 1
            m_name = rec.method_name
            methods[m_name] = methods.get(m_name, 0) + 1

            st_str = str(rec.status_code) if rec.status_code is not None else "OTHER/NONE"
            statuses[st_str] = statuses.get(st_str, 0) + 1

            v_name = rec.http_version
            versions[v_name] = versions.get(v_name, 0) + 1

            ft_name = rec.file_type
            file_types[ft_name] = file_types.get(ft_name, 0) + 1

            reg_name = rec.region_name
            regions[reg_name] = regions.get(reg_name, 0) + 1

            # Map route: use URL if in object_mappings, otherwise /object/<objectID>
            if object_mappings and rec.object_id in object_mappings:
                route_str = object_mappings[rec.object_id]
            else:
                route_str = f"/object/{rec.object_id}"

            # Only emit valid PulseWatch events (valid method and integer status)
            # If invalid method or status, record in accounting and skip or handle
            if rec.is_valid_pulsewatch_event:
                profile.valid_pulsewatch_records += 1
                event = {
                    "timestamp": rec.timestamp,
                    "method": rec.method_name,
                    "route": route_str,
                    "client": f"client:{rec.client_id}",
                    "status": rec.status_code,
                }
                out.write(json.dumps(event) + "\n")
            else:
                profile.invalid_or_unmapped_records += 1

            if max_records and total_records >= max_records:
                break

    profile.total_records = total_records
    profile.truncated_tail_bytes = reader.truncated_bytes
    if total_records > 0:
        profile.min_timestamp = int(min_time)
        profile.max_timestamp = int(max_time)
        profile.elapsed_seconds = int(max_time - min_time)
        profile.min_client_id = int(min_client)
        profile.max_client_id = int(max_client)
        profile.min_object_id = int(min_object)
        profile.max_object_id = int(max_object)
        profile.unique_clients_count = len(unique_clients)
        profile.unique_objects_count = len(unique_objects)
        profile.total_response_bytes = total_bytes
        profile.no_size_records_count = no_size_count
        profile.min_transfer_size = int(min_size) if min_size != float("inf") else 0
        profile.max_transfer_size = int(max_size) if max_size != float("-inf") else 0
        profile.mean_transfer_size = total_bytes / total_records if total_records else 0.0
        profile.out_of_order_timestamps = out_of_order
        profile.unique_servers_count = len(servers)
        profile.methods_distribution = methods
        profile.status_codes_distribution = statuses
        profile.http_versions_distribution = versions
        profile.file_types_distribution = file_types
        profile.regions_distribution = regions
        profile.servers_distribution = servers

    out_hash = sha256_file(output_path)
    return profile, out_hash
