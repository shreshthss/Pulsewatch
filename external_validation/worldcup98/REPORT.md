# PulseWatch External Workload Validation Report: 1998 World Cup Access Logs

**Branch:** `validation/worldcup98`  
**Base Release Commit:** `71a5e357d79f0e57a592b3216c232ab4805c57da`  
**Date:** November 2026  
**Status:** Verification complete · Separate validation branch · Core engine unmodified  

---

## 1. Objective and Scope

This investigation evaluates PulseWatch against genuine, non-synthetic historical HTTP traffic using the official **1998 World Cup Web Site Access Logs** from the Internet Traffic Archive (ITA).

The objective is strictly bounded:
- **What is tested:** Ingestion accounting, schema parsing, rejection of fabricated identity assumptions, sparse temporal aggregation across standard 60-second windows, baseline rate fitting and reliability scoring under real multi-server web workloads, and memory/CPU runtime bounds.
- **Absence of Ground-Truth Labels:** This experiment is **not** an anomaly-detection benchmark. The 1998 World Cup dataset does not include ground-truth incident annotations or labeled anomalies.
- **Incident Candidate Interpretation:** Any incident candidate flagged by PulseWatch represents a statistical deviation from the locally fitted baseline under the specified PW-Scan thresholds, not a verified operational defect or true positive.
- **Metrics Inapplicability:** Precision, recall, F1, false-positive rate, and detection accuracy cannot mathematically or scientifically be computed from this dataset because no ground-truth negative or positive event labels exist.
- **Deduplication Boundary:** The dataset does not contain unique request-level idempotency identifiers (such as request UUIDs or distributed trace/span IDs). PulseWatch must ingest these records under `partial_identity_coverage` and retain them without attempting speculative deduplication. Deduplication verification remains grounded in PulseWatch's controlled synthetic Traffic Lab scenarios.
- **Generalization Boundary:** Historical website traffic patterns from 1998 do not establish present-day microservice, modern API-gateway, or distributed telemetry generalization.

---

## 2. Dataset Provenance and Characteristics

The dataset was collected by Martin Arlitt and Tai Jin of Hewlett-Packard Laboratories during the 1998 FIFA World Cup in France (www.france98.com).

> **Official Attribution:**  
> M. Arlitt and T. Jin, *"1998 World Cup Web Site Access Logs"*, August 1998. Available at the Internet Traffic Archive: <https://ita.ee.lbl.gov/html/contrib/WorldCup.html>.

### Historical Dataset Facts
- **Total Volume:** 1,352,804,107 requests across 88 days (30 April 1998 21:00:00 GMT to 26 July 1998 23:59:59 GMT).
- **Physical Architecture:** 33 HTTP Web servers distributed across 4 geographic locations:
  - Santa Clara, California (Servers 0–5)
  - Plano, Texas (Servers 32–42)
  - Herndon, Virginia (Servers 64–73)
  - Paris, France (Servers 96–101)
- **Time Representation:** Timestamps have 1-second resolution and are stored as integer seconds since the Unix epoch in GMT (UTC). Local venue time in France during the tournament was GMT+0200.
- **File Distribution:** 249 compressed binary trace files named `wc_dayX_Y.gz`.
- **Privacy & Anonymization:** Client IP addresses were mapped to integer client IDs. These mappings are 1-to-1, consistent across the entire 88-day archive, and individual client IP addresses were not released. Client IDs represent client proxies or user browsers, not individual validated authentication sessions.
- **Redistribution Terms:** The raw trace logs and HP analysis software are governed by non-commercial research licensing terms from Hewlett-Packard and the Internet Traffic Archive. Raw `.gz` files and binary tools are not bundled in this repository.

---

## 3. Why This Dataset Was Selected Over NASA-HTTP

Earlier preliminary validation discussions suggested the August 1995 NASA Kennedy Space Center HTTP logs. The 1998 World Cup access logs were selected instead for key engineering reasons:

1. **Volume and Concurrency:** The NASA dataset covers a small single-server environment with approximately 3.4 million total requests and peak rates rarely exceeding 10–20 requests/second. The World Cup dataset captures a global, 33-server load-balanced deployment handling hundreds of requests per second, stressing PulseWatch's multi-scale Poisson/Negative-Binomial detector and sparse temporal engine.
2. **Standardized Binary Format:** NASA logs are ASCII Common Log Format (CLF) files with arbitrary string variances, missing quotes, and timezone ambiguities. The World Cup dataset provides an authoritative, fixed 20-byte binary specification with published C verification tools (`checklog`, `recreate`, `read`) and exact checksum manifests.
3. **Temporal Breadth:** The 88-day World Cup archive spans distinct operational phases: pre-tournament baseline preparation, opening day surges, and tournament final match traffic.

---

## 4. Binary Format and Adapter Methodology

### Binary Record Layout
Each request is stored as a 20-byte big-endian struct (`>IIII4B`):

```c
struct request {
    uint32_t timestamp;  /* UTC Unix seconds */
    uint32_t clientID;   /* Anonymized stable client/proxy integer */
    uint32_t objectID;   /* Unique resource integer identifier */
    uint32_t size;       /* Response bytes (0xffffffff = NO_SIZE) */
    uint8_t method;      /* Encoded method index */
    uint8_t status;      /* High 2 bits: HTTP version; Low 6 bits: Status code */
    uint8_t type;        /* Resource file type index */
    uint8_t server;      /* High 3 bits: Region; Low 5 bits: Server index */
};
```

### Protocol & Field Mappings
The adapter implements field decoders strictly matching Hewlett-Packard's official `definitions.h`:
- **Timestamp:** UTC Unix seconds preserved directly without timezone distortion.
- **HTTP Version:** Extracted via `(status & 0xc0) >> 6`:
  - `0`: HTTP/0.9, `1`: HTTP/1.0, `2`: HTTP/1.1, `3`: HTTP/X.X.
- **Status Code:** Extracted via `status & 0x3f`:
  - Index `2` $\rightarrow$ 200, `10` $\rightarrow$ 301, `11` $\rightarrow$ 302, `13` $\rightarrow$ 304, `15` $\rightarrow$ 400, `19` $\rightarrow$ 404, `31` $\rightarrow$ 500, `37` $\rightarrow$ unmapped ("-").
- **Method:**
  - `0`: GET, `1`: HEAD, `2`: POST, `3`: PUT, `4`: DELETE, `5`: TRACE, `6`: OPTIONS, `7`: CONNECT, `8`: OTHER.
- **Client Mapping:** `client:<clientID>`.
- **Route Mapping:** Resolved against the official URL map (`state/object_mappings.sort`) where available; fallback to `/object/<objectID>` as an opaque resource identifier (not a modern REST endpoint).
- **Identity Invariant:** No synthetic `source`, `seq`, `trace_id`, `span_id`, or `request_id` fields are injected. Converted JSONL events declare only genuine request attributes (`timestamp`, `method`, `route`, `client`, `status`).

---

## 5. Sample Selection and Reproducibility

### Selection Methodology and Streaming Invariant
To maintain bounded execution without thinned or distorted sampling, contiguous 15-minute windows were extracted across three distinct phases of the tournament.

The extraction reader operates as a streaming pipeline:
1. Gzip decompression and 20-byte record decoding occur incrementally in 32,768-record chunks (`chunk_bytes = 655,360` bytes), keeping adapter memory usage strictly bounded regardless of shard size.
2. Because records are chronologically ordered in each shard, extraction streams sequentially from the start of the shard, begins collecting when `rec.timestamp >= start_ts`, and halts the stream the moment `rec.timestamp > end_ts`.
3. Unselected records prior to `start_ts` are streamed through without buffering; records after `end_ts` are never decompressed or read.

### Shard Scanning and Record Selection Statistics

| Sample ID | Day & UTC Date | Phase & Operational Context | Source Shard File | Total Records in Shard | Records Scanned to Window End | Records Selected in Window | Valid Records Exported | Rejected Records | Selected Retention Denominator |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| `day20` | Day 20 (1998-05-15) | Early collection (pre-tournament baseline) | `wc_day20_1.gz` | 2,084,016 | 17,210 | 17,209 | 17,209 | 0 | 17,209 / 17,209 (100.0%) |
| `day46` | Day 46 (1998-06-10) | Mid-collection (opening day: Brazil vs Scotland) | `wc_day46_1.gz` | 6,999,999 | 210,289 | 210,288 | 210,287 | 1 | 210,287 / 210,288 (99.9995%) |
| `day78` | Day 78 (1998-07-12) | Late collection (final match: France vs Brazil) | `wc_day78_1.gz` | 6,999,999 | 130,734 | 130,733 | 130,733 | 0 | 130,733 / 130,733 (100.0%) |

### Explicit Documentation of Day 46 Rejected Record
During extraction of the Day 46 window, exactly one record was rejected:
- **Timestamp:** `897429800` (1998-06-09T22:03:20Z)
- **Object ID:** `10644` (`/images/nav_bg_bottom.gif`)
- **Encoded Method:** `8` (`OTHER_METHODS` in `definitions.h`)
- **Raw Status Byte:** `229` (`0xE5` $\rightarrow$ protocol `HTTP/X.X`, status code index `37` = `OTHER_CODES` / `-`)
- **Reason for Rejection:** The record contains an undefined HTTP method and an unmapped status code index (`-`). Emitting a synthetic substitute (such as fabricating a `GET` or `200`) would violate schema fidelity. The adapter rejected this record from the exported JSONL and incremented `invalid_or_unmapped_records`.
- **Retention Percentage Calculation:** The retention of selected records on Day 46 is strictly:
  $$\frac{210,287 \text{ valid records}}{210,288 \text{ selected records}} = 99.999524\%$$
  The ingested JSONL file therefore contained exactly 210,287 lines, of which 210,287 were retained by PulseWatch.

### Generated JSONL Manifest

| Sample ID | Generated JSONL Filename | Output Size | Output File SHA-256 | Start Timestamp (UTC) | End Timestamp (UTC) | Timestamp Interval ($\Delta t$) |
|:---|:---|:---|:---|:---|:---|:---|
| `day20` | `day20_sample.jsonl` | 1.8 MB | `ff8abb71a593b9ff4af1feb06a6b6549dd222f10565b23cf3987968f903900da` | 895183201 | 895184101 | 900 seconds |
| `day46` | `day46_sample.jsonl` | 21.6 MB | `97c76316758a88c52165d60b64d289f87eef8c73afabfdaa17c35776da7e0ddc` | 897429601 | 897430501 | 900 seconds |
| `day78` | `day78_sample.jsonl` | 13.9 MB | `e81b04cd7e9237609853a1fb2060050e735f9cd2e03ba2b00c84f2f2ab768f4f` | 900194401 | 900195301 | 900 seconds |

---

## 6. Data Integrity and Adapter Tests

### Verification Against Official Checklog Output
The Python streaming binary adapter was independently verified using `external_validation/worldcup98/validate_source.py` against the official 1,000-request test fixture (`test_log.gz`) and reference output (`test_log.check`):

| Metric | Official Reference (`test_log.check`) | Adapter Computed Output | Status |
|:---|:---|:---|:---|
| Total Requests | 1,000 | 1,000 | **Exact Match** |
| Total Bytes Transferred | 5,410,216 | 5,410,216 | **Exact Match** |
| Mean Transfer Size | 5,410.216000 | 5,410.216000 | **Exact Match** |
| Client ID Range | 0 to 34,607 | 0 to 34,607 | **Exact Match** |
| Object ID Range | 0 to 4,505 | 0 to 4,505 | **Exact Match** |
| Transfer Size Range | 0 to 60,349 bytes | 0 to 60,349 bytes | **Exact Match** |
| Timestamp Range | 893971817 to 893973655 | 893971817 to 893973655 | **Exact Match** |
| Out-of-Order Timestamps | 0 | 0 | **Exact Match** |
| Unique Servers | 3 (Server 32: 664, 96: 247, 97: 89) | 3 (Server 32: 664, 96: 247, 97: 89) | **Exact Match** |

### Automated Unit Test Suite
7 dedicated unit tests were implemented in `tests/external_validation/test_worldcup98_adapter.py`:
- `test_record_size_invariant`: Confirms strict 20-byte struct length.
- `test_big_endian_decoding_and_utc_preservation`: Validates field offsets and GMT timestamp integrity.
- `test_official_status_and_method_mappings`: Validates method codes and status bitmask decoders.
- `test_no_size_handling`: Validates `0xffffffff` exclusion from byte sums.
- `test_truncated_and_malformed_records`: Verifies graceful reporting of trailing byte fragments.
- `test_absence_of_fabricated_identity_and_valid_pulsewatch_schema`: Ensures no identity fields are fabricated.
- `test_window_extraction_filtering_and_determinism`: Confirms deterministic boundary filtering and SHA-256 reproducibility.

All 36 tests (29 core engine + 7 external validation tests) pass cleanly without regression.

---

## 7. PulseWatch Pipeline Results

Each extracted 15-minute sample was analyzed using the unmodified PulseWatch CLI:
```bash
pulsewatch summarize INPUT.jsonl
pulsewatch analyze INPUT.jsonl --json OUTPUT.json
```

### Execution Summary Across the Three Workloads

| Pipeline Metric | Day 20 (Early Baseline) | Day 46 (Opening Day) | Day 78 (Final Match Day) |
|:---|:---|:---|:---|
| **Input Lines Read** | 17,209 | 210,287 | 130,733 |
| **Valid Events Ingested** | 17,209 | 210,287 | 130,733 |
| **Deduplication State** | `partial_identity_coverage` | `partial_identity_coverage` | `partial_identity_coverage` |
| **Duplicates Removed** | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| **Retained Records** | 17,209 (100.0% of lines) | 210,287 (100.0% of lines) | 130,733 (100.0% of lines) |
| **Identity Ineligible Records** | 17,209 | 210,287 | 130,733 |
| **Elapsed Timestamp Difference ($t_{max} - t_{min}$)** | 900 seconds | 900 seconds | 900 seconds |
| **Active 1-Second Buckets ($N_{active}$)** | 901 integer seconds | 901 integer seconds | 901 integer seconds |
| **Total Span ($t_{max} - t_{min} + 1$)** | 901 seconds | 901 seconds | 901 seconds |
| **Coverage Ratio** | 100.0% | 100.0% | 100.0% |
| **Unknown Gaps ($>900\text{s}$)** | 0 | 0 | 0 |
| **Fitted Baseline Rate** | 19.25 req/s | 237.58 req/s | 144.02 req/s |
| **Baseline Reliability** | **HIGH** | **HIGH** | **HIGH** |
| **Masked Contamination** | 0.0% | 0.0% | 0.0% |
| **Incident Candidates Detected** | 0 | 0 | 0 |
| **Analysis Hash** | `02f1b10dcdbc8b...` | `1c8925691acd1d...` | `a0a2f76b8f0f44...` |
| **Observed Execution Time** | 0.28 seconds | 3.07 seconds | 1.95 seconds |
| **Observed Peak RSS** | 30.1 MB | 134.9 MB | 92.6 MB |

### Explanation of 900-Second Interval vs 901 Active Buckets
The window selection bounds are inclusive: $[t_{start}, t_{end}]$ where $t_{end} - t_{start} = 900$ seconds.
- In `pulsewatch/temporal.py`, sparse aggregation groups timestamps by integer second:
  $$\text{effective\_earliest} = \min(t), \quad \text{effective\_latest} = \max(t)$$
  $$\text{total\_span} = \text{effective\_latest} - \text{effective\_earliest} + 1 = 900 + 1 = 901 \text{ seconds}$$
- Because traffic was dense, requests arrived in every second from second $0$ through second $900$ inclusive.
- Counting both endpoint seconds yields exactly 901 distinct active 1-second buckets ($N = 901$).
- Total span and active seconds match exactly ($901 / 901 = 1.0$), correctly producing a 100.0% coverage ratio with zero gaps.

---

## 8. Traffic and Contributor Observations

1. **Observed Baseline Stationarity Across Three Distinct Regimes:**
   - On Day 20, traffic was steady at ~19.25 req/s.
   - On Day 46, opening-day traffic rose to ~237.58 req/s across 26 servers.
   - On Day 78, final match day traffic settled at ~144.02 req/s across 30 servers.
   - In all three selected windows, traffic arrival counts adhered closely to the fitted baseline distributions across 10s, 60s, and 300s scales.
   - **Precise Detector Observation:** PulseWatch produced **0 incident candidates** across all three windows. This means that no time window exceeded the multi-scale Poisson/Negative-Binomial elevation thresholds ($Z \ge 3.0$, relative ratio $\ge 1.5$, absolute excess $\ge 5$ req/s).
   - **Crucial Methodological Distinction:** Because the 1998 World Cup logs contain no ground-truth anomaly annotations, the outcome of 0 incident candidates **cannot** be cited as evidence of "zero false alarms" or "detector accuracy". It establishes only that the baseline fitting and dispersion estimators accommodated these real-world web arrival rates without generating spurious alerts on stationary segments.
2. **Resource Cardinality vs Client Proxy Cardinality:**
   - On Day 46, 210,287 requests were generated by 3,720 unique client IDs accessing 3,800 distinct URL paths.
   - High-volume routes were concentrated on image assets and tournament fixtures (navigation GIFs, country flag graphics, match tables).
   - On Day 78, 130,733 requests were generated by 2,493 client IDs accessing 5,545 distinct URL paths.
3. **Response Status Distribution:**
   - Over 90% of requests returned `200 OK`.
   - Approximately 7–9% returned `304 Not Modified` (client cache validation).
   - Fewer than 0.3% returned `4xx` errors (principally 404 Not Found for outdated image references).
   - Only 2 requests across all 358,000+ examined records returned `5xx` server errors (both on Day 46).
4. **Behavior Under Incomplete Identity Coverage:**
   - Because historical web logs lack idempotency keys, PulseWatch reported `partial_identity_coverage` and preserved 100% of the ingested traffic.
   - This confirms that PulseWatch retains unidentifiable records rather than dropping them or fabricating pseudo-identities.

---

## 9. Limitations and Threats to Validity

1. **Absence of Ground-Truth Anomaly Labels:** The ITA dataset contains no incident ground truth. A detected spike would be an anomaly candidate, not a proven incident. Conversely, the absence of detected incident candidates indicates local baseline conformity, but does not measure recall against subtle or unlabelled historical operational anomalies.
2. **Inapplicability of Evaluation Metrics:** Precision, recall, F1, false-positive rate, and detection delay cannot be computed from this experiment.
3. **Not Modern API Traffic:** The 1998 World Cup access logs represent HTTP/1.0 and early HTTP/1.1 static file requests and image downloads. Modern REST/GraphQL API traffic differs substantially in payload size, stateful authentication, query parameters, header metadata, and connection reuse (HTTP/2 and HTTP/3).
4. **Proxy Aggregation (Lack of True Client Cardinality):** Many requests in 1998 originated from corporate caching forward-proxies (e.g., AOL, corporate gateways). A single `clientID` in this dataset can represent hundreds of distinct end-users behind a shared proxy IP.
5. **Deduplication Efficacy Unverified on this Trace:** Because the dataset lacks request UUIDs or trace IDs, this experiment cannot and does not validate PulseWatch's deduplication engine. Deduplication correctness relies solely on Traffic Lab synthetic test scenarios.
6. **Coverage Inferences:** The 100% coverage ratio observed within these contiguous windows reflects contiguous seconds with at least one recorded request; it does not verify that zero requests were dropped by web server syslog buffers before disk writing in 1998.

---

## 10. Conclusions and Recommended Next Steps

### Key Conclusions
1. **Streaming Scalability and Memory Bounds:** The streaming adapter processed over 210,000 binary records into JSONL incrementally, maintaining small memory buffers. PulseWatch ingested and evaluated 210,287 records in 3.07 seconds while consuming under 135 MB of RAM on the audit test machine.
2. **Contract Preservation:** Ingestion accounting, partial identity handling, sparse windowing, and determinism invariants held on real-world multi-server web logs without modifying the core PulseWatch engine.
3. **Absence of Spurious Candidate Alerts:** The PW-Scan multi-scale detector produced 0 incident candidates on steady historical web traffic across baseline rates of 19.25 req/s, 144.02 req/s, and 237.58 req/s.

### Benchmark and Validation Machine Context
- **Operating Environment:** Linux x86_64 (`Linux-7.2.11-arch1-1-x86_64-with-glibc2.44`)
- **Python Version:** Python 3.14.8
- **Note on Performance Figures:** Runtime and peak resident set size (RSS) measurements are machine-specific observations obtained via Python standard library `resource.getrusage` (ru_maxrss in KB) and `time.perf_counter` in isolated sub-processes. They demonstrate pipeline bounded behavior rather than universal hardware constants.

### Recommended Next Steps
- Implement an external validation adapter for a modern API gateway dataset (e.g., Envoy or Istio access access logs with distributed OpenTelemetry headers) if a public, anonymized trace becomes available.
- Retain the 1998 World Cup validation pipeline on this branch (`validation/worldcup98`) as an independent regression suite for high-volume HTTP parsing and memory-bound verification.
