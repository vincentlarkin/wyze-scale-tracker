"use strict";

// ------------------------------------------------------------------ metric catalog
// better: "down" | "up" | null  -> how a change is colored (null = neutral ink)
const METRICS = {
  weight_lb: { label: "Weight", unit: "lb", digits: 1, better: null },
  weight_kg: { label: "Weight (kg)", unit: "kg", digits: 1, better: null },
  body_fat: { label: "Body fat", unit: "%", digits: 1, better: "down" },
  muscle: { label: "Muscle", unit: "lb", digits: 1, better: "up" },
  skeletal_muscle: { label: "Skeletal muscle", unit: "lb", digits: 1, better: "up" },
  muscle_ratio: { label: "Muscle / weight", unit: "%", digits: 1, better: "up", derived: true },
  body_water: { label: "Body water", unit: "%", digits: 1, better: null },
  protein: { label: "Protein", unit: "%", digits: 1, better: null },
  bone_mineral: { label: "Bone mineral", unit: "lb", digits: 1, better: null },
  subcutaneous_fat: { label: "Subcutaneous fat", unit: "%", digits: 1, better: "down" },
  body_vfr: { label: "Visceral fat", unit: "", digits: 0, better: "down" },
  bmi: { label: "BMI", unit: "", digits: 1, better: null },
  bmr: { label: "BMR", unit: "kcal", digits: 0, better: null },
  metabolic_age: { label: "Metabolic age", unit: "yrs", digits: 0, better: "down" },
  body_score: { label: "Body score", unit: "", digits: 0, better: "up" },
};
const CHART_METRICS = Object.keys(METRICS).filter((k) => !METRICS[k].derived);
const TILE_METRICS = ["body_fat", "muscle", "bmi"];
const STAT_METRICS = ["weight_lb", "body_fat", "muscle", "muscle_ratio", "bmi", "body_water"];
const TABLE_DEFAULT = ["weight_lb", "body_fat", "muscle", "body_water", "bmi"];
const DELTA_WINDOW_DAYS = 30;
const GAP_BREAK_DAYS = 14;
const AUTO_SYNC_AFTER_SECONDS = 15 * 60;
const DAY = 86400;

const $ = (id) => document.getElementById(id);
const refs = {
  lastReading: $("lastReading"),
  profilePills: $("profilePills"),
  connectionChip: $("connectionChip"),
  connectionText: $("connectionText"),
  syncNow: $("syncNow"),
  menuBtn: $("menuBtn"),
  menu: $("menu"),
  importFile: $("importFile"),
  statusBar: $("statusBar"),
  heroWeight: $("heroWeight"),
  heroDelta: $("heroDelta"),
  heroSpark: $("heroSpark"),
  tiles: $("tiles"),
  chartTitle: $("chartTitle"),
  metricSelect: $("metricSelect"),
  rangePills: $("rangePills"),
  chartCanvas: $("trendChart"),
  chartEmpty: $("chartEmpty"),
  statsPeriod: $("statsPeriod"),
  statsGrid: $("statsGrid"),
  matchedInsights: $("matchedInsights"),
  rowCount: $("rowCount"),
  columnPicker: $("columnPicker"),
  tableHead: $("recordsHead"),
  tableBody: document.querySelector("#recordsTable tbody"),
  exportDialog: $("exportDialog"),
  exportForm: $("exportForm"),
  exportProfile: $("exportProfile"),
  exportRange: $("exportRange"),
  exportCustom: $("exportCustom"),
  exportStart: $("exportStart"),
  exportEnd: $("exportEnd"),
  exportError: $("exportError"),
  connectDialog: $("connectDialog"),
  connectForm: $("connectForm"),
  connectClose: $("connectClose"),
  connectStepCreds: $("connectStepCreds"),
  connectStepCode: $("connectStepCode"),
  connectError: $("connectError"),
  connectSubmit: $("connectSubmit"),
  disconnectBtn: $("disconnectBtn"),
  consoleLink: $("consoleLink"),
  codePrompt: $("codePrompt"),
  secretNote: $("secretNote"),
  cEmail: $("cEmail"),
  cPassword: $("cPassword"),
  cKeyId: $("cKeyId"),
  cApiKey: $("cApiKey"),
  cRemember: $("cRemember"),
  cCode: $("cCode"),
  wyzeSignInBtn: $("wyzeSignInBtn"),
  manualDivider: $("manualDivider"),
};

const state = {
  profiles: [],
  profile: null,
  rows: [], // all readings for the selected profile, oldest first
  days: 365,
  metric: "weight_lb",
  columns: new Set(TABLE_DEFAULT),
};
let chart = null;
let authState = null;
let pendingChallenge = null;
let syncing = false;

// ------------------------------------------------------------------ small utils
const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode etc. */
    }
  },
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function fmt(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtUnit(value, key) {
  const m = METRICS[key];
  const base = fmt(value, m.digits);
  if (base === "—" || !m.unit) return base;
  return m.unit === "%" ? `${base}%` : `${base} ${m.unit}`;
}

function fmtDate(ts, opts = { month: "short", day: "numeric" }) {
  return ts ? new Date(ts * 1000).toLocaleDateString(undefined, opts) : "—";
}

function fmtDateTime(ts) {
  return ts
    ? new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
}

function timeAgo(ts) {
  if (!ts) return "never";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < DAY) return `${Math.floor(secs / 3600)} h ago`;
  return `${Math.floor(secs / DAY)} d ago`;
}

function setStatus(message, type = "info") {
  if (!message) {
    refs.statusBar.classList.add("hidden");
    return;
  }
  refs.statusBar.className = `status ${type}`;
  refs.statusBar.textContent = message;
}

async function postJson(url, body = {}) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await res.json().catch(() => ({}));
  return { res, payload };
}

// ------------------------------------------------------------------ metric math
const ZERO_IS_MISSING = new Set(Object.keys(METRICS).filter((k) => !k.startsWith("weight")));

function value(row, key) {
  if (key === "muscle_ratio") {
    const muscle = value(row, "muscle");
    const weight = Number(row.weight_lb);
    return muscle === null || !(weight > 0) ? null : (muscle / weight) * 100;
  }
  const v = row[key];
  if (v === null || v === undefined) return null;
  const n = Number(v);
  if (!Number.isFinite(n) || (ZERO_IS_MISSING.has(key) && n <= 0)) return null;
  return n;
}

function series(rows, key) {
  return rows.map((r) => ({ ts: r.measure_ts, v: value(r, key) })).filter((p) => p.v !== null);
}

function rowsInRange(days) {
  if (!days) return state.rows;
  const cutoff = Date.now() / 1000 - days * DAY;
  return state.rows.filter((r) => r.measure_ts >= cutoff);
}

/** Latest value vs the last reading at least DELTA_WINDOW_DAYS earlier (or the earliest one). */
function delta(key) {
  const pts = series(state.rows, key);
  if (pts.length < 2) return null;
  const last = pts[pts.length - 1];
  const target = last.ts - DELTA_WINDOW_DAYS * DAY;
  let base = null;
  for (const p of pts) if (p.ts <= target) base = p;
  base = base || pts[0];
  if (base === last) return null;
  return { change: last.v - base.v, since: base.ts };
}

function trailingAverage(pts, windowDays = 7) {
  const out = [];
  let start = 0;
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    sum += pts[i].v;
    while (pts[i].ts - pts[start].ts > windowDays * DAY) sum -= pts[start++].v;
    out.push(sum / (i - start + 1));
  }
  return out;
}

function deltaHtml(key, d, { withSince = true } = {}) {
  if (!d) return "";
  const m = METRICS[key];
  const eps = Math.pow(10, -m.digits) / 2;
  const dir = d.change > eps ? "up" : d.change < -eps ? "down" : "flat";
  const tone = !m.better || dir === "flat" ? "" : dir === m.better ? "good" : "bad";
  const icon = { up: "i-up", down: "i-down", flat: "i-flat" }[dir];
  const amount = fmtUnit(Math.abs(d.change), key);
  const words = dir === "flat" ? "No change" : `${dir === "up" ? "Up" : "Down"} ${amount}`;
  const since = withSince ? ` <span class="since">since ${fmtDate(d.since)}</span>` : "";
  return `<span class="delta ${tone}"><svg class="icon"><use href="#${icon}"/></svg>${words}${since}</span>`;
}

// ------------------------------------------------------------------ sparkline (inline SVG + HTML end dot)
function sparkline(box, key, days = 90) {
  const cutoff = Date.now() / 1000 - days * DAY;
  let pts = series(state.rows, key).filter((p) => p.ts >= cutoff);
  if (pts.length < 2) pts = series(state.rows, key).slice(-12);
  box.innerHTML = "";
  if (pts.length < 2) return;
  const w = 100;
  const h = 30;
  const minT = pts[0].ts;
  const spanT = pts[pts.length - 1].ts - minT || 1;
  const vals = pts.map((p) => p.v);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || 1;
  const x = (t) => ((t - minT) / spanT) * w;
  const y = (v) => h - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * h;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.ts).toFixed(2)},${y(p.v).toFixed(2)}`).join("");
  const last = pts[pts.length - 1];
  // The SVG stretches to fill the box, so the end dot is HTML to stay round.
  box.innerHTML =
    `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">` +
    `<path class="area" d="${d}L${w},${h}L0,${h}Z"/><path class="line" d="${d}"/></svg>` +
    `<span class="end" style="left:${(x(last.ts) / w) * 100}%;top:${(y(last.v) / h) * 100}%"></span>`;
}

// ------------------------------------------------------------------ overview
function renderOverview() {
  const latest = state.rows[state.rows.length - 1];
  refs.heroWeight.textContent = latest ? fmt(latest.weight_lb, 1) : "—";
  refs.heroDelta.innerHTML = latest ? deltaHtml("weight_lb", delta("weight_lb")) : "No readings yet";
  refs.lastReading.textContent = latest ? `Last weigh-in ${fmtDateTime(latest.measure_ts)}` : "No weigh-ins yet";
  sparkline(refs.heroSpark, "weight_lb");

  refs.tiles.innerHTML = TILE_METRICS.map((key) => {
    const pts = series(state.rows, key);
    const last = pts[pts.length - 1];
    const m = METRICS[key];
    const unit = m.unit && m.unit !== "%" ? `<span class="unit">${m.unit}</span>` : m.unit === "%" ? '<span class="unit">%</span>' : "";
    return `
      <article class="card tile">
        <p class="label">${m.label}</p>
        <p class="tile-value">${last ? fmt(last.v, m.digits) : "—"}${last ? unit : ""}</p>
        ${deltaHtml(key, delta(key), { withSince: false }) || '<span class="delta"></span>'}
        <div class="spark" data-key="${key}"></div>
      </article>`;
  }).join("");
  refs.tiles.querySelectorAll(".spark").forEach((box) => sparkline(box, box.dataset.key));
}

// ------------------------------------------------------------------ chart
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(c) {
    const active = c.tooltip && c.tooltip.getActiveElements();
    if (!active || !active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = c.chartArea;
    const ctx = c.ctx;
    ctx.save();
    ctx.strokeStyle = cssVar("--line-strong");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

function renderChart() {
  const key = state.metric;
  const m = METRICS[key];
  refs.chartTitle.textContent = m.label + (m.unit ? ` (${m.unit})` : "");
  const pts = series(rowsInRange(state.days), key);
  refs.chartEmpty.classList.toggle("hidden", pts.length > 0);
  refs.chartCanvas.parentElement.classList.toggle("hidden", pts.length === 0);
  if (chart) chart.destroy();
  chart = null;
  if (!pts.length) return;

  const avg = trailingAverage(pts);
  // Break the average line across long gaps instead of drawing data that doesn't exist.
  const avgData = [];
  pts.forEach((p, i) => {
    if (i && p.ts - pts[i - 1].ts > GAP_BREAK_DAYS * DAY) avgData.push({ x: (p.ts - DAY) * 1000, y: null });
    avgData.push({ x: p.ts * 1000, y: avg[i] });
  });
  const seriesColor = cssVar("--series");
  const soft = cssVar("--series-soft");
  const grid = cssVar("--grid");
  const muted = cssVar("--muted");
  const ink = cssVar("--ink");
  const surface = cssVar("--surface");

  chart = new Chart(refs.chartCanvas, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Reading",
          data: pts.map((p) => ({ x: p.ts * 1000, y: p.v })),
          showLine: false,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: soft,
          pointBorderWidth: 0,
          pointHoverBackgroundColor: seriesColor,
          pointHoverBorderColor: surface,
          pointHoverBorderWidth: 2,
        },
        {
          label: "7-day average",
          data: avgData,
          spanGaps: false,
          cubicInterpolationMode: "monotone",
          borderColor: seriesColor,
          borderWidth: 2,
          borderCapStyle: "round",
          borderJoinStyle: "round",
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: seriesColor,
          pointHoverBorderColor: surface,
          pointHoverBorderWidth: 2,
          tension: 0.3,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: surface,
          titleColor: ink,
          bodyColor: ink,
          borderColor: cssVar("--line-strong"),
          borderWidth: 1,
          padding: 10,
          displayColors: true,
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
          callbacks: {
            title: (items) => fmtDateTime(items[0].parsed.x / 1000),
            label: (item) => ` ${item.dataset.label}: ${fmtUnit(item.parsed.y, key)}`,
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "PPp", displayFormats: { day: "MMM d", week: "MMM d", month: "MMM yyyy" } },
          grid: { display: false },
          border: { color: cssVar("--line-strong") },
          ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 },
        },
        y: {
          grace: "8%",
          grid: { color: grid },
          border: { display: false },
          ticks: { color: muted, maxTicksLimit: 6, callback: (v) => fmt(v, m.digits > 0 && Math.abs(v) < 100 ? 1 : 0) },
        },
      },
    },
    plugins: [crosshair],
  });
}

// ------------------------------------------------------------------ period summary
function renderStats() {
  const rows = rowsInRange(state.days);
  if (!rows.length) {
    refs.statsPeriod.textContent = "No readings in this range";
    refs.statsGrid.innerHTML = "";
    refs.matchedInsights.innerHTML = "";
    return;
  }
  refs.statsPeriod.textContent = `${fmtDate(rows[0].measure_ts, { month: "short", day: "numeric", year: "numeric" })} – ${fmtDate(
    rows[rows.length - 1].measure_ts,
    { month: "short", day: "numeric", year: "numeric" }
  )} · ${rows.length} readings`;

  refs.statsGrid.innerHTML = STAT_METRICS.map((key) => {
    const vals = series(rows, key);
    const m = METRICS[key];
    if (!vals.length) {
      return `<div class="stat"><span class="label">${m.label}</span><span class="stat-range">No values</span></div>`;
    }
    const nums = vals.map((p) => p.v);
    const avg = nums.reduce((a, b) => a + b, 0) / nums.length;
    const d = vals.length > 1 ? { change: nums[nums.length - 1] - nums[0], since: vals[0].ts } : null;
    return `
      <div class="stat">
        <span class="label">${m.label}</span>
        <span class="stat-avg">${fmtUnit(avg, key)}</span>
        <span class="stat-range">avg · ${fmt(Math.min(...nums), m.digits)}–${fmtUnit(Math.max(...nums), key)}</span>
        ${deltaHtml(key, d, { withSince: false })}
      </div>`;
  }).join("");
  renderMatched(rows);
}

function renderMatched(rows) {
  const latest = rows[rows.length - 1];
  const weight = Number(latest.weight_lb);
  const tol = 1;
  if (!Number.isFinite(weight)) {
    refs.matchedInsights.innerHTML = "";
    return;
  }
  const near = rows.filter((r) => Math.abs(Number(r.weight_lb) - weight) <= tol);
  const baseline = near.find((r) => r.measure_ts < latest.measure_ts && value(r, "muscle") !== null);
  let muscle = `Need two readings near ${fmt(weight)} lb (±${tol} lb) with muscle data to compare.`;
  if (baseline && value(latest, "muscle") !== null) {
    const change = value(latest, "muscle") - value(baseline, "muscle");
    const days = Math.round((latest.measure_ts - baseline.measure_ts) / DAY);
    muscle = `At about <b>${fmt(weight)} lb</b>, muscle went from <b>${fmt(value(baseline, "muscle"))} lb</b> (${fmtDate(
      baseline.measure_ts
    )}) to <b>${fmt(value(latest, "muscle"))} lb</b>: <b>${change >= 0 ? "+" : "−"}${fmt(Math.abs(change))} lb</b> over ${days} days.`;
  }
  const fatRows = near.filter((r) => value(r, "body_fat") !== null);
  let fat = "No body-fat readings at this weight yet.";
  if (fatRows.length > 1) {
    const sorted = [...fatRows].sort((a, b) => value(a, "body_fat") - value(b, "body_fat"));
    const lo = sorted[0];
    const hi = sorted[sorted.length - 1];
    fat = `At about <b>${fmt(weight)} lb</b>, body fat ranged from <b>${fmt(value(lo, "body_fat"))}%</b> (${fmtDate(
      lo.measure_ts
    )}) to <b>${fmt(value(hi, "body_fat"))}%</b> (${fmtDate(hi.measure_ts)}).`;
  }
  refs.matchedInsights.innerHTML = `
    <div class="insight"><h3>Muscle at the same weight</h3>${muscle}</div>
    <div class="insight"><h3>Body fat at the same weight</h3>${fat}</div>`;
}

// ------------------------------------------------------------------ table
const TABLE_COLUMNS = CHART_METRICS;

function renderColumnPicker() {
  refs.columnPicker.innerHTML = TABLE_COLUMNS.map(
    (key) =>
      `<label class="column-option"><input type="checkbox" value="${key}" ${state.columns.has(key) ? "checked" : ""}/>${
        METRICS[key].label
      }${METRICS[key].unit ? ` (${METRICS[key].unit})` : ""}</label>`
  ).join("");
}

function renderTable() {
  const cols = TABLE_COLUMNS.filter((k) => state.columns.has(k));
  refs.tableHead.innerHTML = `<tr><th>When</th>${cols
    .map((k) => `<th>${METRICS[k].label}${METRICS[k].unit ? ` (${METRICS[k].unit})` : ""}</th>`)
    .join("")}</tr>`;
  const rows = rowsInRange(state.days);
  refs.rowCount.textContent = `${rows.length} reading${rows.length === 1 ? "" : "s"}`;
  refs.tableBody.innerHTML = [...rows]
    .reverse()
    .slice(0, 200)
    .map((row) => {
      const cells = cols
        .map((k) => {
          const v = value(row, k);
          return v === null ? '<td class="na">—</td>' : `<td>${fmt(v, METRICS[k].digits)}</td>`;
        })
        .join("");
      return `<tr><td>${fmtDateTime(row.measure_ts)}</td>${cells}</tr>`;
    })
    .join("");
}

// ------------------------------------------------------------------ profiles + data
function renderPills() {
  refs.profilePills.innerHTML = state.profiles
    .map(
      (p) =>
        `<button type="button" data-profile="${escapeHtml(p.profile_id || "")}" class="${
          p.profile_id === state.profile ? "active" : ""
        }">${escapeHtml(p.profile_name)}<span class="count">${p.records}</span></button>`
    )
    .join("");
  refs.profilePills.classList.toggle("hidden", state.profiles.length < 2);
}

async function loadProfiles() {
  const res = await fetch("/api/profiles");
  state.profiles = (await res.json()).data || [];
  const saved = store.get("profile", null);
  const ids = state.profiles.map((p) => p.profile_id);
  if (!ids.includes(state.profile)) {
    state.profile = ids.includes(saved) ? saved : ids[0] || null; // API sorts by record count
  }
  renderPills();
}

async function loadRows() {
  if (!state.profile) {
    state.rows = [];
    return;
  }
  const qs = new URLSearchParams({ limit: "5000", profile: state.profile });
  const res = await fetch(`/api/measurements?${qs}`);
  state.rows = (await res.json()).data || [];
}

function renderAll() {
  renderOverview();
  renderChart();
  renderStats();
  renderTable();
}

async function refresh({ profiles = false } = {}) {
  if (profiles) await loadProfiles();
  await loadRows();
  renderAll();
}

// ------------------------------------------------------------------ Wyze connection
function desktopApi() {
  return authState && authState.desktop && window.pywebview && window.pywebview.api && window.pywebview.api.open_wyze_login
    ? window.pywebview.api
    : null;
}

function renderConnection() {
  const chip = refs.connectionChip;
  chip.classList.remove("ok", "warn");
  if (!authState) return;
  if (authState.connected || authState.has_saved_credentials) {
    const expires = authState.api_key_expires;
    const daysLeft = expires ? Math.floor((expires - Date.now() / 1000) / DAY) : null;
    const expiring = daysLeft !== null && daysLeft <= 30;
    chip.classList.add(expiring ? "warn" : "ok");
    refs.connectionText.textContent = expiring
      ? daysLeft < 0
        ? "API key expired"
        : `Key expires in ${daysLeft} d`
      : `Synced ${timeAgo(authState.last_sync_at)}`;
    chip.title =
      `Connected as ${authState.email || "Wyze account"}.` +
      (expires ? ` API key valid until ${new Date(expires * 1000).toLocaleDateString()}.` : "") +
      " Click to manage.";
  } else {
    chip.classList.add("warn");
    refs.connectionText.textContent = "Connect Wyze";
    chip.title = "Sign in to Wyze to sync your scale";
  }
}

async function fetchAuthStatus() {
  const res = await fetch("/api/auth/status");
  authState = await res.json();
  renderConnection();
  return authState;
}

function showConnectStep(step) {
  refs.connectStepCreds.classList.toggle("hidden", step !== "creds");
  refs.connectStepCode.classList.toggle("hidden", step !== "code");
  refs.connectSubmit.textContent = step === "code" ? "Verify" : "Connect";
}

function setConnectError(message) {
  refs.connectError.textContent = message || "";
  refs.connectError.classList.toggle("hidden", !message);
}

function openConnectDialog(reason = "") {
  pendingChallenge = null;
  showConnectStep("creds");
  setConnectError(reason);
  const s = authState || {};
  refs.cEmail.value = s.email || "";
  refs.cKeyId.value = s.saved_key_id || "";
  refs.cPassword.value = "";
  refs.cApiKey.value = "";
  const saved = Boolean(s.has_saved_credentials);
  refs.cPassword.placeholder = saved ? "Saved (leave blank to keep)" : "";
  refs.cApiKey.placeholder = saved ? "Saved (leave blank to keep)" : "";
  if (s.developer_console_url) refs.consoleLink.href = s.developer_console_url;
  if (s.secret_backend) refs.secretNote.textContent = `Signs in once, then uses a refreshable token. Secrets are stored in: ${s.secret_backend}.`;
  refs.disconnectBtn.classList.toggle("hidden", !s.connected);
  refs.wyzeSignInBtn.classList.toggle("hidden", !desktopApi());
  refs.manualDivider.classList.toggle("hidden", !desktopApi());
  if (!refs.connectDialog.open) refs.connectDialog.showModal();
  (refs.cEmail.value ? refs.cPassword : refs.cEmail).focus();
}

function openWyzeSignIn() {
  const api = desktopApi();
  if (!api) return false;
  if (refs.connectDialog.open) refs.connectDialog.close();
  api.open_wyze_login();
  setStatus("Sign in to Wyze in the new window and create an API key. It closes by itself.");
  return true;
}

function connectClicked() {
  if (authState && !authState.connected && !authState.has_saved_credentials && openWyzeSignIn()) return;
  openConnectDialog();
}

async function submitConnect(event) {
  event.preventDefault();
  setConnectError("");
  refs.connectSubmit.disabled = true;
  try {
    const { res, payload } = pendingChallenge
      ? await postJson("/api/auth/verify", { challenge_id: pendingChallenge, code: refs.cCode.value })
      : await postJson("/api/auth/login", {
          email: refs.cEmail.value,
          password: refs.cPassword.value,
          key_id: refs.cKeyId.value,
          api_key: refs.cApiKey.value,
          remember_password: refs.cRemember.checked,
        });
    if (!res.ok) throw new Error(payload.error || `Login failed (${res.status})`);
    if (payload.status === "mfa_required") {
      pendingChallenge = payload.challenge_id;
      refs.codePrompt.textContent = payload.prompt;
      refs.cCode.value = "";
      showConnectStep("code");
      refs.cCode.focus();
      return;
    }
    refs.connectDialog.close();
    await fetchAuthStatus();
    await runSync();
  } catch (err) {
    setConnectError(err.message);
  } finally {
    refs.connectSubmit.disabled = false;
  }
}

async function disconnect() {
  await postJson("/api/auth/logout");
  refs.connectDialog.close();
  await fetchAuthStatus();
  setStatus("Disconnected from Wyze. Cached tokens removed.");
}

function consumeConnectParams() {
  const params = new URLSearchParams(location.search);
  if (![...params.keys()].length) return null;
  history.replaceState(null, "", location.pathname);
  if (params.get("connected")) return { kind: "connected" };
  if (params.get("challenge")) return { kind: "challenge", id: params.get("challenge"), prompt: params.get("prompt") };
  if (params.get("connect_error")) return { kind: "error", message: params.get("connect_error") };
  return null;
}

// ------------------------------------------------------------------ sync / import / export
async function runSync({ quiet = false, full = false } = {}) {
  if (syncing) return;
  syncing = true;
  refs.syncNow.disabled = true;
  refs.syncNow.classList.add("busy");
  if (!quiet) setStatus(full ? "Re-downloading full history from Wyze…" : "Syncing from Wyze…");
  try {
    const { res, payload } = await postJson("/api/sync", { full });
    if (res.status === 401 && payload.needs_login) {
      await fetchAuthStatus();
      setStatus(payload.error, "error");
      if (!openWyzeSignIn()) openConnectDialog(payload.error);
      return;
    }
    if (!res.ok) throw new Error(payload.error || `Sync failed (${res.status})`);
    const result = payload.result || {};
    await fetchAuthStatus();
    await refresh({ profiles: true });
    const errors = result.errors || [];
    if (errors.length) setStatus(`Synced with problems: ${errors[0]}`, "error");
    else if (result.inserted) {
      const who = (result.people || []).filter((p) => p.new).map((p) => `${p.name} +${p.new}`).join(", ");
      setStatus(`${result.inserted} new reading${result.inserted === 1 ? "" : "s"} (${who}).`);
    } else if (!quiet) setStatus("Up to date — no new readings.");
    else setStatus("");
  } catch (err) {
    setStatus(`Sync error: ${err.message}`, "error");
  } finally {
    syncing = false;
    refs.syncNow.disabled = false;
    refs.syncNow.classList.remove("busy");
  }
}

async function runImport(file) {
  setStatus(`Importing ${file.name}…`);
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/import", { method: "POST", body: form });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.error || `Import failed (${res.status})`);
    const r = payload.result || {};
    await refresh({ profiles: true });
    const issues = (r.parse_errors || []).length;
    setStatus(
      `Imported ${r.inserted || 0} new / ${r.skipped || 0} already stored from ${r.sheets_processed || 0} sheet(s).` +
        (issues ? ` ${issues} row(s) skipped.` : ""),
      issues ? "error" : "info"
    );
  } catch (err) {
    setStatus(`Import error: ${err.message}`, "error");
  } finally {
    refs.importFile.value = "";
  }
}

function openExportDialog() {
  refs.exportProfile.innerHTML =
    '<option value="">Everyone</option>' +
    state.profiles
      .map((p) => `<option value="${escapeHtml(p.profile_id || "")}">${escapeHtml(p.profile_name)}</option>`)
      .join("");
  refs.exportProfile.value = state.profile || "";
  refs.exportError.classList.add("hidden");
  refs.exportCustom.classList.toggle("hidden", refs.exportRange.value !== "custom");
  refs.exportDialog.showModal();
}

function submitExport(event) {
  event.preventDefault();
  const qs = new URLSearchParams({ range: refs.exportRange.value });
  if (refs.exportProfile.value) qs.set("profile", refs.exportProfile.value);
  if (refs.exportRange.value === "custom") {
    if (!refs.exportStart.value || !refs.exportEnd.value) {
      refs.exportError.textContent = "Choose both dates.";
      refs.exportError.classList.remove("hidden");
      return;
    }
    qs.set("start", refs.exportStart.value);
    qs.set("end", refs.exportEnd.value);
  }
  refs.exportDialog.close();
  window.location.href = `/api/export?${qs}`;
}

// ------------------------------------------------------------------ menu
function toggleMenu(open) {
  const show = open ?? refs.menu.classList.contains("hidden");
  refs.menu.classList.toggle("hidden", !show);
  refs.menuBtn.setAttribute("aria-expanded", String(show));
}

function menuAction(action) {
  toggleMenu(false);
  if (action === "export") openExportDialog();
  else if (action === "import") refs.importFile.click();
  else if (action === "full-sync") runSync({ full: true });
  else if (action === "connection") openConnectDialog();
}

// ------------------------------------------------------------------ wiring
function bind() {
  refs.metricSelect.innerHTML = CHART_METRICS.map(
    (k) => `<option value="${k}">${METRICS[k].label}${METRICS[k].unit ? ` (${METRICS[k].unit})` : ""}</option>`
  ).join("");
  state.metric = CHART_METRICS.includes(store.get("metric")) ? store.get("metric") : "weight_lb";
  state.days = store.get("days", 365);
  state.columns = new Set(store.get("columns", TABLE_DEFAULT));
  refs.metricSelect.value = state.metric;
  refs.rangePills.querySelectorAll("button").forEach((b) => b.classList.toggle("active", Number(b.dataset.days) === state.days));
  renderColumnPicker();

  refs.syncNow.addEventListener("click", () => runSync());
  refs.connectionChip.addEventListener("click", connectClicked);
  refs.menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });
  refs.menu.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (btn) menuAction(btn.dataset.action);
  });
  document.addEventListener("click", (e) => {
    if (!refs.menu.contains(e.target)) toggleMenu(false);
    const cols = document.querySelector(".columns-menu");
    if (cols.open && !cols.contains(e.target)) cols.open = false;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleMenu(false);
  });
  refs.importFile.addEventListener("change", () => refs.importFile.files[0] && runImport(refs.importFile.files[0]));

  refs.profilePills.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-profile]");
    if (!btn || btn.dataset.profile === state.profile) return;
    state.profile = btn.dataset.profile;
    store.set("profile", state.profile);
    renderPills();
    refresh().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
  });
  refs.metricSelect.addEventListener("change", () => {
    state.metric = refs.metricSelect.value;
    store.set("metric", state.metric);
    renderChart();
  });
  refs.rangePills.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-days]");
    if (!btn) return;
    state.days = Number(btn.dataset.days);
    store.set("days", state.days);
    refs.rangePills.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    renderChart();
    renderStats();
    renderTable();
  });
  refs.columnPicker.addEventListener("change", (e) => {
    const key = e.target.value;
    if (e.target.checked) state.columns.add(key);
    else state.columns.delete(key);
    if (!state.columns.size) state.columns.add("weight_lb");
    store.set("columns", [...state.columns]);
    renderTable();
  });

  refs.exportRange.addEventListener("change", () => refs.exportCustom.classList.toggle("hidden", refs.exportRange.value !== "custom"));
  refs.exportForm.addEventListener("submit", submitExport);
  refs.exportDialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => refs.exportDialog.close()));

  refs.connectForm.addEventListener("submit", submitConnect);
  refs.connectClose.addEventListener("click", () => refs.connectDialog.close());
  refs.disconnectBtn.addEventListener("click", disconnect);
  refs.wyzeSignInBtn.addEventListener("click", openWyzeSignIn);

  // Re-theme the canvas chart when the OS switches light/dark.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderChart());
  setInterval(renderConnection, 60 * 1000);
}

async function boot() {
  const outcome = consumeConnectParams();
  bind();
  await refresh({ profiles: true });
  const s = await fetchAuthStatus();

  if (outcome?.kind === "connected") {
    setStatus("Connected to Wyze — pulling your history…");
    await runSync();
    return;
  }
  if (outcome?.kind === "challenge") {
    openConnectDialog();
    pendingChallenge = outcome.id;
    refs.codePrompt.textContent = outcome.prompt || "Enter the verification code.";
    showConnectStep("code");
    refs.cCode.focus();
    return;
  }
  if (outcome?.kind === "error") {
    openConnectDialog(outcome.message);
    return;
  }
  if (!s.connected && !s.has_saved_credentials) {
    if (s.desktop) setStatus("Sign in to Wyze in the other window and create an API key — it closes by itself.");
    else openConnectDialog();
    return;
  }
  if (!s.last_sync_at || Date.now() / 1000 - s.last_sync_at > AUTO_SYNC_AFTER_SECONDS) await runSync({ quiet: true });
}

boot().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
