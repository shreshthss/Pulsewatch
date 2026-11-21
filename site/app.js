/**
 * PulseWatch — Interactive Demonstration & Console Logic
 * 100% vanilla JavaScript with zero external runtime dependencies.
 */

const SCENARIOS = {
  normal: {
    id: "normal",
    title: "Scenario 1: Stationary Normal Traffic",
    subtitle: "Steady baseline arrivals modeled with discrete Poisson point processes",
    badge: "0 False Alarms",
    badgeClass: "badge-success",
    metrics: [
      { label: "Observed Span", val: "720s (12m)" },
      { label: "Valid Records", val: "2,183" },
      { label: "Deduplication", val: "0 removed (100% unique)" },
      { label: "Exactness Status", val: "fully_verified_exact" },
      { label: "Coverage Ratio", val: "100.0%" },
      { label: "Baseline Status", val: "HIGH (rate: 3.05 req/s)" },
      { label: "Contamination Masked", val: "0.0% of time" },
      { label: "Detected Incidents", val: "0 flagged" }
    ],
    highlight: "Zero False Alarms on Stationary Poisson Traffic",
    explanation: "Standard anomaly detectors often struggle with discrete integer variance, flagging natural Poisson fluctuations as bursts. PulseWatch selects an adaptive initial block length (60s) where expected arrivals E >= 20, correctly estimating baseline dispersion and achieving zero false positives over 12 continuous minutes.",
    leads: [
      "Stationary baseline validated: Poisson dispersion phi = 1.00.",
      "Reliability assigned: HIGH (observation span >= 600s, coverage >= 85%, clean unmasked baseline)."
    ],
    hash: "6beb434ce07400dae09279974ea950f17450a0d814c566e76e2361592c858a7d",
    reportPath: "demo/1_normal_traffic_report.html",
    jsonPath: "demo/1_normal_traffic_analysis.json"
  },
  dedup: {
    id: "dedup",
    title: "Scenario 2: Duplicate Proxy Storm",
    subtitle: "Heavy client/proxy retries replaying identical request identities",
    badge: "235 Duplicates Removed",
    badgeClass: "badge-cyan",
    metrics: [
      { label: "Observed Span", val: "240s (4m)" },
      { label: "Ingested Lines", val: "715" },
      { label: "Duplicates Filtered", val: "235 (32.9% replayed)" },
      { label: "Retained Records", val: "480" },
      { label: "Exactness Status", val: "fully_verified_exact" },
      { label: "Coverage Ratio", val: "100.0%" },
      { label: "Baseline Status", val: "MEDIUM (rate: 2.00 req/s)" },
      { label: "Detected Incidents", val: "0 (Apparent spike filtered)" }
    ],
    highlight: "Apparent 50% Rate Spike Dismissed via BLAKE2b Hashing",
    explanation: "Upstream retry storms or proxy re-deliveries create phantom volume spikes in raw server logs. PulseWatch enforces a 4-level identity hierarchy using 16-byte BLAKE2b digests. Exact duplicate records matching normalized event attributes are discarded before detection, preventing costly false alarms.",
    leads: [
      "Filtered 235 replayed logs with verified matching attributes (method, route, caller, status).",
      "Exactness ledger: fully_verified_exact; no degradation occurred."
    ],
    hash: "8521a2903b00168d85bdac1f1e269182ed29b18fa2c914b0d921bb9805f4fcd4",
    reportPath: "demo/2_duplicate_replay_report.html",
    jsonPath: "demo/2_duplicate_replay_analysis.json"
  },
  surge: {
    id: "surge",
    title: "Scenario 3: Genuine 90s API Surge",
    subtitle: "Multi-client load spike on search route with localized 1-second boundaries",
    badge: "1 Incident Flagged",
    badgeClass: "badge-danger",
    metrics: [
      { label: "Observed Span", val: "360s (6m)" },
      { label: "Valid Records", val: "2,790" },
      { label: "Incident Duration", val: "90s (Active bounds)" },
      { label: "Peak 1s Rate", val: "25 req/s (12.5x base)" },
      { label: "Observed Volume", val: "2,250 requests" },
      { label: "Estimated Excess", val: "+2,070 requests" },
      { label: "Maximum Z-Score", val: "125.98" },
      { label: "Concentration Tags", val: "broad_based, route_concentrated" }
    ],
    highlight: "Exact Boundary Localization (IoU = 1.00) & Actionable SRE Leads",
    explanation: "A genuine 90-second surge on /api/v1/items/search was detected across 10s, 60s, and 300s timescales. Rather than suffering from moving-average boundary lag (which degrades standard detectors to 0.50–0.75 IoU), PulseWatch trims candidates to 1-second active excess, achieving exact IoU = 1.00 and generating deterministic investigation leads.",
    leads: [
      "Check distributed load-test orchestration, multi-client test harness, or shared environment deployment.",
      "Investigate endpoint '/api/v1/items/search' capacity, database query efficiency, and upstream dependency health.",
      "Baseline contamination warning: 50.0% of observation time sticky-masked during refitting."
    ],
    hash: "d2ca7c8a9cc8269a86086fefb2f5315a46ade2f06898bd7bd52e98e438071875",
    reportPath: "demo/3_genuine_surge_report.html",
    jsonPath: "demo/3_genuine_surge_analysis.json"
  },
  gap: {
    id: "gap",
    title: "Scenario 4: Collector Outage & Coverage Gap",
    subtitle: "1,000-second telemetry drop resulting in explicit, defensible abstention",
    badge: "Detector Abstained",
    badgeClass: "badge-warning",
    metrics: [
      { label: "Total Wall Span", val: "1,241s (~20.7m)" },
      { label: "Active Observation", val: "240s (4m)" },
      { label: "Unknown Gap", val: "1,000s (>900s threshold)" },
      { label: "Coverage Ratio", val: "19.3%" },
      { label: "Baseline Status", val: "ABSTAIN" },
      { label: "Abstain Reason", val: "Coverage (19.3%) below 40% threshold" },
      { label: "False Positives", val: "0 (Naive baselines fire on resume)" },
      { label: "Detected Incidents", val: "0" }
    ],
    highlight: "Defensible Abstention Prevents Telemetry Resume False Alarms",
    explanation: "When telemetry pipelines crash or restart, naïve moving averages (Trailing Mean, EWMA, MAD) treat the unobserved silence as zero-traffic, firing spurious alerts as soon as normal traffic resumes. PulseWatch tracks sparse 1s bins, flags unknown gaps > 900s, recognizes that coverage (19.3%) is below the defensible threshold, and deliberately abstains.",
    leads: [
      "Coverage ratio 19.3% is below minimum defensible baseline threshold (40.0%).",
      "Detector explicitly abstained from emission to prevent false alarm fatigue."
    ],
    hash: "f6da86ec3290ca89049ed18c97bb4bcedc244eac547c57f8e198bcdcb23fd2a1",
    reportPath: "demo/4_coverage_gap_report.html",
    jsonPath: "demo/4_coverage_gap_analysis.json"
  },
  regime: {
    id: "regime",
    title: "Scenario 5: Ambiguous Regime Level Shift",
    subtitle: "Permanent rate elevation from 2 to 16 req/s triggering asymmetry warning",
    badge: "Level Shift Candidate",
    badgeClass: "badge-amber",
    metrics: [
      { label: "Observed Span", val: "300s (5m)" },
      { label: "Valid Records", val: "2,280" },
      { label: "Post-Shift Duration", val: "120s" },
      { label: "Elevated Rate", val: "16 req/s (8.0x base)" },
      { label: "Asymmetry Ratio", val: "0.15 (Significant skew)" },
      { label: "Incident Shape", val: "level_shift_candidate" },
      { label: "Baseline Status", val: "LOW (regime asymmetry)" },
      { label: "Detected Incidents", val: "1 candidate" }
    ],
    highlight: "Symmetric Split Check Identifies Permanent Service Scaling",
    explanation: "When traffic doubles due to scheduled service scale-up or permanent consumer adoption, standard detectors continuously scream anomaly. PulseWatch evaluates first-half vs. second-half arrival rates (asymmetry ratio 0.15 outside [0.40, 2.50]), flags the incident as `level_shift_candidate`, downgrades baseline reliability to LOW, and prompts the engineer to verify scaling.",
    leads: [
      "Significant regime asymmetry detected (first/second half clean ratio: 0.15).",
      "Confirm whether an intentional service scaling step occurred or collect extended steady-state observations."
    ],
    hash: "0fc75199253414a29df0aabf985f4cd7c55eeed807a770bf0ae3429a25c3e0fa",
    reportPath: "demo/5_ambiguous_regime_report.html",
    jsonPath: "demo/5_ambiguous_regime_analysis.json"
  }
};

function selectScenario(scenarioKey) {
  const data = SCENARIOS[scenarioKey];
  if (!data) return;

  // Update tabs
  document.querySelectorAll(".scenario-tab").forEach(tab => {
    const isSelected = tab.dataset.scenario === scenarioKey;
    tab.setAttribute("aria-selected", isSelected ? "true" : "false");
    tab.classList.toggle("active", isSelected);
  });

  // Update header & badges
  document.getElementById("sc-title").textContent = data.title;
  document.getElementById("sc-subtitle").textContent = data.subtitle;
  const badgeEl = document.getElementById("sc-badge");
  badgeEl.textContent = data.badge;
  badgeEl.className = "badge " + data.badgeClass;

  // Update metrics grid
  const metricsContainer = document.getElementById("sc-metrics");
  metricsContainer.innerHTML = "";
  data.metrics.forEach(m => {
    const card = document.createElement("div");
    card.className = "metric-box";
    card.innerHTML = `
      <div class="metric-label">${escapeHtml(m.label)}</div>
      <div class="metric-value">${escapeHtml(m.val)}</div>
    `;
    metricsContainer.appendChild(card);
  });

  // Update explanation & highlight
  document.getElementById("sc-highlight").textContent = data.highlight;
  document.getElementById("sc-explanation").textContent = data.explanation;

  // Update leads
  const leadsContainer = document.getElementById("sc-leads");
  leadsContainer.innerHTML = "";
  data.leads.forEach(lead => {
    const li = document.createElement("li");
    li.textContent = lead;
    leadsContainer.appendChild(li);
  });

  // Update hash & links
  document.getElementById("sc-hash").textContent = data.hash;
  const reportBtn = document.getElementById("sc-report-link");
  reportBtn.href = data.reportPath;
  const jsonBtn = document.getElementById("sc-json-link");
  jsonBtn.href = data.jsonPath;
}

function copyCode(btn, codeId) {
  const el = document.getElementById(codeId);
  if (!el) return;
  const text = el.innerText || el.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.innerHTML;
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
    btn.classList.add("copied");
    setTimeout(() => {
      btn.innerHTML = orig;
      btn.classList.remove("copied");
    }, 2000);
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  // Initialize default scenario
  selectScenario("surge");

  // Tab click listeners
  document.querySelectorAll(".scenario-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      selectScenario(tab.dataset.scenario);
    });
    tab.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectScenario(tab.dataset.scenario);
      }
    });
  });
});
