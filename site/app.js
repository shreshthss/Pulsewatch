/**
 * PulseWatch — Data-Driven Interactive Demonstration, Benchmark & Ablation Explorer
 * Pure vanilla JavaScript, zero external runtime dependencies.
 * Loads pre-generated SHA-256 verified artifacts from ./data/ and ./demo/.
 */

"use strict";

const STATE = {
  scenariosData: null,
  benchmarkData: null,
  ablationsData: null,
  activeScenarioKey: "surge",
  activeBenchmarkCategory: "all",
  activeAblationIndex: 0,
};

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmtNum(val) {
  if (val === null || val === undefined) return "N/A";
  return Number(val).toLocaleString("en-US");
}

function fmtFloat(val, digits = 4) {
  if (val === null || val === undefined) return "N/A";
  return Number(val).toFixed(digits);
}

/* -------------------------------------------------------------------------- */
/* SVG Traffic Visualization                                                  */
/* -------------------------------------------------------------------------- */

function renderTraceSvg(sc) {
  const tl = sc.timeline;
  const bins = (tl && tl.bins) ? tl.bins : [];
  if (bins.length === 0) {
    return `<div style="padding:2rem;text-align:center;color:var(--text-dim);">No time-series bins available.</div>`;
  }

  const W = 900;
  const H = 250;
  const padL = 52;
  const padR = 20;
  const padT = 22;
  const padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const span = Math.max(1, tl.span_sec);
  const binW = tl.bin_size_sec || 10;
  const baseRate = sc.metrics.baseline_rate_1s || 0;

  let maxRps = Math.max(5, baseRate * 1.5);
  bins.forEach(b => {
    if (b.raw_rps > maxRps) maxRps = b.raw_rps;
    if (b.dedup_rps > maxRps) maxRps = b.dedup_rps;
  });
  maxRps = Math.ceil(maxRps * 1.15);

  const xOf = (t) => padL + (Math.min(span, Math.max(0, t)) / span) * plotW;
  const yOf = (r) => padT + plotH - (Math.min(maxRps, Math.max(0, r)) / maxRps) * plotH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Traffic rate over time">`;

  // Horizontal grid lines
  for (let i = 0; i <= 4; i++) {
    const val = (maxRps / 4) * i;
    const y = yOf(val);
    svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#1e293b" stroke-width="1"/>`;
    svg += `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" fill="#64748b" font-size="11" text-anchor="end" font-family="monospace">${val.toFixed(0)}</text>`;
  }

  // Coverage gaps shading (>900s)
  (tl.gap_intervals_rel || []).forEach(([gs, ge]) => {
    const gx = xOf(gs);
    const gw = Math.max(2, xOf(ge) - gx);
    svg += `<rect x="${gx.toFixed(1)}" y="${padT}" width="${gw.toFixed(1)}" height="${plotH}" fill="rgba(245, 158, 11, 0.14)" stroke="rgba(245, 158, 11, 0.5)" stroke-dasharray="4 3"/>`;
    svg += `<text x="${(gx + gw / 2).toFixed(1)}" y="${padT + 18}" fill="#fbbf24" font-size="11" text-anchor="middle" font-weight="600">UNKNOWN GAP (${ge - gs}s)</text>`;
  });

  // Ground-truth intervals (dashed outline)
  (sc.ground_truth_intervals_rel || []).forEach(([gs, ge]) => {
    const gx = xOf(gs);
    const gw = Math.max(3, xOf(ge) - gx);
    svg += `<rect x="${gx.toFixed(1)}" y="${padT}" width="${gw.toFixed(1)}" height="${plotH}" fill="none" stroke="#34d399" stroke-width="1.5" stroke-dasharray="5 3"/>`;
  });

  // Detected incident intervals (rose tint)
  (tl.incident_intervals_rel || []).forEach(([is, ie]) => {
    const ix = xOf(is);
    const iw = Math.max(3, xOf(ie) - ix);
    svg += `<rect x="${ix.toFixed(1)}" y="${padT}" width="${iw.toFixed(1)}" height="${plotH}" fill="rgba(244, 63, 94, 0.16)" stroke="rgba(251, 113, 133, 0.7)" stroke-width="1"/>`;
    svg += `<text x="${(ix + iw / 2).toFixed(1)}" y="${padT + 15}" fill="#fb7185" font-size="11" text-anchor="middle" font-weight="600">DETECTED</text>`;
  });

  // Baseline rate line
  if (baseRate > 0) {
    const by = yOf(baseRate);
    svg += `<line x1="${padL}" y1="${by.toFixed(1)}" x2="${W - padR}" y2="${by.toFixed(1)}" stroke="#34d399" stroke-width="1.5" stroke-dasharray="6 4"/>`;
  }

  // Raw rate polyline (shown when duplicates exist)
  const hasDups = (sc.metrics.duplicates_removed || 0) > 0;
  if (hasDups) {
    const rawPts = bins.map(b => `${xOf(b.t_rel + binW * 0.5).toFixed(1)},${yOf(b.raw_rps).toFixed(1)}`).join(" ");
    svg += `<polyline fill="none" stroke="#fbbf24" stroke-width="1.8" stroke-dasharray="4 3" points="${rawPts}"/>`;
  }

  // Deduplicated rate polyline
  const dedupPts = bins.map(b => `${xOf(b.t_rel + binW * 0.5).toFixed(1)},${yOf(b.dedup_rps).toFixed(1)}`).join(" ");
  svg += `<polyline fill="none" stroke="#38bdf8" stroke-width="2.2" points="${dedupPts}"/>`;

  // X-axis labels
  for (let i = 0; i <= 4; i++) {
    const t = Math.round((span / 4) * i);
    const x = xOf(t);
    svg += `<text x="${x.toFixed(1)}" y="${H - 10}" fill="#64748b" font-size="11" text-anchor="middle" font-family="monospace">${t}s</text>`;
  }

  svg += `<text x="14" y="${padT + plotH / 2}" fill="#64748b" font-size="11" text-anchor="middle" transform="rotate(-90 14 ${padT + plotH / 2})">req/s</text>`;
  svg += `</svg>`;
  return svg;
}

function renderWorldCupSvg(sc) {
  const samples = sc.samples || [];
  const W = 900;
  const H = 220;
  const padL = 70;
  const padR = 30;
  const padT = 25;
  const padB = 38;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxRec = Math.max(...samples.map(s => s.records), 1);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="WorldCup98 sample volumes">`;
  const slotW = plotW / Math.max(1, samples.length);
  samples.forEach((s, idx) => {
    const barW = Math.min(120, slotW * 0.5);
    const barH = (s.records / maxRec) * plotH;
    const x = padL + idx * slotW + (slotW - barW) / 2;
    const y = padT + plotH - barH;
    svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="4" fill="rgba(56, 189, 248, 0.25)" stroke="#38bdf8" stroke-width="1.5"/>`;
    svg += `<text x="${(x + barW / 2).toFixed(1)}" y="${(y - 6).toFixed(1)}" fill="#f8fafc" font-size="12" text-anchor="middle" font-family="monospace">${fmtNum(s.records)} reqs</text>`;
    svg += `<text x="${(x + barW / 2).toFixed(1)}" y="${H - 14}" fill="#94a3b8" font-size="11" text-anchor="middle">${escapeHtml(s.sample_id.toUpperCase())} (${s.baseline_rate_1s} req/s)</text>`;
  });
  svg += `</svg>`;
  return svg;
}

function renderAblationSummarySvg() {
  const exps = (STATE.ablationsData && STATE.ablationsData.experiments) || [];
  const W = 900;
  const H = 200;
  const padL = 60;
  const padR = 30;
  const padT = 24;
  const padB = 36;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Ablation false positive impact">`;
  const slotW = plotW / Math.max(1, exps.length);
  exps.forEach((exp, idx) => {
    const fpDiff = exp.scenarios.reduce((acc, s) => acc + Math.max(0, (s.ablated_fp || 0) - (s.control_fp || 0)), 0);
    const maxH = Math.max(1, plotH * (Math.min(10, Math.max(1, fpDiff)) / 10));
    const barW = Math.min(80, slotW * 0.45);
    const x = padL + idx * slotW + (slotW - barW) / 2;
    const y = padT + plotH - maxH;
    const color = fpDiff > 0 ? "#fb7185" : "#34d399";
    svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${maxH.toFixed(1)}" rx="4" fill="rgba(56, 189, 248, 0.14)" stroke="${color}" stroke-width="1.5"/>`;
    svg += `<text x="${(x + barW / 2).toFixed(1)}" y="${(y - 6).toFixed(1)}" fill="${color}" font-size="11" text-anchor="middle" font-family="monospace">+${fpDiff} FP</text>`;
    svg += `<text x="${(x + barW / 2).toFixed(1)}" y="${H - 12}" fill="#94a3b8" font-size="11" text-anchor="middle">${escapeHtml(exp.experiment_id.split("_")[0])}</text>`;
  });
  svg += `</svg>`;
  return svg;
}

/* -------------------------------------------------------------------------- */
/* Scenario Explorer                                                          */
/* -------------------------------------------------------------------------- */

function buildEvidenceAccordion(sc) {
  const panels = [];
  if (sc.kind === "trace") {
    const ev = sc.evidence || {};
    const m = sc.metrics || {};

    // Panel 1: Incident Overview & Leads
    const leadsHtml = (ev.investigation_leads || []).length > 0
      ? `<ul style="padding-left:1.2rem;">${ev.investigation_leads.map(l => `<li><strong>${escapeHtml(l.trigger)}:</strong> ${escapeHtml(l.suggested_action)}</li>`).join("")}</ul>`
      : `<p>No anomaly incidents triggered in this scenario.</p>`;

    const incInfo = (sc.incidents || []).length > 0
      ? sc.incidents.map(inc => `
          <div style="margin-bottom:0.65rem;">
            <strong>${escapeHtml(inc.incident_id)}</strong> · Shape: <code>${escapeHtml(inc.descriptive_pattern.shape)}</code> ·
            Max Z: <code>${inc.statistical_findings.max_z_score}</code> ·
            Scales: <code>[${(inc.statistical_findings.scales_fired || []).join(", ")}]</code> ·
            Excess: <code>+${fmtNum(inc.observed_facts.estimated_excess_requests)} reqs</code>
          </div>`).join("")
      : "";

    panels.push(`
      <details class="evidence-panel" open>
        <summary>Incident Overview &amp; Deterministic Investigation Leads</summary>
        <div class="evidence-content">
          ${incInfo}
          ${leadsHtml}
        </div>
      </details>
    `);

    // Panel 2: Contributor Evidence (Routes, Clients, Status Codes, Tags)
    const routesEntries = Object.entries(ev.leading_routes || {}).slice(0, 5);
    const clientsEntries = Object.entries(ev.top_clients || {}).slice(0, 5);
    const statusEntries = Object.entries(ev.status_distribution || {});

    panels.push(`
      <details class="evidence-panel">
        <summary>Contributor Distribution, Leading Routes &amp; Status Codes</summary>
        <div class="evidence-content">
          <div class="evidence-grid">
            <div class="evidence-subcard">
              <h5>Leading Routes</h5>
              ${routesEntries.length ? `<ul class="kv-list">${routesEntries.map(([k, v]) => `<li><span>${escapeHtml(k)}</span><span>${fmtNum(v)}</span></li>`).join("")}</ul>` : `<p>N/A (no incident)</p>`}
            </div>
            <div class="evidence-subcard">
              <h5>Top Callers</h5>
              ${clientsEntries.length ? `<ul class="kv-list">${clientsEntries.map(([k, v]) => `<li><span>${escapeHtml(k)}</span><span>${fmtNum(v)}</span></li>`).join("")}</ul>` : `<p>N/A (no incident)</p>`}
            </div>
            <div class="evidence-subcard">
              <h5>Status Distribution &amp; Tags</h5>
              ${statusEntries.length ? `<ul class="kv-list">${statusEntries.map(([k, v]) => `<li><span>HTTP ${escapeHtml(k)}</span><span>${fmtNum(v)}</span></li>`).join("")}</ul>` : `<p>All nominal 200 OK</p>`}
              <div class="tag-row">${(ev.concentration_tags || []).map(t => `<span class="tag-chip">${escapeHtml(t)}</span>`).join("")}</div>
            </div>
          </div>
        </div>
      </details>
    `);

    // Panel 3: Input Integrity, Notices & Schema Metadata
    panels.push(`
      <details class="evidence-panel">
        <summary>Input Integrity, Exactness Status &amp; Data Notices</summary>
        <div class="evidence-content">
          <ul class="kv-list" style="margin-bottom:0.75rem;">
            <li><span>Schema Version &amp; Revision</span><span>${escapeHtml(sc.schema_version)} (rev ${sc.report_revision})</span></li>
            <li><span>Source SHA-256</span><span>${escapeHtml((sc.source_sha256 || "").slice(0, 24))}...</span></li>
            <li><span>Exactness Status</span><span>${escapeHtml(m.exactness_status)}</span></li>
            <li><span>Second-Pass Evidence Replay</span><span>${escapeHtml(m.second_pass_status)}</span></li>
          </ul>
          ${(ev.data_notices || []).length ? `<div><strong>Notices:</strong><ul style="padding-left:1.2rem;margin-top:0.25rem;">${ev.data_notices.map(n => `<li>${escapeHtml(n)}</li>`).join("")}</ul></div>` : ""}
        </div>
      </details>
    `);
  } else if (sc.kind === "worldcup98") {
    panels.push(`
      <details class="evidence-panel" open>
        <summary>Authentic WorldCup98 Trace Samples (Days 20, 46, 78)</summary>
        <div class="evidence-content">
          <div class="benchmark-table-wrapper">
            <table class="benchmark-table">
              <thead><tr><th>Sample</th><th>Date (Paris)</th><th>Records</th><th>Coverage</th><th>Baseline Rate</th><th>Reliability</th><th>False Alarms</th></tr></thead>
              <tbody>
                ${sc.samples.map(s => `
                  <tr>
                    <td><code>${escapeHtml(s.sample_id)}</code></td>
                    <td>${escapeHtml(s.label)}</td>
                    <td>${fmtNum(s.records)}</td>
                    <td>${s.coverage_pct}%</td>
                    <td>${s.baseline_rate_1s} req/s</td>
                    <td class="stat-highlight">${escapeHtml(s.baseline_reliability)}</td>
                    <td class="stat-highlight">${s.incidents_detected}</td>
                  </tr>`).join("")}
              </tbody>
            </table>
          </div>
          <p style="margin-top:0.75rem;">Note: These 3 intervals represent authentic historical traffic with zero false alarms. Controlled perturbation injection tests are evaluated separately.</p>
        </div>
      </details>
    `);
  } else if (sc.kind === "ablations") {
    const exps = (STATE.ablationsData && STATE.ablationsData.experiments) || [];
    panels.push(`
      <details class="evidence-panel" open>
        <summary>Five Controlled Ablation Studies Overview</summary>
        <div class="evidence-content">
          <ul style="padding-left:1.2rem;">
            ${exps.map(e => `<li><strong>${escapeHtml(e.experiment_id)} (${escapeHtml(e.component_name)}):</strong> ${escapeHtml(e.description)}</li>`).join("")}
          </ul>
        </div>
      </details>
    `);
  }
  return panels.join("");
}

function selectScenario(key) {
  if (!STATE.scenariosData || !STATE.scenariosData.scenarios[key]) return;
  STATE.activeScenarioKey = key;
  const sc = STATE.scenariosData.scenarios[key];

  // Update tabs
  document.querySelectorAll("#scenario-tabs .scenario-tab").forEach(tab => {
    const active = tab.dataset.scenario === key;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });

  // Header & badge
  document.getElementById("sc-title").textContent = sc.title;
  document.getElementById("sc-subtitle").textContent = sc.subtitle;
  const badge = document.getElementById("sc-badge");
  badge.textContent = sc.badge;
  badge.className = "badge " + (sc.badge_class || "badge-cyan");

  // Chart & legend
  const chartContainer = document.getElementById("sc-chart-container");
  const legendEl = document.getElementById("sc-chart-legend");
  const chartTitle = document.getElementById("sc-chart-title");

  if (sc.kind === "trace") {
    chartTitle.textContent = "Traffic Rate Timeline (req/s)";
    legendEl.innerHTML = `
      <span class="legend-item"><span class="legend-swatch" style="background:#38bdf8;"></span>Deduplicated Rate</span>
      ${(sc.metrics.duplicates_removed || 0) > 0 ? `<span class="legend-item"><span class="legend-swatch" style="background:#fbbf24;"></span>Raw Input Rate</span>` : ""}
      <span class="legend-item"><span class="legend-swatch" style="background:#34d399;"></span>Baseline Rate</span>
      <span class="legend-item"><span class="legend-box" style="background:rgba(244,63,94,0.35);border:1px solid #fb7185;"></span>Detected Incident</span>
    `;
    chartContainer.innerHTML = renderTraceSvg(sc);
  } else if (sc.kind === "worldcup98") {
    chartTitle.textContent = "WorldCup98 Authentic 15-Min Sample Volumes";
    legendEl.innerHTML = `<span class="legend-item"><span class="legend-box" style="background:rgba(56,189,248,0.3);border:1px solid #38bdf8;"></span>Exported Requests</span>`;
    chartContainer.innerHTML = renderWorldCupSvg(sc);
  } else {
    chartTitle.textContent = "Additional False Positives Caused by Disabling Each Component";
    legendEl.innerHTML = `<span class="legend-item"><span class="legend-box" style="background:rgba(251,113,133,0.3);border:1px solid #fb7185;"></span>Ablated False-Alarm Spike</span>`;
    chartContainer.innerHTML = renderAblationSummarySvg();
  }

  // Numerical results grid
  const m = sc.metrics || {};
  let metricCards = [];
  if (sc.kind === "trace") {
    metricCards = [
      { label: "Input Records", val: fmtNum(m.input_records) },
      { label: "Retained Records", val: fmtNum(m.retained_records) },
      { label: "Duplicates Removed", val: fmtNum(m.duplicates_removed) },
      { label: "Coverage Ratio", val: `${m.coverage_pct}%` },
      { label: "Baseline Reliability", val: m.baseline_reliability },
      { label: "Baseline Rate", val: `${m.baseline_rate_1s} req/s` },
      { label: "Incidents Detected", val: String(m.incident_count) },
      { label: "False Alarms", val: String(m.false_alarms) },
    ];
  } else if (sc.kind === "worldcup98") {
    metricCards = [
      { label: "Samples Evaluated", val: String(m.samples_evaluated) },
      { label: "Total Requests", val: fmtNum(m.total_records) },
      { label: "Peak Window Volume", val: fmtNum(m.max_records_window) },
      { label: "Peak Rate", val: `${m.max_rate_1s} req/s` },
      { label: "False Alarms", val: String(m.false_alarms) },
      { label: "Dataset Origin", val: "1998 World Cup" },
    ];
  } else {
    metricCards = [
      { label: "Ablation Studies", val: String(sc.experiments_count || 5) },
      { label: "Schema", val: "pulsewatch.ablation/1" },
      { label: "Seed Partition", val: "51001..51005" },
      { label: "Status", val: "100% Reproducible" },
    ];
  }

  document.getElementById("sc-metrics").innerHTML = metricCards.map(c => `
    <div class="metric-box">
      <div class="metric-label">${escapeHtml(c.label)}</div>
      <div class="metric-value">${escapeHtml(c.val)}</div>
    </div>
  `).join("");

  // Insight & Evidence
  document.getElementById("sc-highlight").textContent = sc.highlight;
  document.getElementById("sc-explanation").textContent = sc.explanation;
  document.getElementById("sc-evidence-accordion").innerHTML = buildEvidenceAccordion(sc);

  // Hash & Actions
  document.getElementById("sc-hash").textContent = sc.analysis_hash || sc.key + " (aggregate artifact)";
  const reportLink = document.getElementById("sc-report-link");
  if (sc.report_path) {
    reportLink.href = sc.report_path;
    reportLink.hidden = false;
  } else {
    reportLink.hidden = true;
  }

  const dlLink = document.getElementById("sc-json-download");
  dlLink.href = sc.json_path || "data/scenarios.json";
  dlLink.setAttribute("download", (sc.json_path || "scenarios.json").split("/").pop());

  // Close JSON drawer on scenario switch
  document.getElementById("sc-json-drawer").hidden = true;
}

async function toggleRawJson() {
  const drawer = document.getElementById("sc-json-drawer");
  if (!drawer.hidden) {
    drawer.hidden = true;
    return;
  }
  const sc = STATE.scenariosData.scenarios[STATE.activeScenarioKey];
  const path = sc.json_path || "data/scenarios.json";
  document.getElementById("sc-json-filename").textContent = path;
  const pre = document.getElementById("sc-json-content");
  pre.textContent = "Loading " + path + "...";
  drawer.hidden = false;

  try {
    const resp = await fetch(path);
    const text = await resp.text();
    pre.textContent = text.slice(0, 18000) + (text.length > 18000 ? "\n... (truncated preview; use Download JSON for full file)" : "");
  } catch (err) {
    pre.textContent = "Failed to load JSON artifact: " + err.message;
  }
}

/* -------------------------------------------------------------------------- */
/* Benchmark v2 Explorer                                                      */
/* -------------------------------------------------------------------------- */

function renderBenchmarkSection() {
  const bm = STATE.benchmarkData;
  if (!bm) return;

  const s = bm.overall_summary;
  const kpis = [
    { label: "Total Scenarios", val: `${bm.scenarios_count}`, sub: `${bm.categories_count} categories` },
    { label: "Pooled F1 Score", val: fmtFloat(s.pooled_f1_score, 4), sub: `TP=${s.pooled_true_positives}, FP=${s.pooled_false_positives}, FN=${s.pooled_false_negatives}` },
    { label: "Macro-Mean F1", val: fmtFloat(s.macro_mean_f1, 4), sub: "Positive & borderline classes" },
    { label: "Pooled Precision", val: fmtFloat(s.pooled_precision, 4), sub: "Across all seeds" },
    { label: "Pooled Recall", val: fmtFloat(s.pooled_recall, 4), sub: "Across all seeds" },
  ];

  document.getElementById("bm-kpis").innerHTML = kpis.map(k => `
    <div class="kpi-card">
      <div class="kpi-label">${escapeHtml(k.label)}</div>
      <div class="kpi-val">${escapeHtml(k.val)}</div>
      <div class="kpi-sub">${escapeHtml(k.sub)}</div>
    </div>
  `).join("");

  document.getElementById("bm-fingerprint").textContent =
    `Scenario Fingerprint: ${bm.scenario_fingerprint} · Seeds: [${bm.seeds.join(", ")}]`;

  // Category filter buttons
  const filterBar = document.getElementById("bm-filters");
  filterBar.innerHTML = `<button type="button" class="filter-btn active" data-cat="all">All (${bm.scenarios_count})</button>` +
    bm.categories.map(c => `<button type="button" class="filter-btn" data-cat="${escapeHtml(c.category)}">${escapeHtml(c.category)} (${c.count})</button>`).join("");

  filterBar.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      STATE.activeBenchmarkCategory = btn.dataset.cat;
      filterBar.querySelectorAll(".filter-btn").forEach(b => b.classList.toggle("active", b === btn));
      renderBenchmarkTable();
    });
  });

  renderBenchmarkTable();
}

function renderBenchmarkTable() {
  const bm = STATE.benchmarkData;
  const cat = STATE.activeBenchmarkCategory;
  const rows = bm.scenarios.filter(s => cat === "all" || s.category === cat);

  document.getElementById("bm-table-body").innerHTML = rows.map(r => {
    const f1Class = r.f1_score === null ? "" : (r.f1_score >= 0.8 ? "stat-highlight" : (r.f1_score === 0 ? "stat-fail" : "stat-lag"));
    return `
      <tr>
        <td><code>${escapeHtml(r.scenario_id)}</code></td>
        <td>${escapeHtml(r.category)}</td>
        <td>${r.tp}</td>
        <td class="${!r.has_positive_class && r.fp > 0 ? "stat-fail" : ""}">${r.fp}</td>
        <td>${r.fn}</td>
        <td>${fmtFloat(r.precision, 4)}</td>
        <td>${fmtFloat(r.recall, 4)}</td>
        <td class="${f1Class}">${fmtFloat(r.f1_score, 4)}</td>
        <td>${fmtFloat(r.mean_iou, 4)}</td>
      </tr>
    `;
  }).join("");
}

/* -------------------------------------------------------------------------- */
/* Ablation Explorer                                                          */
/* -------------------------------------------------------------------------- */

function renderAblationTabs() {
  const exps = (STATE.ablationsData && STATE.ablationsData.experiments) || [];
  const tabsEl = document.getElementById("ab-tabs");
  tabsEl.innerHTML = exps.map((e, idx) => `
    <button type="button" class="filter-btn ${idx === 0 ? "active" : ""}" data-ab-idx="${idx}" role="tab" aria-selected="${idx === 0}">
      ${escapeHtml(e.experiment_id.split("_")[0])}: ${escapeHtml(e.component_name)}
    </button>
  `).join("");

  tabsEl.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => selectAblation(Number(btn.dataset.abIdx)));
  });

  if (exps.length > 0) selectAblation(0);
}

function selectAblation(idx) {
  const exps = STATE.ablationsData.experiments;
  const exp = exps[idx];
  if (!exp) return;
  STATE.activeAblationIndex = idx;

  document.querySelectorAll("#ab-tabs .filter-btn").forEach((b, i) => {
    b.classList.toggle("active", i === idx);
    b.setAttribute("aria-selected", i === idx ? "true" : "false");
  });

  document.getElementById("ab-title").textContent = `${exp.experiment_id}: ${exp.component_name}`;
  document.getElementById("ab-desc").textContent = exp.description;
  document.getElementById("ab-id-badge").textContent = exp.experiment_id.split("_")[0];

  document.getElementById("ab-table-body").innerHTML = exp.scenarios.map(s => {
    const deltaFp = s.delta_fp || 0;
    const fpClass = deltaFp > 0 ? "stat-fail" : "stat-highlight";
    return `
      <tr>
        <td><code>${escapeHtml(s.scenario_id)}</code></td>
        <td class="stat-highlight">${fmtFloat(s.control_f1, 4)} (${s.control_tp}/${s.control_fp}/${s.control_fn})</td>
        <td>${fmtFloat(s.control_mean_iou, 4)}</td>
        <td class="${s.ablated_f1 === 0 && s.control_f1 > 0 ? "stat-fail" : ""}">${fmtFloat(s.ablated_f1, 4)} (${s.ablated_tp}/${s.ablated_fp}/${s.ablated_fn})</td>
        <td>${fmtFloat(s.ablated_mean_iou, 4)}</td>
        <td class="${fpClass}">${deltaFp >= 0 ? "+" : ""}${deltaFp} FP</td>
      </tr>
    `;
  }).join("");
}

/* -------------------------------------------------------------------------- */
/* Clipboard & Initialization                                                 */
/* -------------------------------------------------------------------------- */

function copyCode(btn, codeId) {
  const el = document.getElementById(codeId);
  if (!el) return;
  const text = el.innerText || el.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    setTimeout(() => {
      btn.textContent = orig;
      btn.classList.remove("copied");
    }, 1800);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const [scResp, bmResp, abResp] = await Promise.all([
      fetch("data/scenarios.json"),
      fetch("data/benchmark_v2.json"),
      fetch("data/ablations.json"),
    ]);
    STATE.scenariosData = await scResp.json();
    STATE.benchmarkData = await bmResp.json();
    STATE.ablationsData = await abResp.json();

    renderBenchmarkSection();
    renderAblationTabs();
    selectScenario("surge");
  } catch (err) {
    document.getElementById("sc-title").textContent = "Error loading site/data/*.json";
    document.getElementById("sc-subtitle").textContent = err.message;
  }

  document.querySelectorAll("#scenario-tabs .scenario-tab").forEach(tab => {
    tab.addEventListener("click", () => selectScenario(tab.dataset.scenario));
  });

  document.getElementById("sc-json-toggle").addEventListener("click", toggleRawJson);
  document.getElementById("sc-json-close").addEventListener("click", () => {
    document.getElementById("sc-json-drawer").hidden = true;
  });
});
