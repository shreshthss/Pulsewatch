# Security Boundaries, Resource Limits & Known Limitations

## 1. Security Boundaries & Offline Guarantee
- **Completely Offline**: The core engine, CLI, and HTML report generator make zero network calls, socket connections, or external API queries. Standard Python library only.
- **No Subprocess / Dynamic Code Execution**: Zero invocations of `subprocess`, `os.system`, `eval()`, or `exec()`.
- **Strict Output Sanitization**:
  - HTML reports enforce a Content Security Policy (`default-src 'none'; style-src 'unsafe-inline';`) prohibiting external scripts, fonts, images, or frames.
  - All user-supplied strings (routes, clients, file paths, IDs) are strictly escaped via `html.escape()` in HTML and sanitized in CLI formatters to prevent terminal control-sequence injection.

## 2. Resource Bounds
- Maximum line length: default 8,192 bytes.
- Maximum records: default 5,000,000 records.
- Deduplication cache: bounded to 200,000 active keys with LRU eviction.
- Evicted key history: bounded to 100,000 entries.
- Non-zero temporal bins: sparse dict bounded to $O(\text{active seconds})$.

## 3. Known Limitations & Disclaimers
1. **No External Attribution or Intent Classification**: Offline traffic monitoring analyzes observed counts, routes, and response statuses. It cannot determine attacker identity, malicious intent, architectural faults, or upstream network disruptions.
2. **Empirical Statistics**: Tail probabilities and $z$-scores are model-based empirical assessments under Poisson / Negative-Binomial dispersion assumptions; they do not represent absolute guarantees against false alarms.
3. **Identity Coverage Dependency**: When log sources emit events without usable identity tuples (`source` + `seq`/`trace_id`/`request_id`), exact deduplication is fundamentally impossible without guessing. PulseWatch retains these records unverified and explicitly discloses the exactness status in the report.
