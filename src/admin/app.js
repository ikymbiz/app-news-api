// Agent Platform Admin SPA
//
// 責務:
//  - Cloudflare Worker (R2 distributor) 経由で artifacts/index.json を取得し一覧表示
//  - Firestore からエクスポートされた job_runs / stage_runs / metrics の
//    静的 JSON ダンプ(artifacts/<app>/meta/runs.json 等)を読み込んで可視化
//
// 本 SPA は認証を持たない「読み取り専用静的表示」であることを前提とし、
// 機密値(APIキー)は一切 HTML/JS に埋め込まない。

const STORAGE_KEY = "agent-admin-config";

const SOURCES_KEY = "agent-admin-sources";

const state = {
  distributorUrl: "",
  app: "news",
  runs: [],
  stages: [],
  metrics: [],
  artifacts: [],
  sources: [],
};

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //

document.addEventListener("DOMContentLoaded", () => {
  restoreConfig();
  restoreSources();
  bindNav();
  bindConfigInputs();
  bindSourcesUI();
  document.getElementById("refresh").addEventListener("click", refreshAll);
  if (state.distributorUrl) {
    refreshAll();
  }
  renderSources();
});

function restoreConfig() {
  try {
    const raw = window.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.distributorUrl = parsed.distributorUrl || "";
    state.app = parsed.app || "news";
    document.getElementById("distributor-url").value = state.distributorUrl;
    document.getElementById("app-select").value = state.app;
  } catch (_e) {
    /* storage unavailable — continue in-memory */
  }
}

function saveConfig() {
  try {
    window.localStorage?.setItem(
      STORAGE_KEY,
      JSON.stringify({ distributorUrl: state.distributorUrl, app: state.app }),
    );
  } catch (_e) {
    /* ignore */
  }
}

function bindNav() {
  document.querySelectorAll("button.nav").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("button.nav").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll("section.view").forEach((v) => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("view-" + btn.dataset.view).classList.add("active");
    });
  });
}

function bindConfigInputs() {
  document.getElementById("distributor-url").addEventListener("change", (e) => {
    state.distributorUrl = e.target.value.trim().replace(/\/+$/, "");
    saveConfig();
  });
  document.getElementById("app-select").addEventListener("change", (e) => {
    state.app = e.target.value;
    saveConfig();
  });
  document.getElementById("filter-status").addEventListener("change", renderRuns);
  document.getElementById("filter-since").addEventListener("change", renderRuns);
}

// --------------------------------------------------------------------------- //
// Data loading
// --------------------------------------------------------------------------- //

async function refreshAll() {
  if (!state.distributorUrl) {
    alert("Distributor URL を設定してください");
    return;
  }
  try {
    await Promise.all([
      loadArtifacts(),
      loadMeta("runs.json", "runs"),
      loadMeta("stages.json", "stages"),
      loadMeta("metrics.json", "metrics"),
    ]);
    renderRuns();
    renderStages();
    renderCost();
    renderArtifacts();
  } catch (err) {
    console.error(err);
    alert("読み込み失敗: " + err.message);
  }
}

async function loadArtifacts() {
  const url = `${state.distributorUrl}/${state.app}/index.json`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`artifacts index: ${resp.status}`);
  const body = await resp.json();
  state.artifacts = body.objects || [];
}

async function loadMeta(filename, key) {
  const url = `${state.distributorUrl}/${state.app}/meta/${filename}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      state[key] = [];
      return;
    }
    const body = await resp.json();
    state[key] = Array.isArray(body) ? body : body.items || [];
  } catch (_e) {
    state[key] = [];
  }
}

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //

function renderRuns() {
  const tbody = document.querySelector("#runs-table tbody");
  tbody.innerHTML = "";
  const statusFilter = document.getElementById("filter-status").value;
  const sinceStr = document.getElementById("filter-since").value;
  const since = sinceStr ? new Date(sinceStr) : null;

  const rows = state.runs.filter((r) => {
    if (statusFilter && r.status !== statusFilter) return false;
    if (since && r.started_at && new Date(r.started_at) < since) return false;
    return true;
  });

  for (const run of rows) {
    const tr = document.createElement("tr");
    tr.className = `status-${run.status || "unknown"}`;
    tr.innerHTML = `
      <td><code>${escapeHtml(run.job_run_id || "-")}</code></td>
      <td>${escapeHtml(run.job_id || "-")}</td>
      <td><span class="badge ${run.status}">${run.status || "-"}</span></td>
      <td>${formatTime(run.started_at)}</td>
      <td>${formatDuration(run.started_at, run.finished_at)}</td>
      <td>$${(run.total_cost_usd || 0).toFixed(4)}</td>
      <td>${(run.stage_count || 0)}</td>
    `;
    tbody.appendChild(tr);
  }
  document.querySelector("#view-runs .empty").style.display = rows.length ? "none" : "block";
}

function renderStages() {
  const tbody = document.querySelector("#stages-table tbody");
  tbody.innerHTML = "";
  for (const s of state.stages) {
    const tr = document.createElement("tr");
    tr.className = `status-${s.status || "unknown"}`;
    tr.innerHTML = `
      <td><code>${escapeHtml(s.job_run_id || "-")}</code></td>
      <td>${escapeHtml(s.stage_id || "-")}</td>
      <td><code>${escapeHtml(s.use || "-")}</code></td>
      <td><span class="badge ${s.status}">${s.status || "-"}</span></td>
      <td>${s.attempt || 1}</td>
      <td>${(s.duration_ms || 0)} ms</td>
      <td class="err">${escapeHtml(s.error_message || "")}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderCost() {
  const now = Date.now();
  const day = 24 * 3600 * 1000;
  let total1 = 0, total7 = 0, total30 = 0;
  const byModel = new Map();

  for (const m of state.metrics) {
    const t = m.recorded_at ? new Date(m.recorded_at).getTime() : now;
    const cost = m.total_cost_usd || 0;
    if (now - t < day) total1 += cost;
    if (now - t < 7 * day) total7 += cost;
    if (now - t < 30 * day) total30 += cost;
    for (const u of m.token_usages || []) {
      const k = u.model || "?";
      const acc = byModel.get(k) || { prompt: 0, completion: 0, cost: 0 };
      acc.prompt += u.prompt_tokens || 0;
      acc.completion += u.completion_tokens || 0;
      acc.cost += u.estimated_cost_usd || 0;
      byModel.set(k, acc);
    }
  }

  document.getElementById("cost-today").textContent = "$" + total1.toFixed(4);
  document.getElementById("cost-7d").textContent = "$" + total7.toFixed(4);
  document.getElementById("cost-30d").textContent = "$" + total30.toFixed(4);

  const tbody = document.querySelector("#cost-table tbody");
  tbody.innerHTML = "";
  for (const [model, v] of byModel) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><code>${escapeHtml(model)}</code></td>
      <td>${v.prompt.toLocaleString()}</td>
      <td>${v.completion.toLocaleString()}</td>
      <td>$${v.cost.toFixed(4)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderArtifacts() {
  const tbody = document.querySelector("#artifacts-table tbody");
  tbody.innerHTML = "";
  for (const a of state.artifacts) {
    const tr = document.createElement("tr");
    const href = `${state.distributorUrl}/${a.key}`;
    tr.innerHTML = `
      <td><code>${escapeHtml(a.key)}</code></td>
      <td>${formatBytes(a.size || 0)}</td>
      <td>${formatTime(a.uploaded)}</td>
      <td><code>${escapeHtml((a.etag || "").slice(0, 12))}</code></td>
      <td><a href="${href}" target="_blank" rel="noopener">open</a></td>
    `;
    tbody.appendChild(tr);
  }
}

// --------------------------------------------------------------------------- //
// Utils
// --------------------------------------------------------------------------- //

function formatTime(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString();
  } catch (_e) {
    return iso;
  }
}

function formatDuration(start, end) {
  if (!start || !end) return "-";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60000).toFixed(1)} min`;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// --------------------------------------------------------------------------- //
// Sources management
// --------------------------------------------------------------------------- //

const CATEGORIES = ["policy", "lab", "papers", "risk", "business", "newsletter", "japan"];
const VIA_OPTIONS = ["rss", "google_news_proxy"];

function restoreSources() {
  try {
    const raw = window.localStorage?.getItem(SOURCES_KEY);
    if (!raw) return;
    state.sources = JSON.parse(raw);
  } catch (_e) {
    /* ignore */
  }
}

function saveSources() {
  try {
    window.localStorage?.setItem(SOURCES_KEY, JSON.stringify(state.sources));
  } catch (_e) {
    /* ignore */
  }
}

function bindSourcesUI() {
  document.getElementById("sources-import").addEventListener("click", () => {
    document.getElementById("sources-file-input").click();
  });
  document.getElementById("sources-file-input").addEventListener("change", handleSourcesImport);
  document.getElementById("sources-export").addEventListener("click", handleSourcesExport);
  document.getElementById("sources-add").addEventListener("click", handleSourcesAdd);
  document.getElementById("sources-filter-category").addEventListener("change", renderSources);
}

function handleSourcesImport(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const data = JSON.parse(ev.target.result);
      const sources = data.sources || data;
      if (!Array.isArray(sources)) {
        alert("Invalid format: expected { sources: [...] } or [...]");
        return;
      }
      state.sources = sources;
      saveSources();
      renderSources();
    } catch (err) {
      alert("JSON parse error: " + err.message);
    }
  };
  reader.readAsText(file);
  e.target.value = "";
}

function handleSourcesExport() {
  if (!state.sources.length) {
    alert("No sources to export.");
    return;
  }
  const data = {
    "$schema_version": "2.0",
    description: "AI OSINT source catalog — exported from Admin UI",
    sources: state.sources,
  };
  const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "sources.json";
  a.click();
  URL.revokeObjectURL(url);
}

function handleSourcesAdd() {
  const newSource = {
    id: "new-source-" + Date.now(),
    name: "",
    category: "policy",
    feed_url: "",
    language: "en",
    enabled: true,
  };
  state.sources.push(newSource);
  saveSources();
  renderSources();
  // Scroll to the new row
  const tbody = document.querySelector("#sources-table tbody");
  if (tbody.lastElementChild) {
    tbody.lastElementChild.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function handleSourceDelete(index) {
  const src = state.sources[index];
  if (!confirm(`Delete source "${src.name || src.id}"?`)) return;
  state.sources.splice(index, 1);
  saveSources();
  renderSources();
}

function handleSourceChange(index, field, value) {
  if (field === "enabled") {
    state.sources[index].enabled = value;
  } else {
    state.sources[index][field] = value;
  }
  // Update via field: set or remove based on whether it looks like a google news proxy
  if (field === "feed_url") {
    if (value.includes("news.google.com/rss/search")) {
      state.sources[index].via = "google_news_proxy";
    } else if (state.sources[index].via === "google_news_proxy") {
      delete state.sources[index].via;
    }
  }
  saveSources();
}

function renderSources() {
  const tbody = document.querySelector("#sources-table tbody");
  tbody.innerHTML = "";
  const catFilter = document.getElementById("sources-filter-category").value;

  const filtered = state.sources
    .map((s, i) => ({ ...s, _index: i }))
    .filter((s) => !catFilter || s.category === catFilter);

  for (const src of filtered) {
    const idx = src._index;
    const tr = document.createElement("tr");
    if (!src.enabled) tr.className = "source-disabled";

    // Enabled checkbox
    const tdEnabled = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = src.enabled !== false;
    checkbox.addEventListener("change", () => {
      handleSourceChange(idx, "enabled", checkbox.checked);
      tr.className = checkbox.checked ? "" : "source-disabled";
    });
    tdEnabled.appendChild(checkbox);
    tr.appendChild(tdEnabled);

    // ID
    const tdId = document.createElement("td");
    const inputId = document.createElement("input");
    inputId.type = "text";
    inputId.value = src.id || "";
    inputId.addEventListener("change", () => handleSourceChange(idx, "id", inputId.value.trim()));
    tdId.appendChild(inputId);
    tr.appendChild(tdId);

    // Name
    const tdName = document.createElement("td");
    const inputName = document.createElement("input");
    inputName.type = "text";
    inputName.value = src.name || "";
    inputName.addEventListener("change", () => handleSourceChange(idx, "name", inputName.value.trim()));
    tdName.appendChild(inputName);
    tr.appendChild(tdName);

    // Category
    const tdCat = document.createElement("td");
    const selectCat = document.createElement("select");
    for (const cat of CATEGORIES) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      if (cat === src.category) opt.selected = true;
      selectCat.appendChild(opt);
    }
    selectCat.addEventListener("change", () => handleSourceChange(idx, "category", selectCat.value));
    tdCat.appendChild(selectCat);
    tr.appendChild(tdCat);

    // Feed URL
    const tdUrl = document.createElement("td");
    tdUrl.className = "feed-url-cell";
    const inputUrl = document.createElement("input");
    inputUrl.type = "url";
    inputUrl.value = src.feed_url || "";
    inputUrl.placeholder = "https://example.com/rss.xml";
    inputUrl.addEventListener("change", () => handleSourceChange(idx, "feed_url", inputUrl.value.trim()));
    tdUrl.appendChild(inputUrl);
    tr.appendChild(tdUrl);

    // Via
    const tdVia = document.createElement("td");
    tdVia.innerHTML = `<code>${escapeHtml(src.via || "rss")}</code>`;
    tr.appendChild(tdVia);

    // Language
    const tdLang = document.createElement("td");
    const selectLang = document.createElement("select");
    for (const lang of ["en", "ja"]) {
      const opt = document.createElement("option");
      opt.value = lang;
      opt.textContent = lang;
      if (lang === src.language) opt.selected = true;
      selectLang.appendChild(opt);
    }
    selectLang.addEventListener("change", () => handleSourceChange(idx, "language", selectLang.value));
    tdLang.appendChild(selectLang);
    tr.appendChild(tdLang);

    // Actions
    const tdActions = document.createElement("td");
    tdActions.className = "actions-cell";
    const btnDelete = document.createElement("button");
    btnDelete.className = "btn-danger";
    btnDelete.textContent = "Delete";
    btnDelete.addEventListener("click", () => handleSourceDelete(idx));
    tdActions.appendChild(btnDelete);
    tr.appendChild(tdActions);

    tbody.appendChild(tr);
  }

  document.getElementById("sources-empty").style.display = filtered.length ? "none" : "block";
}
