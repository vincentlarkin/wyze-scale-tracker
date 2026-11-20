const metricLabels = {
  weight_lb: "Weight (lb)",
  weight_kg: "Weight (kg)",
  bmi: "BMI",
  body_fat: "Body Fat (%)",
  muscle: "Muscle (lb)",
  skeletal_muscle: "Skeletal Muscle (lb)",
  body_water: "Body Water (%)",
  protein: "Protein (%)",
  bone_mineral: "Bone Mineral (lb)",
  subcutaneous_fat: "Subcutaneous Fat (%)",
  body_vfr: "Visceral Fat",
  bmr: "BMR",
  metabolic_age: "Metabolic Age",
  body_score: "Body Score",
};

const AUTO_SYNC_AFTER_SECONDS = 15 * 60;

const tableColumnDefs = [
  { key: "weight_lb", label: "Weight (lb)", digits: 1 },
  { key: "weight_kg", label: "Weight (kg)", digits: 1 },
  { key: "bmi", label: "BMI", digits: 1 },
  { key: "body_fat", label: "Body Fat %", digits: 1, suffix: "%" },
  { key: "muscle", label: "Muscle (lb)", digits: 1 },
  { key: "skeletal_muscle", label: "Skeletal Muscle (lb)", digits: 1 },
  { key: "body_water", label: "Body Water %", digits: 1, suffix: "%" },
  { key: "protein", label: "Protein %", digits: 1, suffix: "%" },
  { key: "bone_mineral", label: "Bone Mineral (lb)", digits: 1 },
  { key: "subcutaneous_fat", label: "Subcutaneous Fat %", digits: 1, suffix: "%" },
  { key: "body_vfr", label: "Visceral Fat", digits: 1 },
  { key: "bmr", label: "BMR", digits: 0 },
  { key: "metabolic_age", label: "Metabolic Age", digits: 0 },
  { key: "body_score", label: "Body Score", digits: 0 },
];

const defaultFewColumns = ["weight_lb", "bmi", "body_fat", "muscle", "body_water", "protein"];
const allColumnKeys = tableColumnDefs.map((column) => column.key);
let selectedColumnKeys = new Set(defaultFewColumns);

const statsMetricDefs = [
  { key: "weight_lb", title: "Weight", icon: "fa-weight-scale", digits: 1, suffix: " lb" },
  { key: "muscle", title: "Muscle", icon: "fa-dumbbell", digits: 1, suffix: " lb" },
  { key: "muscle_ratio", title: "Muscle/Weight", icon: "fa-percent", digits: 1, suffix: "%" },
  { key: "bmi", title: "BMI", icon: "fa-ruler-vertical", digits: 1, suffix: "" },
  { key: "body_fat", title: "Body Fat", icon: "fa-chart-pie", digits: 1, suffix: "%" },
  { key: "body_water", title: "Body Water", icon: "fa-droplet", digits: 1, suffix: "%" },
];

const zeroMeansMissingMetrics = new Set([
  "muscle",
  "body_fat",
  "body_water",
  "protein",
  "bone_mineral",
  "body_vfr",
  "bmr",
  "metabolic_age",
  "muscle_ratio",
  "skeletal_muscle",
  "subcutaneous_fat",
  "body_score",
]);

const refs = {
  syncNow: document.getElementById("syncNow"),
  importNow: document.getElementById("importNow"),
  importFile: document.getElementById("importFile"),
  exportNow: document.getElementById("exportNow"),
  exportRange: document.getElementById("exportRange"),
  exportCustom: document.getElementById("exportCustom"),
  exportStart: document.getElementById("exportStart"),
  exportEnd: document.getElementById("exportEnd"),
  profileSelect: document.getElementById("profileSelect"),
  statusBar: document.getElementById("statusBar"),
  connectionChip: document.getElementById("connectionChip"),
  connectionText: document.getElementById("connectionText"),
  connectDialog: document.getElementById("connectDialog"),
  connectForm: document.getElementById("connectForm"),
  connectClose: document.getElementById("connectClose"),
  connectStepCreds: document.getElementById("connectStepCreds"),
  connectStepCode: document.getElementById("connectStepCode"),
  connectError: document.getElementById("connectError"),
  connectSubmit: document.getElementById("connectSubmit"),
  disconnectBtn: document.getElementById("disconnectBtn"),
  consoleLink: document.getElementById("consoleLink"),
  codePrompt: document.getElementById("codePrompt"),
  cEmail: document.getElementById("cEmail"),
  cPassword: document.getElementById("cPassword"),
  cKeyId: document.getElementById("cKeyId"),
  cApiKey: document.getElementById("cApiKey"),
  cRemember: document.getElementById("cRemember"),
  cCode: document.getElementById("cCode"),
  wyzeSignInBtn: document.getElementById("wyzeSignInBtn"),
  manualDivider: document.getElementById("manualDivider"),
  metricSelect: document.getElementById("metricSelect"),
  daysSelect: document.getElementById("daysSelect"),
  latestWeight: document.getElementById("latestWeight"),
  latestBmi: document.getElementById("latestBmi"),
  latestBodyFat: document.getElementById("latestBodyFat"),
  latestBodyWater: document.getElementById("latestBodyWater"),
  statsPeriod: document.getElementById("statsPeriod"),
  statsGrid: document.getElementById("statsGrid"),
  matchedInsights: document.getElementById("matchedInsights"),
  rowCount: document.getElementById("rowCount"),
  columnPicker: document.getElementById("columnPicker"),
  presetFew: document.getElementById("presetFew"),
  presetAll: document.getElementById("presetAll"),
  tableHead: document.getElementById("recordsHead"),
  tableBody: document.querySelector("#recordsTable tbody"),
  chartCanvas: document.getElementById("trendChart"),
};

let allRows = [];
let chart = null;
let profiles = [];

function setStatus(message, type = "info") {
  refs.statusBar.classList.remove("hidden", "info", "error");
  refs.statusBar.classList.add(type);
  refs.statusBar.textContent = message;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

function formatNum(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toFixed(digits);
}

function formatValueByColumn(value, column) {
  const base = formatNum(value, column.digits ?? 1);
  if (base === "-") return base;
  return `${base}${column.suffix || ""}`;
}

function formatWhen(row) {
  if (!row.measure_ts) {
    return row.measured_at || "-";
  }
  return new Date(row.measure_ts * 1000).toLocaleString();
}

function formatDate(ts) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleDateString();
}

function selectedProfile() {
  return (refs.profileSelect.value || "").trim();
}

function selectedProfileName() {
  const selected = refs.profileSelect.options[refs.profileSelect.selectedIndex];
  if (!selected || !selected.value) return "All Profiles";
  return selected.textContent.replace(/\s+\(\d+\)\s*$/, "");
}

function profileLabel(row) {
  if (row.profile_name) return row.profile_name;
  if (row.profile_id) {
    const match = profiles.find((p) => p.profile_id === row.profile_id);
    return (match && match.profile_name) || row.profile_id;
  }
  return "Unknown";
}

function latestRow() {
  if (!allRows.length) return null;
  return allRows[allRows.length - 1];
}

function renderSummary() {
  const latest = latestRow();
  if (!latest) {
    refs.latestWeight.textContent = "-";
    refs.latestBmi.textContent = "-";
    refs.latestBodyFat.textContent = "-";
    refs.latestBodyWater.textContent = "-";
    return;
  }
  refs.latestWeight.textContent = `${formatNum(latest.weight_lb, 1)} lb`;
  refs.latestBmi.textContent = formatNum(latest.bmi, 1);
  refs.latestBodyFat.textContent = `${formatNum(latest.body_fat, 1)}%`;
  refs.latestBodyWater.textContent = `${formatNum(latest.body_water, 1)}%`;
}

function metricStats(rows, key) {
  const values = rows
    .map((row) => metricValue(row, key))
    .filter((value) => value !== null && value !== undefined)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));

  if (!values.length) {
    return null;
  }

  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const first = values[0];
  const last = values[values.length - 1];
  const change = last - first;

  return {
    avg,
    min,
    max,
    change,
    count: values.length,
  };
}

function metricValue(row, key) {
  if (key === "muscle_ratio") {
    const muscle = metricValue(row, "muscle");
    const weight = Number(row.weight_lb);
    if (muscle === null || !Number.isFinite(weight) || weight <= 0) return null;
    return (muscle / weight) * 100;
  }
  const value = Number(row[key]);
  if (!Number.isFinite(value)) return null;
  if (zeroMeansMissingMetrics.has(key) && value === 0) return null;
  return value;
}

function daysBetween(startTs, endTs) {
  if (!startTs || !endTs) return null;
  return Math.round((endTs - startTs) / 86400);
}

function sameWeightRows(rows, targetWeight, tolerance = 1) {
  return rows.filter((row) => {
    const weight = Number(row.weight_lb);
    return Number.isFinite(weight) && Math.abs(weight - targetWeight) <= tolerance;
  });
}

function renderMatchedInsights() {
  if (!allRows.length) {
    refs.matchedInsights.innerHTML = "";
    return;
  }

  const tolerance = 1.0;
  const latest = allRows[allRows.length - 1];
  const latestWeight = Number(latest.weight_lb);
  if (!Number.isFinite(latestWeight)) {
    refs.matchedInsights.innerHTML = "";
    return;
  }

  const matched = sameWeightRows(allRows, latestWeight, tolerance);
  const priorMatched = matched.filter((row) => Number(row.measure_ts) < Number(latest.measure_ts));
  const baseline = priorMatched[0] || null;

  let muscleHtml = `
    <article class="insight-card">
      <h3><i class="fa-solid fa-dumbbell"></i> Muscle At Matched Weight</h3>
      <div class="insight-body">Need at least two readings near ${formatNum(latestWeight, 1)} lb (±${tolerance} lb) to compare.</div>
    </article>
  `;

  if (baseline) {
    const beforeMuscle = metricValue(baseline, "muscle");
    const nowMuscle = metricValue(latest, "muscle");
    if (beforeMuscle !== null && nowMuscle !== null) {
      const delta = nowMuscle - beforeMuscle;
      const sign = delta > 0 ? "+" : "";
      const days = daysBetween(Number(baseline.measure_ts), Number(latest.measure_ts));
      muscleHtml = `
        <article class="insight-card">
          <h3><i class="fa-solid fa-dumbbell"></i> Muscle At Matched Weight</h3>
          <div class="insight-body">
            Around <b>${formatNum(latestWeight, 1)} lb</b> (±${tolerance} lb), muscle moved from
            <b>${formatNum(beforeMuscle, 1)} lb</b> (${formatDate(Number(baseline.measure_ts))})
            to <b>${formatNum(nowMuscle, 1)} lb</b> (${formatDate(Number(latest.measure_ts))}):
            <b>${sign}${formatNum(delta, 1)} lb</b>${days !== null ? ` over ${days} days` : ""}.
          </div>
        </article>
      `;
    }
  }

  let bodyFatHtml = `
    <article class="insight-card">
      <h3><i class="fa-solid fa-chart-pie"></i> Body Fat At Matched Weight</h3>
      <div class="insight-body">No body fat trend available yet at this matched weight range.</div>
    </article>
  `;

  const matchedWithBodyFat = matched.filter((row) => metricValue(row, "body_fat") !== null);
  if (matchedWithBodyFat.length) {
    const sortedByBodyFat = [...matchedWithBodyFat].sort(
      (a, b) => Number(metricValue(a, "body_fat")) - Number(metricValue(b, "body_fat"))
    );
    const lowest = sortedByBodyFat[0];
    const highest = sortedByBodyFat[sortedByBodyFat.length - 1];
    const currentBodyFat = metricValue(latest, "body_fat");
    const currentLine =
      currentBodyFat === null
        ? ""
        : ` Current reading: <b>${formatNum(currentBodyFat, 1)}%</b> (${formatDate(Number(latest.measure_ts))}).`;
    bodyFatHtml = `
      <article class="insight-card">
        <h3><i class="fa-solid fa-chart-pie"></i> Body Fat At Matched Weight</h3>
        <div class="insight-body">
          Around <b>${formatNum(latestWeight, 1)} lb</b> (±${tolerance} lb), lowest body fat was
          <b>${formatNum(metricValue(lowest, "body_fat"), 1)}%</b> (${formatDate(Number(lowest.measure_ts))}) and highest was
          <b>${formatNum(metricValue(highest, "body_fat"), 1)}%</b> (${formatDate(Number(highest.measure_ts))}).${currentLine}
        </div>
      </article>
    `;
  }

  refs.matchedInsights.innerHTML = muscleHtml + bodyFatHtml;
}

function renderStats() {
  if (!allRows.length) {
    refs.statsPeriod.textContent = "No data in this period";
    refs.statsGrid.innerHTML = "";
    refs.matchedInsights.innerHTML = "";
    return;
  }

  const first = allRows[0];
  const last = allRows[allRows.length - 1];
  refs.statsPeriod.textContent = `${selectedProfileName()} | ${formatDate(first.measure_ts)} - ${formatDate(last.measure_ts)} | ${allRows.length} measurements`;

  refs.statsGrid.innerHTML = statsMetricDefs
    .map((metric) => {
      const stats = metricStats(allRows, metric.key);
      if (!stats) {
        return `
          <article class="stat-card">
            <h3><i class="fa-solid ${metric.icon}"></i> ${metric.title}</h3>
            <div class="stat-values">No values in this period.</div>
          </article>
        `;
      }
      const sign = stats.change > 0 ? "+" : "";
      const suffix = metric.suffix || "";
      return `
        <article class="stat-card">
          <h3><i class="fa-solid ${metric.icon}"></i> ${metric.title}</h3>
          <div class="stat-values">
            <div><span class="stat-label">Average:</span>${formatNum(stats.avg, metric.digits)}${suffix}</div>
            <div><span class="stat-label">Highest:</span>${formatNum(stats.max, metric.digits)}${suffix}</div>
            <div><span class="stat-label">Lowest:</span>${formatNum(stats.min, metric.digits)}${suffix}</div>
            <div><span class="stat-label">Change:</span>${sign}${formatNum(stats.change, metric.digits)}${suffix}</div>
          </div>
        </article>
      `;
    })
    .join("");

  renderMatchedInsights();
}

function renderTableHead() {
  const selected = tableColumnDefs.filter((column) => selectedColumnKeys.has(column.key));
  const columnsHtml = selected.map((column) => `<th>${column.label}</th>`).join("");
  refs.tableHead.innerHTML = `<tr><th>When</th><th>Profile</th>${columnsHtml}</tr>`;
}

function renderTable() {
  renderTableHead();
  const selected = tableColumnDefs.filter((column) => selectedColumnKeys.has(column.key));
  const recent = [...allRows].reverse().slice(0, 80);
  refs.tableBody.innerHTML = "";
  refs.rowCount.textContent = `${allRows.length} rows`;

  for (const row of recent) {
    const dataCells = selected
      .map((column) => `<td>${formatValueByColumn(row[column.key], column)}</td>`)
      .join("");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${formatWhen(row)}</td><td>${escapeHtml(profileLabel(row))}</td>${dataCells}`;
    refs.tableBody.appendChild(tr);
  }
}

function renderColumnPicker() {
  refs.columnPicker.innerHTML = "";
  for (const column of tableColumnDefs) {
    const id = `col_${column.key}`;
    const wrapper = document.createElement("label");
    wrapper.className = "column-option";
    wrapper.innerHTML = `<input id="${id}" type="checkbox" ${selectedColumnKeys.has(column.key) ? "checked" : ""} />${column.label}`;
    const checkbox = wrapper.querySelector("input");
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        selectedColumnKeys.add(column.key);
      } else {
        selectedColumnKeys.delete(column.key);
      }
      if (selectedColumnKeys.size === 0) {
        selectedColumnKeys.add(defaultFewColumns[0]);
      }
      renderTable();
    });
    refs.columnPicker.appendChild(wrapper);
  }
}

function applyColumnPreset(mode) {
  selectedColumnKeys = new Set(mode === "all" ? allColumnKeys : defaultFewColumns);
  renderColumnPicker();
  renderTable();
}

function renderChart() {
  const metric = refs.metricSelect.value;
  const label = metricLabels[metric] || metric;
  const points = allRows
    .filter((row) => row[metric] !== null && row[metric] !== undefined)
    .map((row) => ({ when: formatWhen(row), value: Number(row[metric]) }));

  if (chart) {
    chart.destroy();
  }

  chart = new Chart(refs.chartCanvas, {
    type: "line",
    data: {
      labels: points.map((point) => point.when),
      datasets: [
        {
          label,
          data: points.map((point) => point.value),
          borderColor: "#0f9d8a",
          backgroundColor: "rgba(15,157,138,0.18)",
          borderWidth: 2.2,
          pointRadius: 2,
          tension: 0.25,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 420 },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#5b6872", maxTicksLimit: 10 },
          grid: { color: "rgba(91,104,114,0.1)" },
        },
        y: {
          ticks: { color: "#5b6872" },
          grid: { color: "rgba(91,104,114,0.1)" },
        },
      },
    },
  });
}

function toggleExportCustom() {
  const isCustom = refs.exportRange.value === "custom";
  refs.exportCustom.classList.toggle("hidden", !isCustom);
}

async function fetchProfiles() {
  const res = await fetch("/api/profiles");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Could not load profiles (${res.status})`);
  }
  const payload = await res.json();
  profiles = payload.data || [];

  const current = selectedProfile();
  refs.profileSelect.innerHTML = '<option value="">All Profiles</option>';
  for (const item of profiles) {
    const option = document.createElement("option");
    option.value = item.profile_id || "";
    option.textContent = `${item.profile_name} (${item.records})`;
    refs.profileSelect.appendChild(option);
  }
  if (current && profiles.some((profile) => profile.profile_id === current)) {
    refs.profileSelect.value = current;
  } else if (!profileChosenByUser && profiles.length > 1) {
    // Mixing people makes the chart/stats meaningless; default to the main profile.
    const main = [...profiles].sort((a, b) => b.records - a.records)[0];
    if (main && main.profile_id) refs.profileSelect.value = main.profile_id;
  }
}

let profileChosenByUser = false;

async function fetchMeasurements() {
  const days = Number(refs.daysSelect.value);
  const qs = new URLSearchParams();
  qs.set("limit", "5000");

  const profile = selectedProfile();
  if (profile) {
    qs.set("profile", profile);
  }
  if (Number.isFinite(days) && days > 0) {
    qs.set("days", String(days));
  }

  const res = await fetch(`/api/measurements?${qs.toString()}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Could not load measurements (${res.status})`);
  }
  const payload = await res.json();
  allRows = payload.data || [];
}

async function refreshUi({ reloadProfiles = false } = {}) {
  if (reloadProfiles) {
    await fetchProfiles();
  }
  await fetchMeasurements();
  renderSummary();
  renderStats();
  renderTable();
  renderChart();
  if (!allRows.length) {
    setStatus("No data for this profile/range yet. Try Sync or Import.", "info");
  } else {
    const latest = latestRow();
    setStatus(`Loaded ${allRows.length} measurements. Latest: ${formatWhen(latest)}`, "info");
  }
}

// ---------------------------------------------------------------- Wyze connection

let authState = null;
let pendingChallenge = null;

// Desktop shell (pywebview) exposes window.pywebview.api.open_wyze_login().
function desktopApi() {
  return authState && authState.desktop && window.pywebview && window.pywebview.api && window.pywebview.api.open_wyze_login
    ? window.pywebview.api
    : null;
}

function openWyzeSignIn() {
  const api = desktopApi();
  if (!api) return false;
  if (refs.connectDialog.open) refs.connectDialog.close();
  api.open_wyze_login();
  setStatus("Sign in to Wyze in the new window and create an API key. It will close by itself.", "info");
  return true;
}

function connectClicked() {
  if (authState && !authState.connected && !authState.has_saved_credentials && openWyzeSignIn()) return;
  openConnectDialog();
}

// After the sign-in window closes, the desktop shell reloads us with the outcome in the URL.
function consumeConnectParams() {
  const params = new URLSearchParams(location.search);
  if (![...params.keys()].length) return null;
  history.replaceState(null, "", location.pathname);
  if (params.get("connected")) return { kind: "connected" };
  if (params.get("challenge")) return { kind: "challenge", id: params.get("challenge"), prompt: params.get("prompt") };
  if (params.get("connect_error")) return { kind: "error", message: params.get("connect_error") };
  return null;
}

async function postJson(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  return { res, payload };
}

function timeAgo(ts) {
  if (!ts) return "never";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  return `${Math.floor(secs / 86400)} d ago`;
}

function renderConnection() {
  const chip = refs.connectionChip;
  chip.classList.remove("ok", "warn");
  if (!authState) {
    refs.connectionText.textContent = "Checking…";
    return;
  }
  if (authState.connected || authState.has_saved_credentials) {
    const expires = authState.api_key_expires;
    const daysLeft = expires ? Math.floor((expires - Date.now() / 1000) / 86400) : null;
    const expiring = daysLeft !== null && daysLeft <= 30;
    chip.classList.add(expiring ? "warn" : "ok");
    refs.connectionText.textContent = expiring
      ? daysLeft < 0 ? "Wyze API key expired" : `Wyze key expires in ${daysLeft} d`
      : `Wyze · synced ${timeAgo(authState.last_sync_at)}`;
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
  const state = authState || {};
  refs.cEmail.value = state.email || "";
  refs.cKeyId.value = state.saved_key_id || "";
  refs.cPassword.value = "";
  refs.cApiKey.value = "";
  const saved = Boolean(state.has_saved_credentials);
  refs.cPassword.placeholder = saved ? "Saved (leave blank to keep)" : "";
  refs.cApiKey.placeholder = saved ? "Saved (leave blank to keep)" : "";
  if (state.developer_console_url) refs.consoleLink.href = state.developer_console_url;
  refs.disconnectBtn.classList.toggle("hidden", !state.connected);
  refs.wyzeSignInBtn.classList.toggle("hidden", !desktopApi());
  refs.manualDivider.classList.toggle("hidden", !desktopApi());
  if (!refs.connectDialog.open) refs.connectDialog.showModal();
  (refs.cEmail.value ? refs.cPassword : refs.cEmail).focus();
}

async function submitConnect(event) {
  event.preventDefault();
  setConnectError("");
  refs.connectSubmit.disabled = true;
  try {
    let result;
    if (pendingChallenge) {
      result = await postJson("/api/auth/verify", {
        challenge_id: pendingChallenge,
        code: refs.cCode.value,
      });
    } else {
      result = await postJson("/api/auth/login", {
        email: refs.cEmail.value,
        password: refs.cPassword.value,
        key_id: refs.cKeyId.value,
        api_key: refs.cApiKey.value,
        remember_password: refs.cRemember.checked,
      });
    }
    const { res, payload } = result;
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
  setStatus("Disconnected from Wyze. Cached tokens removed.", "info");
}

// ---------------------------------------------------------------- sync

let syncing = false;

async function runSync({ quiet = false } = {}) {
  if (syncing) return;
  syncing = true;
  refs.syncNow.disabled = true;
  refs.syncNow.classList.add("busy");
  if (!quiet) setStatus("Syncing from Wyze...", "info");
  try {
    const { res, payload } = await postJson("/api/sync");
    if (res.status === 401 && payload.needs_login) {
      await fetchAuthStatus();
      setStatus(payload.error, "error");
      openConnectDialog(payload.error);
      return;
    }
    if (!res.ok) throw new Error(payload.error || `Sync failed (${res.status})`);

    const result = payload.result || {};
    await fetchAuthStatus();
    await refreshUi({ reloadProfiles: true });
    const people = (result.people || [])
      .map((p) => `${p.name}${p.error ? " (error)" : ` +${p.new}`}`)
      .join(", ");
    const errors = result.errors || [];
    const message = `Sync complete: ${result.inserted || 0} new${people ? ` (${people})` : ""}.`;
    setStatus(errors.length ? `${message} ${errors[0]}` : message, errors.length ? "error" : "info");
  } catch (err) {
    setStatus(`Sync error: ${err.message}`, "error");
  } finally {
    syncing = false;
    refs.syncNow.disabled = false;
    refs.syncNow.classList.remove("busy");
  }
}

async function runImport() {
  const file = refs.importFile.files && refs.importFile.files[0];
  if (!file) {
    setStatus("Select your exported .xlsx file first.", "error");
    return;
  }
  refs.importNow.disabled = true;
  setStatus("Importing Excel data...", "info");
  try {
    const form = new FormData();
    form.append("file", file);

    const res = await fetch("/api/import", {
      method: "POST",
      body: form,
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(payload.error || `Import failed (${res.status})`);
    }
    const result = payload.result || {};
    await refreshUi({ reloadProfiles: true });
    const parseErrCount = (result.parse_errors || []).length;
    const summary = `Import complete: ${result.inserted || 0} new / ${result.skipped || 0} existing from ${result.sheets_processed || 0} sheet(s).`;
    setStatus(
      parseErrCount ? `${summary} ${parseErrCount} row(s) were skipped due to parse issues.` : summary,
      parseErrCount ? "error" : "info"
    );
  } catch (err) {
    setStatus(`Import error: ${err.message}`, "error");
  } finally {
    refs.importNow.disabled = false;
  }
}

function runExport() {
  const range = refs.exportRange.value || "all";
  const qs = new URLSearchParams();
  qs.set("range", range);

  const profile = selectedProfile();
  if (profile) {
    qs.set("profile", profile);
  }

  if (range === "custom") {
    const start = refs.exportStart.value;
    const end = refs.exportEnd.value;
    if (!start || !end) {
      setStatus("Choose both custom start and end dates for export.", "error");
      return;
    }
    qs.set("start", start);
    qs.set("end", end);
  }

  window.location.href = `/api/export?${qs.toString()}`;
}

refs.syncNow.addEventListener("click", () => runSync());
refs.connectionChip.addEventListener("click", connectClicked);
refs.wyzeSignInBtn.addEventListener("click", openWyzeSignIn);
refs.connectForm.addEventListener("submit", submitConnect);
refs.connectClose.addEventListener("click", () => refs.connectDialog.close());
refs.disconnectBtn.addEventListener("click", disconnect);
refs.importNow.addEventListener("click", runImport);
refs.exportNow.addEventListener("click", runExport);
refs.exportRange.addEventListener("change", toggleExportCustom);
refs.metricSelect.addEventListener("change", renderChart);
refs.daysSelect.addEventListener("change", () => {
  refreshUi().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
});
refs.profileSelect.addEventListener("change", () => {
  profileChosenByUser = true;
  refreshUi().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
});
refs.presetFew.addEventListener("click", () => applyColumnPreset("few"));
refs.presetAll.addEventListener("click", () => applyColumnPreset("all"));

async function boot() {
  const outcome = consumeConnectParams();
  toggleExportCustom();
  renderColumnPicker();
  await refreshUi({ reloadProfiles: true });
  const state = await fetchAuthStatus();

  if (outcome && outcome.kind === "connected") {
    setStatus("Connected to Wyze — pulling your history…", "info");
    await runSync();
    return;
  }
  if (outcome && outcome.kind === "challenge") {
    openConnectDialog();
    pendingChallenge = outcome.id;
    refs.codePrompt.textContent = outcome.prompt || "Enter the verification code.";
    showConnectStep("code");
    refs.cCode.focus();
    return;
  }
  if (outcome && outcome.kind === "error") {
    openConnectDialog(outcome.message);
    return;
  }

  const canSync = state.connected || state.has_saved_credentials;
  if (!canSync) {
    // Desktop app opens the Wyze sign-in window by itself on first boot.
    if (!state.desktop) openConnectDialog();
    else setStatus("Sign in to Wyze in the other window and create an API key — it closes by itself.", "info");
    return;
  }
  const stale = !state.last_sync_at || Date.now() / 1000 - state.last_sync_at > AUTO_SYNC_AFTER_SECONDS;
  if (stale) await runSync({ quiet: true });
}

setInterval(renderConnection, 60 * 1000);
boot().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
