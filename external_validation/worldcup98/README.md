# PulseWatch External Workload Validation: 1998 World Cup HTTP Access Logs

This directory contains the external workload validation suite for PulseWatch using the official **1998 World Cup Web Site Access Logs** hosted by the Internet Traffic Archive (ITA).

This validation suite tests PulseWatch's ingestion accounting, identity-eligibility handling, temporal windowing, sparse coverage aggregation, baseline refitting, and runtime efficiency on genuine historical HTTP request traffic at scale.

> **Important Boundary & Scope Note**:  
> This experiment tests PulseWatch against real, multi-server HTTP web access logs collected in 1998. It is **not** a labelled anomaly-detection benchmark and does **not** evaluate precision, recall, or F1. Because the dataset lacks unique request-level idempotency identifiers (such as request UUIDs or distributed trace/span IDs), this experiment does **not** validate duplicate-replay detection (which remains verified by the controlled Traffic Lab benchmarks). Historical website traffic patterns do not establish modern microservice or API-gateway generalization.

---

## Source Attribution & Provenance

The 1998 World Cup Web Site Access Logs were collected and published by Martin Arlitt and Tai Jin of Hewlett-Packard Laboratories:

> **Attribution**:  
> M. Arlitt and T. Jin, *"1998 World Cup Web Site Access Logs"*, August 1998. Available at the Internet Traffic Archive: <https://ita.ee.lbl.gov/html/contrib/WorldCup.html>.

### Dataset Facts
- **Total Requests Collected**: 1,352,804,107 requests over 88 days (30 April 1998 through 26 July 1998).
- **Infrastructure**: 33 HTTP servers distributed across 4 geographic regions (Santa Clara, Plano, Herndon, Paris).
- **Format**: Chronologically ordered, compact 20-byte big-endian binary records stored across 249 compressed `.gz` files (`wc_dayX_Y.gz`).
- **Timestamp Resolution**: 1-second resolution, recorded in GMT (UTC epoch seconds).
- **Redistribution & Licensing**: Binary log traces are publicly provided by ITA for non-commercial research. HP tools and documentation are distributed under non-commercial research licensing terms. Raw logs and proprietary tools are not bundled in this repository.

---

## Directory Structure

```text
external_validation/worldcup98/
├── README.md                      # Reproduction instructions and overview
├── REPORT.md                      # Definitive 10-section validation report
├── adapter.py                     # Streaming 20-byte binary decoder and PulseWatch mapper
├── validate_source.py             # Validation script against official 1,000-request fixture
├── select_windows.py              # Deterministic 15-minute window selection and profiling
└── results/
    ├── dataset_manifest.json      # Provenance, SHA-256 hashes, and traffic profiles
    └── aggregate_summary.json     # Execution runtime, memory, and PulseWatch summary metrics
```

---

## Step-by-Step Reproduction Guide

All adapter and validation scripts use only the Python standard library (no pip packages required).

### 1. Download Official Tools Archive and Raw Slices
Download the official tools archive and the candidate slices for Day 20, Day 46, and Day 78 into ignored local directories:

```bash
mkdir -p external_validation/worldcup98/raw external_validation/worldcup98/scratch

# Download official tools archive
curl -o external_validation/worldcup98/scratch/WorldCup_tools.tar.gz \
  https://ita.ee.lbl.gov/traces/WorldCup/WorldCup_tools.tar.gz

# Unpack tools (for definitions.h, README, and test_log.gz fixture)
tar -xzf external_validation/worldcup98/scratch/WorldCup_tools.tar.gz \
  -C external_validation/worldcup98/scratch/

# Download 3 candidate days (approx. 105 MB total)
curl -o external_validation/worldcup98/raw/wc_day20_1.gz \
  https://ita.ee.lbl.gov/traces/WorldCup/wc_day20_1.gz

curl -o external_validation/worldcup98/raw/wc_day46_1.gz \
  https://ita.ee.lbl.gov/traces/WorldCup/wc_day46_1.gz

curl -o external_validation/worldcup98/raw/wc_day78_1.gz \
  https://ita.ee.lbl.gov/traces/WorldCup/wc_day78_1.gz
```

### 2. Verify Adapter Against Official 1,000-Request Fixture
Run the source validator to confirm that Python's binary unpacking reproduces the official C `checklog` tool's outputs byte-for-byte:

```bash
python3 external_validation/worldcup98/validate_source.py
```
*Expected output: `SUCCESS: Adapter outputs match official checklog reference exactly!`*

### 3. Run Unit Test Suite
Execute the dedicated unit test suite covering 20-byte struct packing, big-endian conversion, UTC preservation, HTTP method/status mappings, and rejection of fabricated request IDs:

```bash
python3 -m unittest discover tests -v
```

### 4. Extract Contiguous 15-Minute Windows
Run the deterministic window extraction script. This creates 3 JSONL files in `scratch/` representing contiguous 900-second windows without thinning or sampling:

```bash
python3 external_validation/worldcup98/select_windows.py
```

### 5. Run PulseWatch Pipeline
Execute PulseWatch's offline CLI on each window:

```bash
# Day 20 (Early collection / pre-tournament)
./bin/pulsewatch summarize external_validation/worldcup98/scratch/day20_sample.jsonl
./bin/pulsewatch analyze external_validation/worldcup98/scratch/day20_sample.jsonl --json external_validation/worldcup98/results/day20_analysis.json

# Day 46 (Tournament opening day)
./bin/pulsewatch summarize external_validation/worldcup98/scratch/day46_sample.jsonl
./bin/pulsewatch analyze external_validation/worldcup98/scratch/day46_sample.jsonl --json external_validation/worldcup98/results/day46_analysis.json

# Day 78 (Final match day)
./bin/pulsewatch summarize external_validation/worldcup98/scratch/day78_sample.jsonl
./bin/pulsewatch analyze external_validation/worldcup98/scratch/day78_sample.jsonl --json external_validation/worldcup98/results/day78_analysis.json
```
