"""PulseWatch Static HTML Report Generator: 10-section offline, print-optimised forensic report."""

from __future__ import annotations

import html
import json
import math
from typing import Any, Dict, List, Optional

from pulsewatch.ablation_view import render_ablation_html


def _format_num(val: Any) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, int):
        return f"{val:,}"
    if isinstance(val, float):
        if abs(val) >= 1000:
            return f"{val:,.1f}"
        if abs(val) < 0.001 and val != 0.0:
            return f"{val:.4e}"
        return f"{val:.3f}"
    return str(val)


def _render_timeline_svg(windows_60s: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> str:
    """Generate pure inline SVG timeline chart of 60s request counts and incident intervals."""
    if not windows_60s:
        return "<p style='color:#64748b; font-style:italic;'>No 60-second window data available for timeline.</p>"

    counts = [w.get("total_requests", 0) for w in windows_60s]
    max_c = max(counts) if counts else 1
    max_c = max(max_c, 10)

    width = 960
    height = 200
    padding_left = 60
    padding_right = 30
    padding_top = 20
    padding_bottom = 35
    chart_w = width - padding_left - padding_right
    chart_h = height - padding_top - padding_bottom

    n_bars = len(windows_60s)
    bar_w = max(1.0, chart_w / max(1, n_bars))

    # Identify windows covered by incidents
    inc_intervals = []
    for inc in incidents:
        obs = inc.get("observed_facts", {})
        s_sec = obs.get("start_sec")
        e_sec = obs.get("end_sec")
        if s_sec is not None and e_sec is not None:
            inc_intervals.append((s_sec, e_sec))

    bars_svg = []
    for i, w in enumerate(windows_60s):
        c = w.get("total_requests", 0)
        h_bar = (c / max_c) * chart_h
        x = padding_left + i * bar_w
        y = padding_top + chart_h - h_bar

        # Check if window overlaps any incident
        w_start = w.get("start_sec", 0)
        w_end = w.get("end_sec", w_start + 60)
        is_inc = any(not (w_end <= s or w_start >= e) for s, e in inc_intervals)

        fill = "#ef4444" if is_inc else "#3b82f6"
        iso = html.escape(str(w.get("start_iso", "")))
        bars_svg.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(1.0, bar_w - 0.5):.1f}" height="{h_bar:.1f}" '
            f'fill="{fill}" opacity="0.85"><title>{iso}: {c} reqs</title></rect>'
        )

    # Grid lines and y-axis ticks
    y_ticks = [0, max_c // 2, max_c]
    grid_svg = []
    for yt in y_ticks:
        y_pos = padding_top + chart_h - (yt / max_c) * chart_h
        grid_svg.append(
            f'<line x1="{padding_left}" y1="{y_pos:.1f}" x2="{width - padding_right}" y2="{y_pos:.1f}" '
            f'stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3,3" />'
        )
        grid_svg.append(
            f'<text x="{padding_left - 8}" y="{y_pos + 4:.1f}" fill="#64748b" font-size="10" '
            f'text-anchor="end">{yt:,}</text>'
        )

    # Time labels
    first_iso = html.escape(str(windows_60s[0].get("start_iso", "")[:19]))
    last_iso = html.escape(str(windows_60s[-1].get("start_iso", "")[:19]))
    grid_svg.append(
        f'<text x="{padding_left}" y="{height - 8}" fill="#64748b" font-size="10" text-anchor="start">{first_iso}</text>'
    )
    grid_svg.append(
        f'<text x="{width - padding_right}" y="{height - 8}" fill="#64748b" font-size="10" text-anchor="end">{last_iso}</text>'
    )

    legend_svg = (
        f'<g transform="translate({width - padding_right - 180}, {padding_top + 5})">'
        f'<rect x="0" y="0" width="12" height="12" fill="#3b82f6" />'
        f'<text x="18" y="10" fill="#475569" font-size="11">Nominal (60s)</text>'
        f'<rect x="100" y="0" width="12" height="12" fill="#ef4444" />'
        f'<text x="118" y="10" fill="#475569" font-size="11">Incident Window</text>'
        f'</g>'
    )

    return f"""
    <div class="svg-timeline-container" style="overflow-x:auto;">
        <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" style="max-width:{width}px; display:block; margin:0 auto;">
            {''.join(grid_svg)}
            {''.join(bars_svg)}
            {legend_svg}
        </svg>
    </div>
    """


def render_html_report(
    report_data: Dict[str, Any],
    manifest_data: Optional[Dict[str, Any]] = None,
    ablation_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Render comprehensive 10-section offline, print-optimised HTML report."""
    # Top-level and schema metadata
    schema_ver = html.escape(str(report_data.get("schema_version", "pulsewatch.report/1")))
    rep_rev = html.escape(str(report_data.get("report_revision", 1)))
    analysis_hash = html.escape(str(report_data.get("analysis_hash", "")))
    pw_version = html.escape(str(report_data.get("pulsewatch_version", "0.2.0")))

    # Sections
    inp = report_data.get("input", {})
    cov = report_data.get("coverage", {})
    base = report_data.get("baseline_profile", report_data.get("baseline_status", {}))
    incidents = report_data.get("incidents", [])
    notices = report_data.get("data_notices", [])
    limitations = report_data.get("known_limitations", [])
    windows_60s = report_data.get("primary_60s_windows", [])
    cfg = report_data.get("analysis_config", {})
    calib = report_data.get("calibration_status", {})
    cfg_digest = html.escape(str(report_data.get("detector_config_digest", "")))
    second_pass_status = html.escape(str(report_data.get("second_pass_status", "none")))

    source_label = html.escape(str(inp.get("source_label", report_data.get("input_metadata", {}).get("source_file", ""))))
    source_sha = html.escape(str(inp.get("source_sha256", report_data.get("input_metadata", {}).get("source_file_sha256", "N/A"))))

    # SECTION 1: Status Panel
    reliability = base.get("reliability", "unknown")
    inc_count = len(incidents)

    if reliability == "abstain":
        decision_label = "DETECTOR ABSTAINED"
        decision_class = "badge-abstain"
        decision_desc = f"Analysis abstained due to telemetry conditions: {html.escape(str(base.get('abstain_reason', 'insufficient data')))}"
    elif inc_count > 0:
        decision_label = f"{inc_count} ANOMALY INCIDENT{'S' if inc_count > 1 else ''} DETECTED"
        decision_class = "badge-danger"
        decision_desc = f"Identified {inc_count} statistically significant traffic surge incident(s)."
    else:
        decision_label = "TRAFFIC NOMINAL"
        decision_class = "badge-success"
        decision_desc = "No statistically significant multi-scale anomaly incidents detected in valid intervals."

    rel_class = {
        "high": "badge-success",
        "moderate": "badge-info",
        "low": "badge-warning",
        "abstain": "badge-abstain",
    }.get(reliability, "badge-neutral")

    calib_msg = html.escape(str(calib.get("status_message", "uncalibrated_fixed_default")))
    is_calibrated = calib.get("is_calibrated", False)
    calib_class = "badge-success" if is_calibrated else "badge-info"

    # SECTION 2: Input & Coverage Quality
    lines_read = inp.get("total_lines_read", report_data.get("ingestion_accounting", {}).get("total_lines_read", 0))
    valid_recs = inp.get("valid_records", report_data.get("ingestion_accounting", {}).get("valid_records", 0))
    span_sec = cov.get("total_span_sec", report_data.get("coverage_and_gaps", {}).get("total_span_sec", 0))
    act_sec = cov.get("active_seconds", report_data.get("coverage_and_gaps", {}).get("active_seconds", 0))
    cov_ratio = cov.get("coverage_ratio", report_data.get("coverage_and_gaps", {}).get("coverage_ratio", 0.0))
    gaps_count = cov.get("unknown_gaps_count", len(report_data.get("coverage_and_gaps", {}).get("unknown_gaps", [])))

    # SECTION 3: Identity & Deduplication
    retained_recs = inp.get("retained_records", report_data.get("deduplication_accounting", {}).get("retained_records", 0))
    dups_removed = inp.get("duplicates_removed", report_data.get("deduplication_accounting", {}).get("duplicates_removed", 0))
    exactness = html.escape(str(inp.get("exactness_status", report_data.get("deduplication_accounting", {}).get("exactness_status", "unknown"))))
    dup_pct = (dups_removed / max(1, valid_recs)) * 100

    # SECTION 4: Baseline Reliability Table
    fitted_rate = base.get("fitted_rate_1s", 0.0)
    rate_stability = base.get("rate_stability", {})
    rs_label = html.escape(str(rate_stability.get("label", "unassessed")))
    rs_q = rate_stability.get("statistic")
    rs_phi = rate_stability.get("phi")
    rs_q_str = f"{rs_q:.2f}" if isinstance(rs_q, (int, float)) else "N/A"
    rs_phi_str = f"{rs_phi:.2f}" if isinstance(rs_phi, (int, float)) else "N/A"
    part_rates = rate_stability.get("part_rates", [])
    part_rates_str = ", ".join(f"{r:.2f}" for r in part_rates) if part_rates else "N/A"

    disp_scales = base.get("dispersion_by_scale", {})
    disp_cells = []
    for sc in ["10", "60", "300"]:
        d_info = disp_scales.get(sc, {})
        phi_v = d_info.get("dispersion_phi", 1.0)
        disp_cells.append(f"<td><strong>Scale {sc}s:</strong> &phi; = {phi_v:.2f} ({d_info.get('model', 'poisson')})</td>")

    masked_cnt = base.get("masked_intervals_count", len(base.get("masked_intervals", [])))
    frac_time_masked = base.get("fraction_time_masked", 0.0)
    frac_ev_masked = base.get("fraction_events_masked", 0.0)

    # SECTION 5 & 6: Incidents and Multi-Scale Evidence Details
    incident_rows_html = []
    incident_details_html = []
    for inc in incidents:
        inc_id = html.escape(str(inc.get("incident_id", "")))
        shape = html.escape(str(inc.get("descriptive_pattern", {}).get("shape", "")))
        obs = inc.get("observed_facts", {})
        findings = inc.get("statistical_findings", {})
        evidence = inc.get("contributor_evidence", {})
        leads = inc.get("investigation_leads", [])

        dur = obs.get("duration_sec", 0)
        peak = obs.get("peak_1s_rate", 0)
        total_obs = obs.get("total_observed_requests", 0)
        excess = obs.get("estimated_excess_requests", 0)
        max_z = findings.get("max_z_score", 0.0)
        scales_fired = findings.get("scales_fired", [])
        scales_str = html.escape(str(scales_fired))
        o_over_e = findings.get("observed_over_expected")
        o_over_e_str = f"{o_over_e:.2f}x" if o_over_e is not None else "N/A"
        min_p = findings.get("min_p_value")
        min_p_str = f"{min_p:.4e}" if min_p is not None else "N/A"
        tails_exact = findings.get("all_tails_exact", True)
        omitted_mass = findings.get("max_omitted_mass_bound", 0.0)
        start_iso = html.escape(str(obs.get("start_iso", "")))
        end_iso = html.escape(str(obs.get("end_iso", "")))

        tags_html = " ".join(
            f'<span class="badge badge-tag">{html.escape(t)}</span>'
            for t in evidence.get("concentration_tags", [])
        ) or "<span style='color:#94a3b8; font-size:0.85rem;'>None</span>"

        incident_rows_html.append(f"""
        <tr>
            <td><strong>{inc_id}</strong></td>
            <td>{start_iso}<br>&rarr; {end_iso}</td>
            <td>{dur}s</td>
            <td>{total_obs:,}</td>
            <td style="color:#b91c1c; font-weight:600;">+{excess:,}</td>
            <td>{peak:,} req/s</td>
            <td><span class="badge badge-shape">{shape}</span></td>
            <td>{tags_html}</td>
        </tr>
        """)

        # Windows table (up to 40)
        det_windows = findings.get("detection_windows", [])
        win_rows = []
        for dw in det_windows[:20]:
            win_rows.append(f"""
            <tr>
                <td>{dw.get('scale_sec')}s</td>
                <td>{dw.get('start_sec')} &rarr; {dw.get('end_sec')}</td>
                <td>{dw.get('observed_count'):,}</td>
                <td>{dw.get('baseline_count', 0.0):.1f}</td>
                <td style="color:#b91c1c;">+{dw.get('excess_count', 0):,}</td>
                <td><strong>{dw.get('z_score', 0.0):.2f}</strong></td>
                <td>{dw.get('elevation_ratio', 0.0):.2f}x</td>
                <td>{dw.get('p_value', 1.0):.2e}</td>
            </tr>
            """)

        # SECTION 7: Contributors & Leads
        top_routes = evidence.get("top_routes", {})
        routes_li = "".join(
            f"<li><code>{html.escape(r)}</code>: {cnt:,} reqs ({cnt / max(1, total_obs) * 100:.1f}%)</li>"
            for r, cnt in list(top_routes.items())[:5]
        ) or "<li>None recorded</li>"

        top_clients = evidence.get("top_clients", {})
        clients_li = "".join(
            f"<li><code>{html.escape(c)}</code>: {cnt:,} reqs ({cnt / max(1, total_obs) * 100:.1f}%)</li>"
            for c, cnt in list(top_clients.items())[:5]
        ) or "<li>None recorded</li>"

        leads_divs = "".join(
            f"""
            <div class="lead-item">
                <div class="lead-trigger"><strong>Observation:</strong> {html.escape(str(ld.get('trigger', '')))}</div>
                <div class="lead-action"><strong>Suggested Next Check:</strong> {html.escape(str(ld.get('suggested_action', '')))}</div>
            </div>
            """
            for ld in leads
        ) or "<p class='lead-none'>No specific automated investigative leads triggered.</p>"

        incident_details_html.append(f"""
        <div class="card incident-detail-card" style="margin-top:1.5rem;">
            <div class="card-header" style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="margin:0;">Incident {inc_id} Details</h3>
                <div>{tags_html}</div>
            </div>
            <div class="card-body">
                <div class="metrics-grid">
                    <div class="metric-card">
                        <span class="label">Max Z-Score</span>
                        <span class="value" style="color:#b91c1c;">{max_z:.2f}</span>
                        <span class="subtext">Scales: {scales_str}</span>
                    </div>
                    <div class="metric-card">
                        <span class="label">Observed / Expected</span>
                        <span class="value">{o_over_e_str}</span>
                        <span class="subtext">Elevation multiplier</span>
                    </div>
                    <div class="metric-card">
                        <span class="label">Min P-Value</span>
                        <span class="value">{min_p_str}</span>
                        <span class="subtext">Exact tails: {'Yes' if tails_exact else 'Approx'}</span>
                    </div>
                    <div class="metric-card">
                        <span class="label">Omitted Mass Bound</span>
                        <span class="value">{omitted_mass:.2e}</span>
                        <span class="subtext">Tail truncation bound</span>
                    </div>
                </div>

                <h4 style="margin:1.25rem 0 0.5rem 0;">Multi-Scale Scanning Evidence Windows ({len(det_windows)} total)</h4>
                <table class="data-table" style="font-size:0.85rem;">
                    <thead>
                        <tr>
                            <th>Scale</th>
                            <th>Interval (s)</th>
                            <th>Observed</th>
                            <th>Baseline</th>
                            <th>Excess</th>
                            <th>Z-Score</th>
                            <th>Elevation</th>
                            <th>P-Value</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(win_rows)}
                    </tbody>
                </table>

                <h4 style="margin:1.25rem 0 0.5rem 0;">Top Contributors &amp; Concentration</h4>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem;">
                    <div>
                        <strong>Leading Routes:</strong>
                        <ul style="margin:0.25rem 0; padding-left:1.25rem;">{routes_li}</ul>
                    </div>
                    <div>
                        <strong>Leading Clients:</strong>
                        <ul style="margin:0.25rem 0; padding-left:1.25rem;">{clients_li}</ul>
                    </div>
                </div>

                <h4 style="margin:1.25rem 0 0.5rem 0;">Forensic Investigation Leads</h4>
                {leads_divs}
            </div>
        </div>
        """)

    # Timeline SVG
    timeline_svg = _render_timeline_svg(windows_60s, incidents)

    # SECTION 8: Warnings
    notices_li = "".join(f"<li>{html.escape(str(n))}</li>" for n in notices) or "<li>No data notices or quality warnings issued.</li>"

    # SECTION 9: Limitations
    limitations_li = "".join(f"<li>{html.escape(str(l))}</li>" for l in limitations)

    # SECTION 10: Integrity & Verification
    manifest_info = ""
    if manifest_data:
        m_sha = html.escape(str(manifest_data.get("manifest_sha256", "")))
        m_run_id = html.escape(str(manifest_data.get("run_id", "")))
        manifest_info = f"""
        <tr>
            <td><strong>Sidecar Manifest</strong></td>
            <td>Verified (Run ID: <code>{m_run_id}</code>)<br><small>SHA-256: <code>{m_sha}</code></small></td>
        </tr>
        """

    # APPENDIX A: Ablation View
    ablation_section = ""
    if ablation_data:
        ablation_html = render_ablation_html(ablation_data)
        ablation_section = f"""
        <div class="card" id="appendix-ablation" style="margin-top:2rem;">
            <div class="card-header">
                <h2 style="margin:0;">Appendix A: Component Ablation Study</h2>
            </div>
            <div class="card-body">
                {ablation_html}
            </div>
        </div>
        """

    # APPENDIX B: 60s Windows Table
    window_rows = []
    for w in windows_60s[:100]:
        w_iso = html.escape(str(w.get("start_iso", "")))
        w_reqs = w.get("total_requests", 0)
        w_act = w.get("active_seconds_count", 0)
        w_comp = "Yes" if w.get("is_complete", True) else "No (Partial/Gap)"
        w_status = f"{w.get('status_2xx', 0)} / {w.get('status_4xx', 0)} / {w.get('status_5xx', 0)}"
        window_rows.append(f"""
        <tr>
            <td><code>{w_iso}</code></td>
            <td>{w_reqs:,}</td>
            <td>{w_act}s</td>
            <td>{w_status}</td>
            <td>{w_comp}</td>
        </tr>
        """)

    appendix_windows = f"""
    <div class="card" id="appendix-windows" style="margin-top:2rem;">
        <div class="card-header">
            <h2 style="margin:0;">Appendix B: Primary 60-Second Windows ({len(windows_60s)} total)</h2>
        </div>
        <div class="card-body">
            <p style="color:#64748b; font-size:0.85rem;">Displaying up to first 100 UTC 60-second primary evaluation windows.</p>
            <div style="max-height: 400px; overflow-y:auto;">
                <table class="data-table" style="font-size:0.85rem;">
                    <thead>
                        <tr>
                            <th>Window Start (UTC)</th>
                            <th>Requests</th>
                            <th>Active Sec</th>
                            <th>Status (2xx / 4xx / 5xx)</th>
                            <th>Complete</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(window_rows)}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PulseWatch Incident &amp; Traffic Report: {source_label}</title>
    <style>
        :root {{
            --bg: #f8fafc;
            --surface: #ffffff;
            --text: #0f172a;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --primary: #2563eb;
            --success: #15803d;
            --warning: #d97706;
            --danger: #b91c1c;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            padding: 2rem 1.5rem;
            background-color: var(--bg);
            color: var(--text);
            font-family: var(--font-family);
            line-height: 1.5;
        }}
        .container {{
            max-width: 1040px;
            margin: 0 auto;
        }}
        header {{
            margin-bottom: 2rem;
            border-bottom: 2px solid var(--border);
            padding-bottom: 1.5rem;
        }}
        h1, h2, h3, h4 {{
            color: var(--text);
            font-weight: 700;
            letter-spacing: -0.02em;
        }}
        h1 {{ font-size: 1.75rem; margin: 0 0 0.5rem 0; }}
        h2 {{ font-size: 1.35rem; margin: 0 0 1rem 0; }}
        h3 {{ font-size: 1.1rem; margin: 0 0 0.5rem 0; }}
        code {{
            font-family: var(--font-mono);
            font-size: 0.88em;
            background: #f1f5f9;
            padding: 0.15em 0.35em;
            border-radius: 4px;
            color: #1e293b;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            overflow: hidden;
        }}
        .card-header {{
            padding: 1rem 1.25rem;
            background: #f8fafc;
            border-bottom: 1px solid var(--border);
        }}
        .card-body {{
            padding: 1.25rem;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
        }}
        .metric-card {{
            background: #f8fafc;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 0.85rem 1rem;
            display: flex;
            flex-direction: column;
        }}
        .metric-card .label {{
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--text-muted);
            margin-bottom: 0.25rem;
        }}
        .metric-card .value {{
            font-size: 1.45rem;
            font-weight: 700;
            color: var(--text);
        }}
        .metric-card .subtext {{
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}
        .badge {{
            display: inline-block;
            padding: 0.2em 0.6em;
            font-size: 0.78rem;
            font-weight: 600;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        .badge-success {{ background: #dcfce7; color: #166534; }}
        .badge-warning {{ background: #fef3c7; color: #92400e; }}
        .badge-danger {{ background: #fee2e2; color: #991b1b; }}
        .badge-abstain {{ background: #ffedd5; color: #9a3412; }}
        .badge-info {{ background: #e0f2fe; color: #075985; }}
        .badge-neutral {{ background: #f1f5f9; color: #475569; }}
        .badge-shape {{ background: #ede9fe; color: #5b21b6; }}
        .badge-tag {{ background: #e2e8f0; color: #334155; font-size: 0.72rem; text-transform: none; }}
        .data-table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        .data-table th, .data-table td {{
            padding: 0.65rem 0.75rem;
            border-bottom: 1px solid var(--border);
        }}
        .data-table th {{
            background: #f8fafc;
            font-weight: 600;
            color: #475569;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}
        .lead-item {{
            background: #f8fafc;
            border-left: 3px solid var(--primary);
            padding: 0.65rem 0.85rem;
            margin-bottom: 0.5rem;
            border-radius: 0 4px 4px 0;
            font-size: 0.9rem;
        }}
        .lead-trigger {{ margin-bottom: 0.25rem; }}
        .notice-box {{
            padding: 0.85rem 1rem;
            border-radius: 6px;
            margin-bottom: 1rem;
            font-size: 0.9rem;
        }}
        .notice-warning {{ background: #fffbeb; border-left: 4px solid var(--warning); color: #78350f; }}
        .notice-danger {{ background: #fef2f2; border-left: 4px solid var(--danger); color: #7f1d1d; }}
        @media print {{
            body {{ background: #ffffff; padding: 0; font-size: 10pt; }}
            .card {{ box-shadow: none; border: 1px solid #cbd5e1; page-break-inside: avoid; }}
            header {{ border-bottom: 1px solid #94a3b8; }}
            .no-print {{ display: none; }}
        }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
                <h1>PulseWatch Incident &amp; Traffic Report</h1>
                <div style="color:var(--text-muted); font-size:0.95rem;">
                    Target: <code>{source_label}</code> | PulseWatch v{pw_version}
                </div>
            </div>
            <div style="text-align:right;">
                <span class="badge {decision_class}" style="font-size:1rem; padding:0.4em 0.8em;">{decision_label}</span>
                <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.4rem;">
                    Report Revision: {rep_rev}
                </div>
            </div>
        </div>
    </header>

    <!-- SECTION 1: Status Panel -->
    <div class="card" id="section-status">
        <div class="card-header">
            <h2 style="margin:0;">1. Executive Status Panel</h2>
        </div>
        <div class="card-body">
            <div class="metrics-grid">
                <div class="metric-card">
                    <span class="label">Decision Verdict</span>
                    <span class="value" style="font-size:1.15rem;">{decision_label}</span>
                    <span class="subtext">{decision_desc}</span>
                </div>
                <div class="metric-card">
                    <span class="label">Baseline Reliability</span>
                    <span class="value"><span class="badge {rel_class}">{reliability}</span></span>
                    <span class="subtext">Rate: {fitted_rate:.2f} req/s</span>
                </div>
                <div class="metric-card">
                    <span class="label">Calibration Mode</span>
                    <span class="value"><span class="badge {calib_class}">{calib_msg}</span></span>
                    <span class="subtext">Active Z: {calib.get('active_z_threshold', 3.5)}</span>
                </div>
                <div class="metric-card">
                    <span class="label">Replay Verification</span>
                    <span class="value">{second_pass_status}</span>
                    <span class="subtext">Second-pass byte match</span>
                </div>
            </div>
        </div>
    </div>

    <!-- SECTION 2: Input & Coverage Quality -->
    <div class="card" id="section-input-coverage">
        <div class="card-header">
            <h2 style="margin:0;">2. Input &amp; Coverage Quality</h2>
        </div>
        <div class="card-body">
            <div class="metrics-grid">
                <div class="metric-card">
                    <span class="label">Total Lines Read</span>
                    <span class="value">{lines_read:,}</span>
                    <span class="subtext">Valid records: {valid_recs:,}</span>
                </div>
                <div class="metric-card">
                    <span class="label">Total Span</span>
                    <span class="value">{span_sec:,}s</span>
                    <span class="subtext">Active: {act_sec:,}s ({cov_ratio * 100:.1f}%)</span>
                </div>
                <div class="metric-card">
                    <span class="label">Coverage Ratio</span>
                    <span class="value">{cov_ratio * 100:.1f}%</span>
                    <span class="subtext">Active / Total Span</span>
                </div>
                <div class="metric-card">
                    <span class="label">Unknown Gaps</span>
                    <span class="value">{gaps_count}</span>
                    <span class="subtext">Gaps exceeding threshold</span>
                </div>
            </div>
            <div style="margin-top:1rem; font-size:0.85rem; color:var(--text-muted);">
                <strong>Input File SHA-256:</strong> <code>{source_sha}</code>
            </div>
        </div>
    </div>

    <!-- SECTION 3: Identity & Deduplication -->
    <div class="card" id="section-dedup">
        <div class="card-header">
            <h2 style="margin:0;">3. Identity &amp; Deduplication Accounting</h2>
        </div>
        <div class="card-body">
            <div class="metrics-grid">
                <div class="metric-card">
                    <span class="label">Valid Records</span>
                    <span class="value">{valid_recs:,}</span>
                    <span class="subtext">Ingested JSONL records</span>
                </div>
                <div class="metric-card">
                    <span class="label">Retained Records</span>
                    <span class="value">{retained_recs:,}</span>
                    <span class="subtext">Unique event identities</span>
                </div>
                <div class="metric-card">
                    <span class="label">Duplicates Removed</span>
                    <span class="value">{dups_removed:,}</span>
                    <span class="subtext">{dup_pct:.2f}% duplicate reduction</span>
                </div>
                <div class="metric-card">
                    <span class="label">Exactness Status</span>
                    <span class="value">{exactness}</span>
                    <span class="subtext">Single-pass bit-packed dedup</span>
                </div>
            </div>
        </div>
    </div>

    <!-- SECTION 4: Baseline Reliability -->
    <div class="card" id="section-baseline">
        <div class="card-header">
            <h2 style="margin:0;">4. Statistical Baseline &amp; Rate Stability</h2>
        </div>
        <div class="card-body">
            <table class="data-table">
                <tbody>
                    <tr>
                        <td style="width:25%;"><strong>Fitted Rate (&lambda;)</strong></td>
                        <td style="width:25%;"><strong>{fitted_rate:.3f} req/s</strong></td>
                        <td style="width:25%;"><strong>Rate Stability</strong></td>
                        <td style="width:25%;"><span class="badge {'badge-success' if rs_label == 'stable' else 'badge-warning'}">{rs_label}</span> (Score Q = {rs_q_str}, &phi; = {rs_phi_str})</td>
                    </tr>
                    <tr>
                        <td><strong>4-Part Temporal Rates</strong></td>
                        <td><code>[{part_rates_str}]</code> req/s</td>
                        <td><strong>Masking Impact</strong></td>
                        <td>{masked_cnt} intervals (Time: {frac_time_masked * 100:.1f}%, Events: {frac_ev_masked * 100:.1f}%)</td>
                    </tr>
                    <tr>
                        <td><strong>Quasi-Poisson &phi;</strong></td>
                        <td colspan="3">
                            <table style="width:100%; border:none;">
                                <tr>{''.join(disp_cells)}</tr>
                            </table>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- SECTION 5: Incident Summary & 60s Timeline -->
    <div class="card" id="section-incidents">
        <div class="card-header">
            <h2 style="margin:0;">5. Incident Summary &amp; 60-Second UTC Timeline</h2>
        </div>
        <div class="card-body">
            {timeline_svg}
            <div style="margin-top:1.5rem;">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Incident ID</th>
                            <th>Time Range (UTC)</th>
                            <th>Duration</th>
                            <th>Observed</th>
                            <th>Excess</th>
                            <th>Peak Rate</th>
                            <th>Shape</th>
                            <th>Tags</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(incident_rows_html) if incident_rows_html else '<tr><td colspan="8" style="text-align:center; color:#64748b; padding:1.5rem;">No anomaly incidents detected in analyzed intervals.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- SECTION 6 & 7: Multi-Scale Evidence Details & Contributors -->
    {f'<div id="section-evidence"><h2 style="margin-top:2rem;">6 &amp; 7. Multi-Scale Evidence Details &amp; Forensic Contributors</h2>{"".join(incident_details_html)}</div>' if incident_details_html else ''}

    <!-- SECTION 8: Warnings & Data Notices -->
    <div class="card" id="section-warnings" style="margin-top:2rem;">
        <div class="card-header">
            <h2 style="margin:0;">8. Data Notices &amp; Quality Warnings</h2>
        </div>
        <div class="card-body">
            <ul style="margin:0; padding-left:1.25rem;">
                {notices_li}
            </ul>
        </div>
    </div>

    <!-- SECTION 9: Known Limitations -->
    <div class="card" id="section-limitations">
        <div class="card-header">
            <h2 style="margin:0;">9. System Limitations &amp; Bounds</h2>
        </div>
        <div class="card-body">
            <ul style="margin:0; padding-left:1.25rem;">
                {limitations_li}
            </ul>
        </div>
    </div>

    <!-- SECTION 10: Integrity & Verification -->
    <div class="card" id="section-integrity">
        <div class="card-header">
            <h2 style="margin:0;">10. Cryptographic Integrity &amp; Reproduction</h2>
        </div>
        <div class="card-body">
            <table class="data-table">
                <tbody>
                    <tr>
                        <td style="width:30%;"><strong>Report Schema &amp; Revision</strong></td>
                        <td><code>{schema_ver}</code> (Revision {rep_rev})</td>
                    </tr>
                    <tr>
                        <td><strong>Analysis Canonical Hash</strong></td>
                        <td><code>{analysis_hash}</code></td>
                    </tr>
                    <tr>
                        <td><strong>Detector Fingerprint</strong></td>
                        <td><code>{cfg_digest}</code></td>
                    </tr>
                    {manifest_info}
                    <tr>
                        <td><strong>Independent Reproduction</strong></td>
                        <td>
                            Run CLI verifier to check cryptographic integrity and reproduce analysis:
                            <br><br>
                            <code>pulsewatch verify &lt;report.json&gt; --input &lt;input.jsonl&gt; --reproduce</code>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    {ablation_section}
    {appendix_windows}

</div>
</body>
</html>
"""
