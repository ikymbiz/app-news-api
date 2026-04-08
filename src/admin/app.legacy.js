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
const PAT_STORAGE_KEY = "agent-admin-github-pat";

const state = {
  distributorUrl: "",
  githubRepo: "",
  app: "news",
  runs: [],
  stages: [],
  metrics: [],
  artifacts: [],
  sources: [],
  rawPipeline: "",
  rawJobs: "",
  rawRuntime: "",
  runtime: null,
  runtimeSha: null,
  // News tab
  news: null,
  // Prompts CRUD
  promptsList: [],          // [{ name, path, sha }]
  currentPrompt: null,      // { name, path, sha, frontmatter, body }
  promptIsNew: false,
  // Pipeline editor
  pipelineYaml: null,       // parsed object
  pipelineSha: null,
  pipelineStagesEdited: [], // working copy of stages array
  // Catalogs
  modelsCatalog: [],        // [{ id, display_name, provider, ... }]
  stagesCatalog: [],        // [{ use, display_name, category, ... }]
};

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //

document.addEventListener("DOMContentLoaded", () => {
  restoreConfig();
  bindNav();
  bindConfigInputs();
  bindSettingsFilters();
  bindRuntimeForm();
  bindNewsControls();
  bindPromptsUI();
  bindPipelineEditorUI();
  document.getElementById("refresh").addEventListener("click", refreshAll);
  if (state.distributorUrl || state.githubRepo) {
    refreshAll();
  }
});

function restoreConfig() {
  try {
    const raw = window.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.distributorUrl = parsed.distributorUrl || "";
    state.githubRepo = parsed.githubRepo || "";
    state.app = parsed.app || "news";
    document.getElementById("distributor-url").value = state.distributorUrl;
    document.getElementById("github-repo").value = state.githubRepo;
    document.getElementById("app-select").value = state.app;
  } catch (_e) {
    /* storage unavailable — continue in-memory */
  }
}

function saveConfig() {
  try {
    window.localStorage?.setItem(
      STORAGE_KEY,
      JSON.stringify({
        distributorUrl: state.distributorUrl,
        githubRepo: state.githubRepo,
        app: state.app,
      }),
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
  document.getElementById("github-repo").addEventListener("change", (e) => {
    state.githubRepo = e.target.value.trim().replace(/^\/+|\/+$/g, "");
    saveConfig();
    updateEditLinks();
  });
  document.getElementById("app-select").addEventListener("change", (e) => {
    state.app = e.target.value;
    saveConfig();
  });
  document.getElementById("filter-status").addEventListener("change", renderRuns);
  document.getElementById("filter-since").addEventListener("change", renderRuns);
}

function bindSettingsFilters() {
  document.getElementById("src-category-filter").addEventListener("change", renderSources);
  document.getElementById("src-status-filter").addEventListener("change", renderSources);
}

function bindRuntimeForm() {
  // Restore PAT (separate key, not bundled with config).
  try {
    const pat = window.localStorage?.getItem(PAT_STORAGE_KEY) || "";
    document.getElementById("rt-github-pat").value = pat;
  } catch (_e) { /* ignore */ }

  document.getElementById("rt-github-pat").addEventListener("change", (e) => {
    try {
      window.localStorage?.setItem(PAT_STORAGE_KEY, e.target.value.trim());
    } catch (_e) { /* ignore */ }
  });
  document.getElementById("rt-save-btn").addEventListener("click", saveRuntime);
  document.getElementById("rt-reload-btn").addEventListener("click", () => {
    state.runtimeSha = null;
    loadSettings().then(() => renderRawFiles()).catch((e) => setRuntimeStatus("reload失敗: " + e.message, "err"));
  });
}

function populateRuntimeForm() {
  const rt = state.runtime || {};
  const collect = rt.collect || {};
  const report = rt.report || {};
  const days = collect.max_age_days;
  const drop = !!collect.drop_undated;
  const thr = report.high_value_threshold;
  document.getElementById("rt-max-age-days").value = days != null ? days : "";
  document.getElementById("rt-drop-undated").checked = drop;
  document.getElementById("rt-high-threshold").value = thr != null ? thr : "";
}

function setRuntimeStatus(msg, kind) {
  const el = document.getElementById("rt-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "rt-status " + (kind || "");
}

async function saveRuntime() {
  const repo = state.githubRepo;
  if (!repo) {
    setRuntimeStatus("GitHub Repo を先に入力してください", "err");
    return;
  }
  const pat = (document.getElementById("rt-github-pat").value || "").trim();
  if (!pat) {
    setRuntimeStatus("GitHub PAT が必要です", "err");
    return;
  }

  const days = parseInt(document.getElementById("rt-max-age-days").value, 10);
  const drop = document.getElementById("rt-drop-undated").checked;
  const thr = parseFloat(document.getElementById("rt-high-threshold").value);
  if (Number.isNaN(days) || days < 0) {
    setRuntimeStatus("取得期間は 0 以上の整数で", "err");
    return;
  }
  if (Number.isNaN(thr) || thr < 0 || thr > 10) {
    setRuntimeStatus("閾値は 0〜10 の数値で", "err");
    return;
  }

  const next = {
    _comment: "Edited from Admin SPA. Picked up by next agent-platform run.",
    collect: { max_age_days: days, drop_undated: drop },
    report: { high_value_threshold: thr },
  };
  const nextText = JSON.stringify(next, null, 2) + "\n";

  setRuntimeStatus("保存中…", "");
  const apiUrl = `https://api.github.com/repos/${repo}/contents/config/runtime.json`;
  const headers = {
    Authorization: `Bearer ${pat}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  try {
    // GET current SHA (required by Contents API for updates).
    let sha = state.runtimeSha;
    if (!sha) {
      const getResp = await fetch(apiUrl + "?ref=main", { headers });
      if (getResp.ok) {
        const meta = await getResp.json();
        sha = meta.sha;
      } else if (getResp.status !== 404) {
        throw new Error(`GET ${getResp.status}`);
      }
    }

    const body = {
      message: `chore(runtime): update via Admin SPA (max_age_days=${days}, threshold=${thr})`,
      content: btoa(unescape(encodeURIComponent(nextText))),
      branch: "main",
    };
    if (sha) body.sha = sha;

    const putResp = await fetch(apiUrl, {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!putResp.ok) {
      const errText = await putResp.text();
      throw new Error(`PUT ${putResp.status}: ${errText.slice(0, 200)}`);
    }
    const result = await putResp.json();
    state.runtimeSha = result.content?.sha || null;
    state.runtime = next;
    state.rawRuntime = nextText;
    renderRawFiles();
    setRuntimeStatus("✓ 保存しました。次の agent-platform 実行から反映されます", "ok");
  } catch (e) {
    console.error(e);
    setRuntimeStatus("保存失敗: " + e.message, "err");
  }
}

// --------------------------------------------------------------------------- //
// Data loading
// --------------------------------------------------------------------------- //

async function refreshAll() {
  const tasks = [];
  if (state.distributorUrl) {
    tasks.push(
      loadArtifacts(),
      loadMeta("runs.json", "runs"),
      loadMeta("stages.json", "stages"),
      loadMeta("metrics.json", "metrics"),
      loadNews(),
    );
  }
  if (state.githubRepo) {
    tasks.push(
      loadSettings(),
      loadCatalogs(),
      loadPromptsList(),
      loadPipelineForEditor(),
    );
  }
  if (tasks.length === 0) {
    alert("配信URL または GitHubリポジトリ を設定してください");
    return;
  }
  try {
    await Promise.all(tasks);
    renderRuns();
    renderStages();
    renderCost();
    renderArtifacts();
    renderSources();
    renderRawFiles();
    updateEditLinks();
    renderNews();
    renderPromptsList();
    renderPipelineEditor();
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

async function loadSettings() {
  const repo = state.githubRepo;
  if (!repo) return;
  const rawBase = `https://raw.githubusercontent.com/${repo}/main`;

  const fetchText = async (path) => {
    try {
      const resp = await fetch(`${rawBase}/${path}`);
      if (!resp.ok) return `(failed to fetch ${path}: HTTP ${resp.status})`;
      return await resp.text();
    } catch (e) {
      return `(network error fetching ${path}: ${e.message})`;
    }
  };

  const [sourcesText, pipelineText, jobsText, runtimeText] = await Promise.all([
    fetchText("config/sources.json"),
    fetchText(`src/apps/${state.app}/pipeline.yml`),
    fetchText("config/jobs.yml"),
    fetchText("config/runtime.json"),
  ]);

  try {
    const parsed = JSON.parse(sourcesText);
    state.sources = parsed.sources || [];
  } catch (_e) {
    state.sources = [];
  }
  state.rawPipeline = pipelineText;
  state.rawJobs = jobsText;
  state.rawRuntime = runtimeText;
  try {
    state.runtime = JSON.parse(runtimeText);
  } catch (_e) {
    state.runtime = null;
  }
  populateRuntimeForm();
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

function renderSources() {
  const tbody = document.querySelector("#sources-table tbody");
  const empty = document.getElementById("src-empty");
  const countEl = document.getElementById("src-count");
  tbody.innerHTML = "";

  // Populate category filter once.
  const catFilter = document.getElementById("src-category-filter");
  const existingCats = new Set(
    [...catFilter.options].map((o) => o.value).filter(Boolean),
  );
  for (const s of state.sources) {
    if (s.category && !existingCats.has(s.category)) {
      const opt = document.createElement("option");
      opt.value = s.category;
      opt.textContent = s.category;
      catFilter.appendChild(opt);
      existingCats.add(s.category);
    }
  }

  const catVal = catFilter.value;
  const statusVal = document.getElementById("src-status-filter").value;

  const filtered = state.sources.filter((s) => {
    if (catVal && s.category !== catVal) return false;
    if (statusVal === "enabled" && s.enabled === false) return false;
    if (statusVal === "disabled" && s.enabled !== false) return false;
    return true;
  });

  countEl.textContent = `(${filtered.length} / ${state.sources.length})`;

  if (state.sources.length === 0) {
    empty.style.display = "block";
    empty.textContent = state.githubRepo
      ? "No sources loaded. Check the GitHub Repo input and click Refresh."
      : "Set GitHub Repo above and click Refresh to load sources.";
    return;
  }
  empty.style.display = "none";

  for (const s of filtered) {
    const tr = document.createElement("tr");
    tr.className = s.enabled === false ? "src-disabled" : "src-enabled";
    const urlText = (s.feed_url || "").replace(/^https?:\/\//, "");
    const urlShort = urlText.length > 60 ? urlText.slice(0, 57) + "…" : urlText;
    tr.innerHTML = `
      <td><code>${escapeHtml(s.id || "")}</code></td>
      <td>${escapeHtml(s.name || "")}</td>
      <td><span class="cat cat-${escapeHtml(s.category || "")}">${escapeHtml(s.category || "-")}</span></td>
      <td>${escapeHtml(s.via || "rss")}</td>
      <td>${s.enabled === false ? "✗" : "✓"}</td>
      <td><code title="${escapeHtml(s.feed_url || "")}">${escapeHtml(urlShort)}</code></td>
    `;
    tbody.appendChild(tr);
  }
}

function renderRawFiles() {
  document.getElementById("pipeline-raw").textContent = state.rawPipeline || "(not loaded)";
  document.getElementById("jobs-raw").textContent = state.rawJobs || "(not loaded)";
  const rt = document.getElementById("runtime-raw");
  if (rt) rt.textContent = state.rawRuntime || "(not loaded)";
}

function updateEditLinks() {
  const repo = state.githubRepo;
  const blobBase = repo ? `https://github.com/${repo}/edit/main` : "#";
  const setLink = (id, path) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.href = repo ? `${blobBase}/${path}` : "#";
    el.style.opacity = repo ? "1" : "0.4";
    el.style.pointerEvents = repo ? "auto" : "none";
  };
  setLink("src-edit-link", "config/sources.json");
  setLink("pipe-edit-link", `src/apps/${state.app}/pipeline.yml`);
  setLink("jobs-edit-link", "config/jobs.yml");
  setLink("runtime-edit-link", "config/runtime.json");
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
// GitHub Contents API helpers (REQUIREMENTS.md §1.2 / §1.3)
// --------------------------------------------------------------------------- //

function ghPat() {
  return (document.getElementById("rt-github-pat")?.value || "").trim();
}

function ghHeaders() {
  const pat = ghPat();
  if (!pat) throw new Error("GitHub アクセストークンが未設定です(動作チューニング欄)");
  return {
    Authorization: `Bearer ${pat}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function ghContentsUrl(path) {
  return `https://api.github.com/repos/${state.githubRepo}/contents/${path}`;
}

async function ghGet(path) {
  const resp = await fetch(ghContentsUrl(path) + "?ref=main", { headers: ghHeaders() });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GET ${path} → ${resp.status}`);
  return await resp.json();
}

async function ghList(path) {
  return await ghGet(path);
}

async function ghPut(path, contentText, sha, message) {
  const body = {
    message,
    content: btoa(unescape(encodeURIComponent(contentText))),
    branch: "main",
  };
  if (sha) body.sha = sha;
  const resp = await fetch(ghContentsUrl(path), {
    method: "PUT",
    headers: { ...ghHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PUT ${path} → ${resp.status}: ${(await resp.text()).slice(0, 200)}`);
  return await resp.json();
}

async function ghDelete(path, sha, message) {
  const resp = await fetch(ghContentsUrl(path), {
    method: "DELETE",
    headers: { ...ghHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ message, sha, branch: "main" }),
  });
  if (!resp.ok) throw new Error(`DELETE ${path} → ${resp.status}`);
  return await resp.json();
}

function decodeContentBase64(b64) {
  return decodeURIComponent(escape(atob(b64.replace(/\n/g, ""))));
}

// --------------------------------------------------------------------------- //
// Catalogs (models.yml + stages.catalog.yml)
// --------------------------------------------------------------------------- //

async function loadCatalogs() {
  const repo = state.githubRepo;
  if (!repo) return;
  const rawBase = `https://raw.githubusercontent.com/${repo}/main`;
  try {
    const [modelsTxt, stagesTxt] = await Promise.all([
      fetch(`${rawBase}/config/models.yml`).then((r) => r.ok ? r.text() : ""),
      fetch(`${rawBase}/config/stages.catalog.yml`).then((r) => r.ok ? r.text() : ""),
    ]);
    if (modelsTxt && window.jsyaml) {
      const doc = window.jsyaml.load(modelsTxt);
      state.modelsCatalog = (doc && doc.models) || [];
    }
    if (stagesTxt && window.jsyaml) {
      const doc = window.jsyaml.load(stagesTxt);
      state.stagesCatalog = (doc && doc.stages) || [];
    }
  } catch (e) {
    console.error("loadCatalogs failed", e);
  }
  populatePromptModelDropdown();
  populatePipelineAddDropdown();
}

function populatePromptModelDropdown() {
  const sel = document.getElementById("prompt-model");
  if (!sel) return;
  sel.innerHTML = "";
  for (const m of state.modelsCatalog) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = `${m.display_name || m.id} (${m.provider || ""})`;
    sel.appendChild(opt);
  }
}

function populatePipelineAddDropdown() {
  const sel = document.getElementById("pipeline-add-select");
  if (!sel) return;
  sel.innerHTML = '<option value="">— ステージ部品を選択 —</option>';
  for (const s of state.stagesCatalog) {
    const opt = document.createElement("option");
    opt.value = s.use;
    opt.textContent = `[${s.category}] ${s.display_name || s.use}`;
    sel.appendChild(opt);
  }
}

// --------------------------------------------------------------------------- //
// News tab (REQUIREMENTS.md §1.4)
// --------------------------------------------------------------------------- //

function bindNewsControls() {
  document.getElementById("news-min-score").addEventListener("change", renderNews);
  document.getElementById("news-category-filter").addEventListener("change", renderNews);
  document.getElementById("news-sort").addEventListener("change", renderNews);
}

async function loadNews() {
  if (!state.distributorUrl) return;
  try {
    const url = `${state.distributorUrl}/${state.app}/current_news.json`;
    const resp = await fetch(url);
    if (!resp.ok) {
      state.news = null;
      return;
    }
    state.news = await resp.json();
  } catch (e) {
    console.error("loadNews failed", e);
    state.news = null;
  }
}

function renderNews() {
  const container = document.getElementById("news-list");
  const empty = document.getElementById("news-empty");
  const meta = document.getElementById("news-meta");
  container.innerHTML = "";
  if (!state.news || !Array.isArray(state.news.items)) {
    empty.style.display = "block";
    meta.textContent = "";
    return;
  }
  empty.style.display = "none";

  // Populate category filter once
  const catSel = document.getElementById("news-category-filter");
  const existing = new Set([...catSel.options].map((o) => o.value).filter(Boolean));
  for (const it of state.news.items) {
    const cat = (it.raw && it.raw.category) || it.category || "";
    if (cat && !existing.has(cat)) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      catSel.appendChild(opt);
      existing.add(cat);
    }
  }

  const minScore = parseFloat(document.getElementById("news-min-score").value) || 0;
  const catFilter = catSel.value;
  const sort = document.getElementById("news-sort").value;

  let items = state.news.items.filter((it) => {
    const score = it.score != null ? it.score : 0;
    if (score < minScore) return false;
    if (catFilter) {
      const c = (it.raw && it.raw.category) || it.category || "";
      if (c !== catFilter) return false;
    }
    return true;
  });

  if (sort === "score-desc") items.sort((a, b) => (b.score || 0) - (a.score || 0));
  else if (sort === "score-asc") items.sort((a, b) => (a.score || 0) - (b.score || 0));
  else if (sort === "date-desc") items.sort((a, b) => String(b.published_at || "").localeCompare(String(a.published_at || "")));

  meta.textContent = `${items.length} 件 / 全 ${state.news.items.length} 件 ・ 生成: ${formatTime(state.news.generated_at)}`;

  for (const it of items) {
    const card = document.createElement("article");
    card.className = "news-card";
    const score = it.score != null ? it.score.toFixed(1) : "—";
    const star = (it.score || 0) >= 8.0 ? "★ " : "";
    const cat = (it.raw && it.raw.category) || it.category || "";
    const sourceName = (it.raw && it.raw.source_name) || it.source || "";
    const topics = Array.isArray(it.topics) ? it.topics : [];
    card.innerHTML = `
      <div class="news-card-head">
        <span class="news-score">${star}${score}</span>
        <span class="cat cat-${escapeHtml(cat)}">${escapeHtml(cat || "-")}</span>
      </div>
      <h3 class="news-title"><a href="${escapeHtml(it.url || "#")}" target="_blank" rel="noopener">${escapeHtml(it.title || "(no title)")}</a></h3>
      <div class="news-source">${escapeHtml(sourceName)} ・ ${escapeHtml(it.published_at || "")}</div>
      ${it.reason ? `<p class="news-reason">${escapeHtml(it.reason)}</p>` : ""}
      ${topics.length ? `<div class="news-topics">${topics.map((t) => `<span class="topic">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
    `;
    container.appendChild(card);
  }
}

// --------------------------------------------------------------------------- //
// Prompts CRUD (REQUIREMENTS.md §1.1 / §1.2)
// --------------------------------------------------------------------------- //

function bindPromptsUI() {
  document.getElementById("prompt-new-btn").addEventListener("click", newPrompt);
  document.getElementById("prompt-save-btn").addEventListener("click", savePrompt);
  document.getElementById("prompt-cancel-btn").addEventListener("click", closePromptEditor);
  document.getElementById("prompt-delete-btn").addEventListener("click", deletePrompt);
}

async function loadPromptsList() {
  state.promptsList = [];
  if (!state.githubRepo) return;
  try {
    // Public list via raw is impossible; use Contents API (no auth required for public repos).
    const url = `https://api.github.com/repos/${state.githubRepo}/contents/src/apps/${state.app}/prompts?ref=main`;
    const resp = await fetch(url, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!resp.ok) {
      console.warn("loadPromptsList:", resp.status);
      return;
    }
    const items = await resp.json();
    state.promptsList = (Array.isArray(items) ? items : [])
      .filter((it) => it.type === "file" && it.name.endsWith(".md"))
      .map((it) => ({ name: it.name, path: it.path, sha: it.sha }));
  } catch (e) {
    console.error("loadPromptsList failed", e);
  }
}

function renderPromptsList() {
  const ul = document.getElementById("prompts-ul");
  const empty = document.getElementById("prompts-empty");
  const count = document.getElementById("prompts-count");
  ul.innerHTML = "";
  count.textContent = state.promptsList.length ? `(${state.promptsList.length})` : "";
  if (!state.promptsList.length) {
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";
  for (const p of state.promptsList) {
    const li = document.createElement("li");
    li.className = "prompt-item";
    li.innerHTML = `<button type="button" class="prompt-open">${escapeHtml(p.name)}</button>`;
    li.querySelector(".prompt-open").addEventListener("click", () => openPromptEditor(p));
    ul.appendChild(li);
  }
}

async function openPromptEditor(p) {
  setPromptStatus("読み込み中…", "");
  try {
    const meta = await ghGet(p.path);
    if (!meta) throw new Error("not found");
    const text = decodeContentBase64(meta.content);
    const { fm, body } = splitFrontmatterClient(text);
    state.currentPrompt = {
      name: p.name,
      path: p.path,
      sha: meta.sha,
      frontmatter: fm || {},
      body,
    };
    state.promptIsNew = false;
    fillPromptEditor();
    document.getElementById("prompt-editor").hidden = false;
    document.getElementById("prompt-delete-btn").hidden = false;
    setPromptStatus("", "");
  } catch (e) {
    setPromptStatus("読み込み失敗: " + e.message, "err");
  }
}

function newPrompt() {
  state.currentPrompt = {
    name: "new_prompt.md",
    path: `src/apps/${state.app}/prompts/new_prompt.md`,
    sha: null,
    frontmatter: {
      id: "new_prompt",
      description: "",
      model: state.modelsCatalog[0]?.id || "gemini-2.5-flash-lite",
      temperature: 0.0,
      response_mime_type: "application/json",
    },
    body: "# New Prompt\n\nここに本文を記述してください。\n",
  };
  state.promptIsNew = true;
  fillPromptEditor();
  document.getElementById("prompt-editor").hidden = false;
  document.getElementById("prompt-delete-btn").hidden = true;
  setPromptStatus("", "");
}

function fillPromptEditor() {
  const p = state.currentPrompt;
  if (!p) return;
  document.getElementById("prompt-filename").value = p.name;
  document.getElementById("prompt-filename").disabled = !state.promptIsNew;
  document.getElementById("prompt-description").value = p.frontmatter.description || "";
  document.getElementById("prompt-model").value = p.frontmatter.model || "";
  document.getElementById("prompt-temperature").value = p.frontmatter.temperature ?? 0.0;
  document.getElementById("prompt-body").value = p.body || "";
}

function closePromptEditor() {
  state.currentPrompt = null;
  state.promptIsNew = false;
  document.getElementById("prompt-editor").hidden = true;
  setPromptStatus("", "");
}

async function savePrompt() {
  if (!state.currentPrompt) return;
  if (!state.githubRepo) { setPromptStatus("GitHubリポジトリ未設定", "err"); return; }
  try {
    const filename = document.getElementById("prompt-filename").value.trim();
    if (!/^[\w\-.]+\.md$/.test(filename)) {
      setPromptStatus("ファイル名は半角英数 + .md", "err");
      return;
    }
    const fm = {
      id: filename.replace(/\.md$/, ""),
      description: document.getElementById("prompt-description").value.trim(),
      model: document.getElementById("prompt-model").value,
      temperature: parseFloat(document.getElementById("prompt-temperature").value) || 0,
      response_mime_type: state.currentPrompt.frontmatter.response_mime_type || "application/json",
    };
    const body = document.getElementById("prompt-body").value;
    const fullText = serializeFrontmatterClient(fm, body);
    const path = state.promptIsNew
      ? `src/apps/${state.app}/prompts/${filename}`
      : state.currentPrompt.path;
    setPromptStatus("保存中…", "");
    const result = await ghPut(
      path,
      fullText,
      state.promptIsNew ? null : state.currentPrompt.sha,
      `chore(prompt): ${state.promptIsNew ? "create" : "update"} ${filename} via Admin SPA`,
    );
    state.currentPrompt.sha = result.content?.sha || null;
    state.currentPrompt.path = path;
    state.currentPrompt.name = filename;
    state.promptIsNew = false;
    setPromptStatus("✓ 保存しました", "ok");
    await loadPromptsList();
    renderPromptsList();
  } catch (e) {
    setPromptStatus("保存失敗: " + e.message, "err");
  }
}

async function deletePrompt() {
  if (!state.currentPrompt || state.promptIsNew) return;
  if (!confirm(`${state.currentPrompt.name} を削除しますか?この操作は元に戻せません。`)) return;
  try {
    setPromptStatus("削除中…", "");
    await ghDelete(
      state.currentPrompt.path,
      state.currentPrompt.sha,
      `chore(prompt): delete ${state.currentPrompt.name} via Admin SPA`,
    );
    closePromptEditor();
    setPromptStatus("✓ 削除しました", "ok");
    await loadPromptsList();
    renderPromptsList();
  } catch (e) {
    setPromptStatus("削除失敗: " + e.message, "err");
  }
}

function setPromptStatus(msg, kind) {
  const el = document.getElementById("prompt-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "rt-status " + (kind || "");
}

function splitFrontmatterClient(text) {
  if (!text.startsWith("---")) return { fm: null, body: text };
  const m = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n/);
  if (!m) return { fm: null, body: text };
  const body = text.slice(m[0].length);
  let fm = null;
  if (window.jsyaml) {
    try { fm = window.jsyaml.load(m[1]); } catch (_e) { fm = null; }
  }
  return { fm: fm || {}, body };
}

function serializeFrontmatterClient(fm, body) {
  let yamlText = "";
  if (window.jsyaml) {
    yamlText = window.jsyaml.dump(fm, { lineWidth: 200 });
  } else {
    yamlText = Object.entries(fm).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n") + "\n";
  }
  return `---\n${yamlText}---\n\n${body}`;
}

// --------------------------------------------------------------------------- //
// Pipeline editor (REQUIREMENTS.md §1.3)
// --------------------------------------------------------------------------- //

function bindPipelineEditorUI() {
  document.getElementById("pipeline-add-btn").addEventListener("click", addPipelineStage);
  document.getElementById("pipeline-save-btn").addEventListener("click", savePipeline);
  document.getElementById("pipeline-reset-btn").addEventListener("click", () => {
    resetPipelineEdited();
    renderPipelineEditor();
    setPipelineStatus("変更を破棄しました", "");
  });
}

async function loadPipelineForEditor() {
  if (!state.githubRepo) return;
  try {
    const meta = await fetch(
      `https://api.github.com/repos/${state.githubRepo}/contents/src/apps/${state.app}/pipeline.yml?ref=main`,
      { headers: { Accept: "application/vnd.github+json" } },
    );
    if (!meta.ok) return;
    const j = await meta.json();
    state.pipelineSha = j.sha;
    const text = decodeContentBase64(j.content);
    if (window.jsyaml) {
      try {
        state.pipelineYaml = window.jsyaml.load(text);
      } catch (e) {
        console.error("pipeline.yml parse failed", e);
        state.pipelineYaml = null;
      }
    }
    resetPipelineEdited();
  } catch (e) {
    console.error("loadPipelineForEditor failed", e);
  }
}

function resetPipelineEdited() {
  if (state.pipelineYaml && Array.isArray(state.pipelineYaml.stages)) {
    state.pipelineStagesEdited = JSON.parse(JSON.stringify(state.pipelineYaml.stages));
  } else {
    state.pipelineStagesEdited = [];
  }
}

function renderPipelineEditor() {
  const ol = document.getElementById("pipeline-stages");
  if (!ol) return;
  ol.innerHTML = "";
  for (let i = 0; i < state.pipelineStagesEdited.length; i++) {
    const st = state.pipelineStagesEdited[i];
    const li = document.createElement("li");
    li.className = "pipeline-stage";
    li.innerHTML = `
      <div class="pipeline-stage-head">
        <span class="pipeline-stage-id">${escapeHtml(st.id || "(no id)")}</span>
        <code>${escapeHtml(st.use || "")}</code>
      </div>
      <div class="pipeline-stage-meta">
        ${st.depends_on && st.depends_on.length ? `depends_on: ${escapeHtml(st.depends_on.join(", "))}` : ""}
        ${st.when ? ` ・ when: <code>${escapeHtml(st.when)}</code>` : ""}
      </div>
      <div class="pipeline-stage-actions">
        <button type="button" data-act="up" data-i="${i}">↑</button>
        <button type="button" data-act="down" data-i="${i}">↓</button>
        <button type="button" data-act="remove" data-i="${i}" class="danger">削除</button>
      </div>
    `;
    ol.appendChild(li);
  }
  ol.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const i = parseInt(e.currentTarget.dataset.i, 10);
      const act = e.currentTarget.dataset.act;
      if (act === "up" && i > 0) {
        const tmp = state.pipelineStagesEdited[i - 1];
        state.pipelineStagesEdited[i - 1] = state.pipelineStagesEdited[i];
        state.pipelineStagesEdited[i] = tmp;
      } else if (act === "down" && i < state.pipelineStagesEdited.length - 1) {
        const tmp = state.pipelineStagesEdited[i + 1];
        state.pipelineStagesEdited[i + 1] = state.pipelineStagesEdited[i];
        state.pipelineStagesEdited[i] = tmp;
      } else if (act === "remove") {
        if (!confirm(`${state.pipelineStagesEdited[i].id} を削除しますか?`)) return;
        state.pipelineStagesEdited.splice(i, 1);
      }
      renderPipelineEditor();
    });
  });
}

function addPipelineStage() {
  const sel = document.getElementById("pipeline-add-select");
  const use = sel.value;
  if (!use) { setPipelineStatus("ステージ部品を選択してください", "err"); return; }
  const cat = state.stagesCatalog.find((s) => s.use === use);
  const newId = prompt("新しいステージの ID を入力(英数とアンダースコアのみ)", cat?.category || "stage");
  if (!newId) return;
  if (!/^[a-z0-9_]+$/.test(newId)) { setPipelineStatus("ID は小文字英数とアンダースコアのみ", "err"); return; }
  if (state.pipelineStagesEdited.some((s) => s.id === newId)) {
    setPipelineStatus("同じ ID が既に存在します", "err");
    return;
  }
  const stage = {
    id: newId,
    description: cat?.display_name || use,
    use,
    depends_on: state.pipelineStagesEdited.length
      ? [state.pipelineStagesEdited[state.pipelineStagesEdited.length - 1].id]
      : [],
    config: {},
  };
  state.pipelineStagesEdited.push(stage);
  sel.value = "";
  renderPipelineEditor();
  setPipelineStatus("追加しました(保存ボタンで反映)", "");
}

async function savePipeline() {
  if (!state.githubRepo) { setPipelineStatus("GitHubリポジトリ未設定", "err"); return; }
  if (!state.pipelineYaml) { setPipelineStatus("パイプライン未読み込み", "err"); return; }
  if (!window.jsyaml) { setPipelineStatus("YAMLライブラリ未読み込み(再読み込みを試してください)", "err"); return; }
  try {
    setPipelineStatus("保存中…", "");
    const newDoc = { ...state.pipelineYaml, stages: state.pipelineStagesEdited };
    const newText = window.jsyaml.dump(newDoc, { lineWidth: 200, noRefs: true });
    const result = await ghPut(
      `src/apps/${state.app}/pipeline.yml`,
      newText,
      state.pipelineSha,
      `chore(pipeline): edit stages via Admin SPA (${state.pipelineStagesEdited.length} stages)`,
    );
    state.pipelineSha = result.content?.sha || null;
    state.pipelineYaml = newDoc;
    state.rawPipeline = newText;
    document.getElementById("pipeline-raw").textContent = newText;
    setPipelineStatus("✓ 保存しました。次の実行から反映されます", "ok");
  } catch (e) {
    setPipelineStatus("保存失敗: " + e.message, "err");
  }
}

function setPipelineStatus(msg, kind) {
  const el = document.getElementById("pipeline-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "rt-status " + (kind || "");
}
