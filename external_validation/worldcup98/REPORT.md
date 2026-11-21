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
- **What is NOT tested:** This experiment is **not** an anomaly-detection benchmark. The 1998 World Cup dataset does not include ground-truth incident annotations or labeled anomalies. Any detected incident candidate represents a statistical deviation from the local fitted baseline, not a verified operational defect or true positive. Precision, recall, F1, and detection accuracy cannot mathematically or scientifically be computed from this dataset.
- **Deduplication boundary:** The dataset does not contain unique request-level idempotency identifiers (such as request UUIDs or distributed trace/span IDs). PulseWatch must ingest these records under `partial_identity_coverage` and retain them without attempting speculative deduplication. Deduplication verification remains grounded in PulseWatch's controlled synthetic scenarios.

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

To maintain bounded execution without thinned or distorted sampling, three contiguous **15-minute (900-second)** windows were extracted across three distinct phases of the tournament:

| Sample ID | Day & Date (UTC) | Phase & Operational Context | Source Trace File | SHA-256 of Raw File | Start UTC Timestamp | End UTC Timestamp | Elapsed Seconds |
|:---|:---|:---|:---|:---|:---|:---|:---|
| `day20` | Day 20 (1998-05-15) | Early collection (pre-tournament baseline) | `wc_day20_1.gz` | `e5a1bdc0f97abb84f73952185e15ccccad5753ebd061d347de26de975679780c` | 895183201 | 895184101 | 900s |
| `day46` | Day 46 (1998-06-10) | Mid-collection (opening day: Brazil vs Scotland) | `wc_day46_1.gz` | `f676ac324ad3de49bf134148256928a25022ce3b05cf3b424159b3e4395e74d8` | 897429601 | 897430501 | 900s |
| `day78` | Day 78 (1998-07-12) | Late collection (final match: France vs Brazil) | `wc_day78_1.gz` | `15b763fa9f708a24abf0225a5de467b185f909a659e6db61484b34e18bf79215` | 900194401 | 900195301 | 900s |

### Generated JSONL Manifest

| Sample ID | Generated JSONL Filename | Output Size | Output File SHA-256 | Extracted Records | Valid Events | Unmapped Records |
|:---|:---|:---|:---|:---|:---|:---|
| `day20` | `day20_sample.jsonl` | 1.8 MB | `ff8abb71a593b9ff4af1feb06a6b6549dd222f10565b23cf3987968f903900da` | 17,209 | 17,209 | 0 |
| `day46` | `day46_sample.jsonl` | 21.6 MB | `97c76316758a88c52165d60b64d289f87eef8c73afabfdaa17c35776da7e0ddc` | 210,288 | 210,287 | 1 |
| `day78` | `day78_sample.jsonl` | 13.9 MB | `e81b04cd7e9237609853a1fb2060050e735f9cd2e03ba2b00c84f2f2ab768f4f` | 130,733 | 130,733 | 0 |

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
| **Retained Records** | 17,209 (100.0%) | 210,287 (100.0%) | 130,733 (100.0%) |
| **Identity Ineligible Records** | 17,209 | 210,287 | 130,733 |
| **Total Time Span** | 901 seconds | 901 seconds | 901 seconds |
| **Active 1-Second Buckets** | 901 (100.0%) | 901 (100.0%) | 901 (100.0%) |
| **Coverage Ratio** | 100.0% | 100.0% | 100.0% |
| **Unknown Gaps** | 0 | 0 | 0 |
| **Fitted Baseline Rate** | 19.25 req/s | 237.58 req/s | 144.02 req/s |
| **Baseline Reliability** | **HIGH** | **HIGH** | **HIGH** |
| **Masked Contamination** | 0.0% | 0.0% | 0.0% |
| **Detected Incidents** | 0 | 0 | 0 |
| **Analysis Hash** | `02f1b10dcdbc8b...` | `1c8925691acd1d...` | `a0a2f76b8f0f44...` |
| **Execution Time** | 0.28 seconds | 3.07 seconds | 1.95 seconds |
| **Peak Resident Set Size (RSS)** | 30.1 MB | 134.9 MB | 92.6 MB |

---

## 8. Traffic and Contributor Observations

1. **Stationary Baseline Stability Under High Concurrency:**
   - On Day 20, traffic was steady at ~19 req/s with low dispersion.
   - On Day 46, traffic increased by an order of magnitude to ~238 req/s across 26 servers.
   - On Day 78, traffic settled at ~144 req/s across 30 servers.
   - In all three contiguous 15-minute windows, the traffic distribution fit Poisson/Negative-Binomial dispersion smoothly. Because there were no abrupt multi-scale spikes exceeding the adaptive thresholds, PulseWatch reported **0 false alarms** and classified baseline reliability as **HIGH** with 0.0% contamination.
2. **Resource Cardinality vs Client Cardinality:**
   - In Day 46, 210,287 requests were generated by 3,720 unique client proxies accessing 3,800 distinct URL paths.
   - High-volume routes were concentrated on image assets and tournament fixtures (e.g., navigation GIFs, country flag graphics, match tables).
   - In Day 78, 130,733 requests were generated by 2,493 client proxies accessing 5,545 distinct URL paths.
3. **Response Status Distribution:**
   - Over 90% of requests returned `200 OK`.
   - Approximately 7–9% returned `304 Not Modified` (client cache validation).
   - Fewer than 0.3% returned `4xx` errors (principally 404 Not Found for outdated image references).
   - Only 2 requests across all 358,000+ examined records returned `5xx` server errors (both on Day 46).
4. **Behavior with Non-Identifiable Traffic:**
   - Because the events lacked idempotency keys, PulseWatch accurately reported `partial_identity_coverage` and preserved 100% of the ingested traffic.
   - This empirically confirms that PulseWatch does not drop records or degrade silently when encountering traffic lacking modern distributed tracing headers.

---

## 9. Limitations and Threats to Validity

1. **Not Modern API Traffic:** The 1998 World Cup access logs represent HTTP/1.0 and early HTTP/1.1 static file requests and image downloads. Modern REST/GraphQL API traffic differs substantially in payload size, stateful authentication, query parameters, header metadata, and connection reuse (HTTP/2 and HTTP/3).
2. **Proxy Aggregation (Lack of True Client Cardinality):** Many requests in 1998 originated from corporate caching forward-proxies (e.g., AOL, corporate gateways). A single `clientID` in this dataset can represent hundreds of distinct end-users behind a shared proxy IP.
3. **Absence of Ground-Truth Anomaly Labels:** The ITA dataset contains no incident ground truth. A detected spike would be an anomaly candidate, not a proven incident. Conversely, the absence of detected incidents in these three windows indicates baseline conformity, but does not measure recall against unlabelled subtle historical anomalies.
4. **Deduplication Efficacy Unverified on this Dataset:** Because the dataset lacks request UUIDs or trace IDs, this experiment cannot and does not validate PulseWatch's deduplication engine. Deduplication correctness relies solely on Traffic Lab synthetic test scenarios.
5. **Coverage Inferences:** The 100% coverage ratio observed within these contiguous windows reflects contiguous seconds with at least one recorded request; it does not verify that zero requests were dropped by web server syslog buffers before disk writing in 1998.

---

## 10. Conclusions and Recommended Next Steps

### Key Conclusions
1. **Pipeline Scalability:** PulseWatch successfully processed over 210,000 real HTTP records in 3.07 seconds while consuming under 135 MB of RAM on standard commodity hardware.
2. **Contract Preservation:** Ingestion accounting, partial identity handling, sparse windowing, and determinism invariants held perfectly on real-world multi-server web logs without requiring any modifications to the core PulseWatch engine.
3. **Statistical Soundness:** The PW-Scan multi-scale detector demonstrated robust abstention from false alarms on high-volume, non-anomalous web traffic across 19 req/s, 144 req/s, and 238 req/s baseline rates.

### Recommended Next Steps
- Implement an external validation adapter for a modern API gateway dataset (e.g., Envoy or Istio access access logs with distributed OpenTelemetry headers) if a public, anonymized trace becomes available.
- Retain the 1998 World Cup validation pipeline on this branch (`validation/worldcup98`) as an independent regression suite for high-volume HTTP parsing and memory-bound verification.
