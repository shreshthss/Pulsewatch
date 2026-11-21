# PulseWatch Input Schema & Ingestion Contract

## 1. File Format
PulseWatch ingests line-delimited JSON (`JSONL`). Each line contains either an API event record or a control record. Maximum line size is bounded by default to 8,192 bytes.

## 2. API Event Records

| Field | Type | Required | Description |
|---|---|---|---|
| `timestamp` | `str` / `int` / `float` | Yes | Event timestamp (ISO-8601 UTC string or epoch seconds/ms/ns). Stored internally as integer UTC nanoseconds. |
| `method` | `str` | Yes | HTTP method (`GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`). |
| `route` | `str` | Yes | Endpoint URI path pattern. |
| `client` | `str` | Yes | Calling client identifier or IP address. |
| `status` | `int` | Yes | HTTP response status code ($100 \le \text{status} \le 599$). |
| `source` | `str` | Optional | Originating gateway or reporting node identifier. Required for identity. |
| `seq` | `int` | Optional | Monotonic sequence number per source (Priority 1 identity). |
| `trace_id` | `str` | Optional | Distributed trace ID (Priority 2 identity). |
| `span_id` | `str` | Optional | Distributed span ID (Priority 2 identity). |
| `request_id` | `str` | Optional | Unique request ID (Priority 3 identity). |

### Example Event Record
```json
{"timestamp": "2026-11-21T05:00:00Z", "method": "GET", "route": "/api/v1/items", "client": "client-001", "status": 200, "source": "gw1", "seq": 101}
```

## 3. Control Records (`_pw: coverage`)
Explicit monitoring coverage intervals may be declared using control records:
```json
{"_pw": "coverage", "start": "2026-11-21T05:00:00Z", "end": "2026-11-21T05:30:00Z"}
```
Seconds within declared coverage intervals without event arrivals are recorded as **known zeros**. Outside declared coverage, intervals $> 900$s without events are treated as **unknown gaps**.
