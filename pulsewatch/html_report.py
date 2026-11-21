"""Static HTML report generator consuming canonical JSON report (pulsewatch.report/1)."""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List


def render_html_report(report_data: Dict[str, Any]) -> str:
    """Render an offline standalone HTML report from pulsewatch.report/1 data."""
    source_file = html.escape(str(report_data.get("input_metadata", {}).get("source_file", "")))
    source_sha = html.escape(str(report_data.get("input_metadata", {}).get("source_file_sha256", "N/A")))
    schema_version = html.escape(str(report_data.get("schema_version", "")))
    analysis_hash = html.escape(str(report_data.get("analysis_hash", "")))

    in_meta = report_data.get("input_metadata", {})
    ing_acc = report_data.get("ingestion_accounting", {})
    ded_acc = report_data.get("deduplication_accounting", {})
    coverage = report_data.get("coverage_and_gaps", {})
    baseline = report_data.get("baseline_status", {})
    detector = report_data.get("detector_summary", {})
    incidents = report_data.get("incidents", [])
    notices = report_data.get("data_notices", [])
    limitations = report_data.get("known_limitations", [])
    windows_60s = report_data.get("primary_60s_windows", [])

    # Incident cards with operator investigation leads
    incident_cards_html = []
    for inc in incidents:
        inc_id = html.escape(str(inc.get("incident_id", "")))
        shape = html.escape(str(inc.get("descriptive_pattern", {}).get("shape", "")))
        rationale = html.escape(str(inc.get("descriptive_pattern", {}).get("rationale", "")))
        obs = inc.get("observed_facts", {})
        findings = inc.get("statistical_findings", {})
        evidence = inc.get("contributor_evidence", {})
        leads = inc.get("investigation_leads", [])

        dur = obs.get("duration_sec", 0)
        peak = obs.get("peak_1s_rate", 0)
        total_obs = obs.get("total_observed_requests", 0)
        excess = obs.get("estimated_excess_requests", 0)
        max_z = findings.get("max_z_score", 0.0)
        scales = html.escape(str(findings.get("scales_fired", [])))
        start_iso = html.escape(str(obs.get("start_iso", "")))
        end_iso = html.escape(str(obs.get("end_iso", "")))

        tags_html = "".join(
            f'<span class="badge badge-tag">{html.escape(t)}</span> '
            for t in evidence.get("concentration_tags", [])
        )

        # Leading routes list
        top_routes = evidence.get("top_routes", {})
        routes_html = "".join(
            f"<li><code>{html.escape(r)}</code>: {cnt} reqs ({cnt / max(1, total_obs) * 100:.1f}%)</li>"
            for r, cnt in list(top_routes.items())[:3]
        ) or "<li>None recorded</li>"

        # Leading clients list
        top_clients = evidence.get("top_clients", {})
        clients_html = "".join(
            f"<li><code>{html.escape(c)}</code>: {cnt} reqs ({cnt / max(1, total_obs) * 100:.1f}%)</li>"
            for c, cnt in list(top_clients.items())[:3]
        ) or "<li>None recorded</li>"

        # Investigation leads
        leads_html = "".join(
            f"""
            <div class="lead-item">
                <div class="lead-trigger"><strong>Observation:</strong> {html.escape(str(lead.get('trigger', '')))}</div>
                <div class="lead-action"><strong>Suggested Next Check:</strong> {html.escape(str(lead.get('suggested_action', '')))}</div>
            </div>
            """
            for lead in leads
        ) or "<p class='lead-none'>No specific automated leads triggered; investigate general endpoint telemetry.</p>"

        incident_cards_html.append(f"""
        <div class="incident-card">
            <div class="incident-card-header">
                <div>
                    <span class="incident-id">{inc_id}</span>
                    <span class="badge badge-shape">{shape}</span>
                    {tags_html}
                </div>
                <div class="incident-time">{start_iso} &rarr; {end_iso} ({dur}s duration)</div>
            </div>

            <div class="incident-metrics">
                <div class="inc-metric">
                    <span class="lbl">Observed Traffic</span>
                    <span class="val">{total_obs:,}</span>
                </div>
                <div class="inc-metric">
                    <span class="lbl">Estimated Excess</span>
                    <span class="val" style="color: #b91c1c;">+{excess:,}</span>
                </div>
                <div class="inc-metric">
                    <span class="lbl">Peak Rate</span>
                    <span class="val">{peak:,} req/s</span>
                </div>
                <div class="inc-metric">
                    <span class="lbl">Max Statistical Z</span>
                    <span class="val">{max_z:.2f}</span>
                </div>
                <div class="inc-metric">
                    <span class="lbl">Scales Fired</span>
                    <span class="val">{scales}</span>
                </div>
            </div>

            <div class="contributors-grid">
                <div class="contributor-col">
                    <strong>Leading Routes:</strong>
                    <ul>{routes_html}</ul>
                </div>
                <div class="contributor-col">
                    <strong>Leading Callers:</strong>
                    <ul>{clients_html}</ul>
                </div>
            </div>

            <div class="leads-box">
                <div class="leads-header">OPERATOR INVESTIGATION LEADS</div>
                {leads_html}
            </div>

            <div class="incident-rationale">
                <strong>Pattern Rationale:</strong> {rationale}<br>
                <small style="color: #64748b;"><em>Note: {html.escape(str(inc.get('unknown_causes', '')))}</em></small>
            </div>
        </div>
        """)

    notice_items = "".join(f"<li>{html.escape(str(n))}</li>" for n in notices)
    limitation_items = "".join(f"<li>{html.escape(str(l))}</li>" for l in limitations)

    # Primary 60s windows rows
    window_rows = []
    for w in windows_60s[:30]:
        w_start = html.escape(str(w.get("start_iso", "")))
        w_req = w.get("total_requests", 0)
        w_active = w.get("active_seconds_count", 0)
        w_comp = '<span class="status-yes">Complete</span>' if w.get("is_complete") else '<span class="status-no">Partial / Gap</span>'
        s2 = w.get("status_2xx", 0)
        s4 = w.get("status_4xx", 0)
        s5 = w.get("status_5xx", 0)
        status_bar = f"<span class='s2'>{s2} 2xx</span> / <span class='s4'>{s4} 4xx</span> / <span class='s5'>{s5} 5xx</span>"

        window_rows.append(f"""
        <tr>
            <td><code>{w_start}</code></td>
            <td><strong>{w_req:,}</strong></td>
            <td>{w_active}/60s</td>
            <td>{status_bar}</td>
            <td>{w_comp}</td>
        </tr>
        """)

    exactness_status = html.escape(str(ded_acc.get("exactness_status", "unknown")))
    rate_val = baseline.get("fitted_rate_1s", baseline.get("median_rate_1s", 0))

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<title>PulseWatch Incident &amp; Traffic Report</title>
<style>
    body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        line-height: 1.5;
        color: #0f172a;
        background-color: #f1f5f9;
        margin: 0;
        padding: 24px;
    }}
    .container {{
        max-width: 1160px;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.07);
        padding: 32px;
    }}
    .header {{
        border-bottom: 2px solid #e2e8f0;
        padding-bottom: 16px;
        margin-bottom: 24px;
    }}
    .header h1 {{
        margin: 0 0 6px 0;
        font-size: 1.8rem;
        color: #0f172a;
    }}
    .header-sub {{
        color: #64748b;
        font-size: 0.9rem;
    }}
    .exec-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
    }}
    .exec-card {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 16px;
    }}
    .exec-card .lbl {{
        font-size: 0.75rem;
        text-transform: uppercase;
        color: #64748b;
        font-weight: 700;
        margin-bottom: 4px;
    }}
    .exec-card .val {{
        font-size: 1.35rem;
        font-weight: 700;
        color: #0f172a;
    }}
    .panel-integrity {{
        background: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        padding: 18px;
        margin-bottom: 24px;
    }}
    .panel-integrity h3 {{
        margin: 0 0 12px 0;
        font-size: 1.05rem;
        color: #1e293b;
    }}
    .integrity-cols {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 20px;
        font-size: 0.9rem;
    }}
    .incident-card {{
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-left: 5px solid #2563eb;
        border-radius: 6px;
        padding: 20px;
        margin-bottom: 24px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }}
    .incident-card-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
        flex-wrap: wrap;
        gap: 8px;
    }}
    .incident-id {{
        font-size: 1.25rem;
        font-weight: 700;
        color: #1e3a8a;
        margin-right: 8px;
    }}
    .incident-time {{
        font-size: 0.85rem;
        color: #475569;
        font-family: monospace;
    }}
    .incident-metrics {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        background: #f8fafc;
        padding: 12px;
        border-radius: 6px;
        margin-bottom: 16px;
        gap: 8px;
    }}
    .inc-metric .lbl {{
        display: block;
        font-size: 0.75rem;
        color: #64748b;
        text-transform: uppercase;
        font-weight: 600;
    }}
    .inc-metric .val {{
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
    }}
    .contributors-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-bottom: 16px;
        font-size: 0.88rem;
    }}
    .contributor-col ul {{
        margin: 4px 0 0 0;
        padding-left: 20px;
    }}
    .leads-box {{
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        border-radius: 6px;
        padding: 14px;
        margin-bottom: 14px;
    }}
    .leads-header {{
        font-size: 0.8rem;
        font-weight: 700;
        color: #1e40af;
        letter-spacing: 0.05em;
        margin-bottom: 10px;
    }}
    .lead-item {{
        margin-bottom: 8px;
        padding-bottom: 8px;
        border-bottom: 1px dashed #cbd5e1;
        font-size: 0.88rem;
    }}
    .lead-item:last-child {{
        margin-bottom: 0;
        padding-bottom: 0;
        border-bottom: none;
    }}
    .lead-trigger {{
        color: #334155;
    }}
    .lead-action {{
        color: #1e3a8a;
        margin-top: 2px;
    }}
    .lead-none {{
        font-size: 0.85rem;
        color: #64748b;
        margin: 0;
    }}
    .incident-rationale {{
        font-size: 0.85rem;
        color: #334155;
    }}
    .badge {{
        display: inline-block;
        padding: 3px 8px;
        font-size: 0.75rem;
        border-radius: 4px;
        font-weight: 600;
        text-transform: uppercase;
    }}
    .badge-shape {{
        background: #dbeafe;
        color: #1e40af;
    }}
    .badge-tag {{
        background: #fef3c7;
        color: #92400e;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 24px;
        font-size: 0.88rem;
    }}
    th, td {{
        text-align: left;
        padding: 8px 12px;
        border-bottom: 1px solid #e2e8f0;
    }}
    th {{
        background: #f8fafc;
        color: #475569;
        font-size: 0.78rem;
        text-transform: uppercase;
    }}
    .status-yes {{
        color: #15803d;
        font-weight: 600;
    }}
    .status-no {{
        color: #b91c1c;
        font-weight: 600;
    }}
    .s2 {{ color: #15803d; }}
    .s4 {{ color: #d97706; }}
    .s5 {{ color: #dc2626; }}
    .notices {{
        background: #fffbeb;
        border-left: 4px solid #f59e0b;
        padding: 14px;
        margin-bottom: 24px;
        font-size: 0.88rem;
    }}
    .limitations {{
        background: #f8fafc;
        border-left: 4px solid #64748b;
        padding: 14px;
        margin-bottom: 24px;
        font-size: 0.85rem;
    }}
    .hash-box {{
        font-family: monospace;
        font-size: 0.8rem;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        padding: 8px 12px;
        border-radius: 4px;
        word-break: break-all;
    }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>PulseWatch Incident &amp; Traffic Report</h1>
        <div class="header-sub">Source: <code>{source_file}</code> &bull; SHA-256: <code>{source_sha[:16]}...</code> &bull; Contract: <code>{schema_version}</code></div>
    </div>

    <!-- Executive Summary Grid -->
    <div class="exec-grid">
        <div class="exec-card">
            <div class="lbl">Retained Records</div>
            <div class="val">{ded_acc.get("retained_records", 0):,}</div>
        </div>
        <div class="exec-card">
            <div class="lbl">Duplicates Filtered</div>
            <div class="val">{ded_acc.get("duplicates_removed", 0):,}</div>
        </div>
        <div class="exec-card">
            <div class="lbl">Coverage Ratio</div>
            <div class="val">{coverage.get("coverage_ratio", 0.0) * 100:.1f}%</div>
        </div>
        <div class="exec-card">
            <div class="lbl">Baseline Status</div>
            <div class="val">{html.escape(str(baseline.get("reliability", "unknown")).upper())} <span style="font-size: 0.9rem; font-weight: 400;">({rate_val:.2f} r/s)</span></div>
        </div>
        <div class="exec-card">
            <div class="lbl">Incidents Detected</div>
            <div class="val" style="color: {'#b91c1c' if incidents else '#15803d'};">{len(incidents)}</div>
        </div>
    </div>

    <!-- Telemetry Integrity & Deduplication Panel -->
    <div class="panel-integrity">
        <h3>Telemetry Integrity &amp; Coverage Ledger</h3>
        <div class="integrity-cols">
            <div>
                <strong>Identity &amp; Deduplication Exactness:</strong> <code>{exactness_status}</code><br>
                &bull; Verified Unique Records: {ded_acc.get("verified_unique_records", 0):,}<br>
                &bull; Ineligible (Identity Missing): {ded_acc.get("identity_ineligible", 0):,}<br>
                &bull; Conflicting Attribute Collisions: {ded_acc.get("identity_conflicts", 0):,}<br>
                &bull; Capacity Degradation: {"Yes (LRU Evicted)" if ded_acc.get("cap_degraded") else "No (Within Bounds)"}
            </div>
            <div>
                <strong>Temporal Coverage &amp; Gaps:</strong><br>
                &bull; Active Observation Seconds: {coverage.get("active_seconds", 0):,}s<br>
                &bull; Known Zero Traffic: {coverage.get("known_zero_seconds", 0):,}s<br>
                &bull; Assumed Zero Traffic (&le;900s): {coverage.get("assumed_zero_seconds", 0):,}s<br>
                &bull; Unknown Coverage Gaps (&gt;900s): {coverage.get("unknown_gaps_count", 0)} intervals ({coverage.get("unknown_gap_seconds", 0):,}s)
            </div>
        </div>
    </div>

    {f'<div class="notices"><strong>Data Integrity Notices:</strong><ul style="margin: 4px 0 0 0; padding-left: 20px;">{notice_items}</ul></div>' if notice_items else ''}

    <h2>Incident Investigations ({len(incidents)})</h2>
    {"<p>No traffic anomalies detected in observed intervals.</p>" if not incident_cards_html else "".join(incident_cards_html)}

    <h2>Primary 60-Second Windows (Sample)</h2>
    <table>
        <thead>
            <tr>
                <th>Window Start (UTC)</th>
                <th>Observed Requests</th>
                <th>Active Seconds</th>
                <th>Status Breakdown (2xx / 4xx / 5xx)</th>
                <th>Observation Integrity</th>
            </tr>
        </thead>
        <tbody>
            {"".join(window_rows)}
        </tbody>
    </table>

    <div class="limitations">
        <strong>Offline Monitoring Known Limitations:</strong>
        <ul style="margin: 4px 0 0 0; padding-left: 20px;">{limitation_items}</ul>
    </div>

    <div>
        <strong>Deterministic Analysis SHA-256 Digest:</strong>
        <div class="hash-box">{analysis_hash}</div>
    </div>
</div>
</body>
</html>
"""
    return html_content
