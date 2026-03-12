const metricLabels = {
  weight_lb: "Weight (lb)",
  weight_kg: "Weight (kg)",
  bmi: "BMI",
  body_fat: "Body Fat (%)",
  muscle: "Muscle",
  body_water: "Body Water (%)",
  protein: "Protein (%)",
  bone_mineral: "Bone Mineral",
  body_vfr: "Visceral Fat",
  bmr: "BMR",
  metabolic_age: "Metabolic Age",
};

const refs = {
  syncNow: document.getElementById("syncNow"),
  importNow: document.getElementById("importNow"),
  importFile: document.getElementById("importFile"),
  exportNow: document.getElementById("exportNow"),
  exportRange: document.getElementById("exportRange"),
  profileSelect: document.getElementById("profileSelect"),
  lookbackDays: document.getElementById("lookbackDays"),
  statusBar: document.getElementById("statusBar"),
  metricSelect: document.getElementById("metricSelect"),
  daysSelect: document.getElementById("daysSelect"),
  latestWeight: document.getElementById("latestWeight"),
  latestBmi: document.getElementById("latestBmi"),
  latestBodyFat: document.getElementById("latestBodyFat"),
  latestBodyWater: document.getElementById("latestBodyWater"),
  rowCount: document.getElementById("rowCount"),
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

function formatNum(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toFixed(digits);
}

function formatWhen(row) {
  if (!row.measure_ts) {
    return row.measured_at || "-";
  }
  return new Date(row.measure_ts * 1000).toLocaleString();
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

function renderTable() {
  const recent = [...allRows].reverse().slice(0, 60);
  refs.tableBody.innerHTML = "";
  refs.rowCount.textContent = `${allRows.length} rows`;
  for (const row of recent) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${formatWhen(row)}</td>
      <td>${profileLabel(row)}</td>
      <td>${formatNum(row.weight_lb, 1)}</td>
      <td>${formatNum(row.bmi, 1)}</td>
      <td>${formatNum(row.body_fat, 1)}</td>
      <td>${formatNum(row.muscle, 1)}</td>
      <td>${formatNum(row.body_water, 1)}</td>
      <td>${formatNum(row.protein, 1)}</td>
    `;
    refs.tableBody.appendChild(tr);
  }
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
      animation: {
        duration: 420,
      },
      plugins: {
        legend: {
          display: false,
        },
      },
      scales: {
        x: {
          ticks: {
            color: "#5b6872",
            maxTicksLimit: 10,
          },
          grid: {
            color: "rgba(91,104,114,0.1)",
          },
        },
        y: {
          ticks: {
            color: "#5b6872",
          },
          grid: {
            color: "rgba(91,104,114,0.1)",
          },
        },
      },
    },
  });
}

function selectedProfile() {
  return (refs.profileSelect.value || "").trim();
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
  if (current && profiles.some((p) => p.profile_id === current)) {
    refs.profileSelect.value = current;
  }
}

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
  renderTable();
  renderChart();
  if (!allRows.length) {
    setStatus("No data for this profile/range yet. Try Sync or Import.", "info");
  } else {
    const latest = latestRow();
    setStatus(`Loaded ${allRows.length} measurements. Latest: ${formatWhen(latest)}`, "info");
  }
}

async function runSync() {
  refs.syncNow.disabled = true;
  const lookback = Math.max(1, Number(refs.lookbackDays.value || "365"));
  setStatus("Syncing from Wyze...", "info");
  try {
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lookback_days: lookback }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(payload.error || `Sync failed (${res.status})`);
    }
    const result = payload.result || {};
    await refreshUi({ reloadProfiles: true });
    const errCount = (result.scale_errors || []).length;
    const modelInfo = (result.models_attempted || []).join(", ");
    const baseMessage = `Sync complete: ${result.inserted || 0} new / ${result.skipped || 0} existing (${result.records_seen || 0} seen, scales listed: ${result.scales_found || 0}${modelInfo ? `, models: ${modelInfo}` : ""}).`;
    const hint = (result.hints || [])[0];
    setStatus(
      errCount
        ? `${baseMessage} ${errCount} model call(s) had errors.${hint ? ` ${hint}` : ""}`
        : `${baseMessage}${hint ? ` ${hint}` : ""}`,
      errCount ? "error" : "info"
    );
  } catch (err) {
    setStatus(`Sync error: ${err.message}`, "error");
  } finally {
    refs.syncNow.disabled = false;
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
  const qs = new URLSearchParams();
  qs.set("range", refs.exportRange.value || "all");
  const profile = selectedProfile();
  if (profile) {
    qs.set("profile", profile);
  }
  window.location.href = `/api/export?${qs.toString()}`;
}

refs.syncNow.addEventListener("click", runSync);
refs.importNow.addEventListener("click", runImport);
refs.exportNow.addEventListener("click", runExport);
refs.metricSelect.addEventListener("change", renderChart);
refs.daysSelect.addEventListener("change", () => {
  refreshUi().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
});
refs.profileSelect.addEventListener("change", () => {
  refreshUi().catch((err) => setStatus(`Load error: ${err.message}`, "error"));
});

refreshUi({ reloadProfiles: true }).catch((err) => setStatus(`Load error: ${err.message}`, "error"));
