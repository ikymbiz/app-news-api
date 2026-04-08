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

const state = {
  distributorUrl: "",
  app: "news",
  runs: [],
  stages: [],
  metrics: [],
  artifacts: [],
};

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //

document.addEventListener("DOMContentLoaded", () => {
  restoreConfig();
  bindNav();
  bindConfigInputs();
  document.getElementById("refresh").addEventListener("click", refreshAll);
  if (state.distributorUrl) {
    refreshAll();
  }
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
