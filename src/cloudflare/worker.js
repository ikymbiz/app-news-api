// Cloudflare Worker — static JSON distributor for the agent platform
//
// SYSTEM_DESIGN.md §5 "Data Distribution Protocol" に基づく。
// R2 バケットにアップロードされた artifacts(current_news.json 等)を
// ETag/If-None-Match ベースで差分配信する。
//
// - GET /<app>/<filename>  : R2 から返却(Cache-Control + ETag)
// - GET /<app>/index.json  : app 配下のアーティファクト一覧(簡易インデックス)
// - HEAD /<app>/<filename> : checksum/last-modified のみ返却(HTA の差分チェック用)
//
// 環境変数(wrangler.toml):
//   ARTIFACTS (R2 binding)   : R2 bucket binding
//   ALLOWED_APPS              : カンマ区切りの許可アプリ名(例 "news")
//   CORS_ORIGIN               : 管理画面の Origin (CORS 許可)

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    // CORS preflight
    if (method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }

    // ---- Admin SPA → GitHub Contents proxy ----
    // Cloudflare Access が前段にある前提で Cf-Access-Authenticated-User-Email を検証する。
    // 直接アクセス(Access bypass)で穴が空かないよう、SPA から呼ばれる /api/github/* は
    // ヘッダ未付与なら 401。許可メールリスト ACCESS_ALLOWED_EMAILS を任意で併用できる。
    if (url.pathname.startsWith("/api/github/")) {
      const guard = checkAccess(request, env);
      if (guard) return guard;
      try {
        if (method === "GET" && url.pathname === "/api/github/read") {
          return await githubRead(url, env);
        }
        if (method === "POST" && url.pathname === "/api/github/write") {
          return await githubWrite(request, env);
        }
        return json({ error: "method not allowed" }, 405, env);
      } catch (err) {
        return json({ error: String(err && err.message || err) }, 500, env);
      }
    }

    // ---- Admin SPA → non-secret config readback ----
    if (url.pathname === "/api/config" && method === "GET") {
      const guard = checkAccess(request, env);
      if (guard) return guard;
      return json({
        GH_REPO: env.GH_REPO || "",
        GH_BRANCH: env.GH_BRANCH || "main",
        ALLOWED_APPS: env.ALLOWED_APPS || "news",
        ACCESS_ALLOWED_EMAILS: env.ACCESS_ALLOWED_EMAILS || "",
        // GH_PAT is never returned.
      }, 200, env);
    }

    // ---- Admin SPA → RSS feed preview proxy (CORS bypass) ----
    if (url.pathname === "/api/rss/preview" && method === "GET") {
      const guard = checkAccess(request, env);
      if (guard) return guard;
      try {
        return await rssPreview(url, env);
      } catch (err) {
        return json({ error: String(err && err.message || err) }, 500, env);
      }
    }

    // Parse path: /<app>/<rest...>
    const parts = url.pathname.replace(/^\/+/, "").split("/");
    if (parts.length < 2 || !parts[0] || !parts[1]) {
      return json({ error: "path must be /<app>/<filename>" }, 400, env);
    }
    const [app, ...rest] = parts;

    if (!isAllowedApp(app, env)) {
      return json({ error: `app not allowed: ${app}` }, 403, env);
    }

    const key = `${app}/${rest.join("/")}`;

    try {
      if (method === "GET" || method === "HEAD") {
        return await serveObject(request, env, app, key, method === "HEAD");
      }
      return json({ error: "method not allowed" }, 405, env);
    } catch (err) {
      return json({ error: String(err && err.message || err) }, 500, env);
    }
  },
};

// --------------------------------------------------------------------------- //

async function serveObject(request, env, app, key, headOnly) {
  // Simple index endpoint
  if (key.endsWith("/index.json") || key === `${app}/index.json`) {
    const listed = await env.ARTIFACTS.list({ prefix: `${app}/` });
    const payload = {
      app,
      generated_at: new Date().toISOString(),
      objects: listed.objects.map((o) => ({
        key: o.key,
        size: o.size,
        uploaded: o.uploaded,
        etag: o.etag,
      })),
    };
    return json(payload, 200, env, {
      "Cache-Control": "public, max-age=60",
    });
  }

  // If-None-Match → 304 shortcut
  const ifNoneMatch = request.headers.get("If-None-Match");
  if (ifNoneMatch) {
    const head = await env.ARTIFACTS.head(key);
    if (head && head.etag && quote(head.etag) === ifNoneMatch) {
      return new Response(null, {
        status: 304,
        headers: {
          ETag: quote(head.etag),
          "Cache-Control": "public, max-age=300, must-revalidate",
          ...corsHeaders(env),
        },
      });
    }
  }

  const object = await env.ARTIFACTS.get(key);
  if (!object) {
    return json({ error: "not found", key }, 404, env);
  }

  const headers = new Headers({
    ETag: quote(object.etag),
    "Cache-Control": "public, max-age=300, must-revalidate",
    "Content-Type": contentTypeFor(key),
    "X-Artifact-Key": key,
    ...corsHeaders(env),
  });
  if (object.uploaded) {
    headers.set("Last-Modified", new Date(object.uploaded).toUTCString());
  }
  if (object.customMetadata && object.customMetadata.checksum) {
    headers.set("X-Artifact-Checksum", object.customMetadata.checksum);
  }

  if (headOnly) {
    headers.set("Content-Length", String(object.size));
    return new Response(null, { status: 200, headers });
  }
  return new Response(object.body, { status: 200, headers });
}

// --------------------------------------------------------------------------- //

function isAllowedApp(app, env) {
  const list = (env.ALLOWED_APPS || "news").split(",").map((s) => s.trim()).filter(Boolean);
  return list.includes(app);
}

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.CORS_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
    "Access-Control-Allow-Headers": "If-None-Match, Content-Type, Cf-Access-Authenticated-User-Email",
    "Access-Control-Expose-Headers": "ETag, X-Artifact-Checksum, Last-Modified",
  };
}

function contentTypeFor(key) {
  if (key.endsWith(".json")) return "application/json; charset=utf-8";
  if (key.endsWith(".md")) return "text/markdown; charset=utf-8";
  if (key.endsWith(".html")) return "text/html; charset=utf-8";
  return "application/octet-stream";
}

function quote(etag) {
  if (!etag) return '""';
  return etag.startsWith('"') ? etag : `"${etag}"`;
}

function json(obj, status, env, extra) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders(env),
      ...(extra || {}),
    },
  });
}

// --------------------------------------------------------------------------- //
// GitHub Contents API proxy (Admin SPA → secrets.GH_PAT)
// --------------------------------------------------------------------------- //

// Cloudflare Access の前段保護を検証する。Access が前にある場合は
// Cf-Access-Authenticated-User-Email ヘッダがリクエストに付与される。
// ACCESS_ALLOWED_EMAILS が定義されていれば追加で照合する(防御の二層目)。
// 戻り値: ガード失敗時は Response、成功時は null。
function checkAccess(request, env) {
  const email = request.headers.get("Cf-Access-Authenticated-User-Email") || "";
  if (!email) {
    return json(
      { error: "unauthenticated: Cloudflare Access header missing" },
      401,
      env,
    );
  }
  const allowed = (env.ACCESS_ALLOWED_EMAILS || "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  if (allowed.length > 0 && !allowed.includes(email.toLowerCase())) {
    return json({ error: `forbidden: ${email} not in allowlist` }, 403, env);
  }
  return null;
}

function ghRepo(env) {
  const repo = env.GH_REPO || "";
  if (!/^[^/\s]+\/[^/\s]+$/.test(repo)) {
    throw new Error("env.GH_REPO must be set as 'owner/name'");
  }
  return repo;
}

function ghHeaders(env) {
  if (!env.GH_PAT) throw new Error("env.GH_PAT secret is not configured");
  return {
    "Authorization": `Bearer ${env.GH_PAT}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "agent-platform-worker/1.0",
  };
}

function ghBranch(env) {
  return env.GH_BRANCH || "main";
}

// path クエリは "config/sources.json" のようなリポジトリ相対パス。
// 安全のため `..` や絶対パスを拒否する。
function safeRepoPath(p) {
  if (!p || typeof p !== "string") throw new Error("path is required");
  if (p.startsWith("/") || p.includes("..") || p.includes("\\")) {
    throw new Error(`unsafe path: ${p}`);
  }
  return p;
}

async function githubRead(url, env) {
  const path = safeRepoPath(url.searchParams.get("path"));
  const repo = ghRepo(env);
  const branch = ghBranch(env);
  const api = `https://api.github.com/repos/${repo}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}?ref=${encodeURIComponent(branch)}`;
  const r = await fetch(api, { headers: ghHeaders(env) });
  if (r.status === 404) {
    return json({ error: "not found", path }, 404, env);
  }
  if (!r.ok) {
    const body = await r.text();
    return json({ error: "github read failed", status: r.status, body }, 502, env);
  }
  const data = await r.json();
  // GitHub Contents API returns base64-encoded content
  let content = "";
  if (data && data.content) {
    content = atob(data.content.replace(/\n/g, ""));
  }
  return json(
    { path, sha: data && data.sha, size: data && data.size, content },
    200,
    env,
  );
}

async function githubWrite(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return json({ error: "invalid JSON body" }, 400, env);
  }
  const path = safeRepoPath(body.path);
  const message = (body.message || `chore: update ${path} via admin SPA`).slice(0, 200);
  if (typeof body.content !== "string") {
    return json({ error: "content (string) is required" }, 400, env);
  }
  const repo = ghRepo(env);
  const branch = ghBranch(env);
  const api = `https://api.github.com/repos/${repo}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`;

  // Determine current sha (required for updates; omitted on create)
  let sha = body.sha;
  if (!sha) {
    const head = await fetch(`${api}?ref=${encodeURIComponent(branch)}`, {
      headers: ghHeaders(env),
    });
    if (head.ok) {
      const meta = await head.json();
      sha = meta && meta.sha;
    }
  }

  const payload = {
    message,
    branch,
    // GitHub Contents API expects base64-encoded content
    content: btoa(unescape(encodeURIComponent(body.content))),
  };
  if (sha) payload.sha = sha;

  const put = await fetch(api, {
    method: "PUT",
    headers: { ...ghHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!put.ok) {
    const errBody = await put.text();
    return json(
      { error: "github write failed", status: put.status, body: errBody },
      502,
      env,
    );
  }
  const result = await put.json();
  return json(
    {
      path,
      sha: result && result.content && result.content.sha,
      commit: result && result.commit && result.commit.sha,
      committed_at: result && result.commit && result.commit.committer && result.commit.committer.date,
    },
    200,
    env,
  );
}

// --------------------------------------------------------------------------- //
// RSS feed preview (server-side fetch + minimal XML parse)
// --------------------------------------------------------------------------- //

async function rssPreview(url, env) {
  const target = url.searchParams.get("url");
  if (!target || !/^https:\/\//i.test(target)) {
    return json({ error: "url must start with https://" }, 400, env);
  }
  const r = await fetch(target, {
    headers: {
      "User-Agent": "Mozilla/5.0 (compatible; agent-platform-preview/1.0)",
      "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    },
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!r.ok) {
    return json({ error: `upstream HTTP ${r.status}`, url: target }, 502, env);
  }
  const xml = await r.text();
  const items = parseFeedItems(xml).slice(0, 5);
  return json({ url: target, items }, 200, env);
}

// 最低限の RSS 2.0 / Atom 抽出。<item>...</item> または <entry>...</entry> のブロック内
// から <title>, <pubDate>/<published>/<updated> を取得する。XML パーサは Workers ランタイムに
// 標準では無いので、正規表現で割り切る(プレビュー用途、5件しか返さない)。
function parseFeedItems(xml) {
  const items = [];
  const blockRe = /<(item|entry)\b[^>]*>([\s\S]*?)<\/\1>/gi;
  let m;
  while ((m = blockRe.exec(xml)) !== null && items.length < 20) {
    const block = m[2];
    items.push({
      title: extractTag(block, "title"),
      published: extractTag(block, "pubDate") || extractTag(block, "published")
                 || extractTag(block, "updated") || extractTag(block, "dc:date") || "",
    });
  }
  return items;
}

function extractTag(block, tag) {
  const re = new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)<\\/${tag}>`, "i");
  const m = block.match(re);
  if (!m) return "";
  let v = m[1].trim();
  // strip CDATA
  v = v.replace(/^<!\[CDATA\[([\s\S]*?)\]\]>$/i, "$1");
  // strip HTML tags
  v = v.replace(/<[^>]+>/g, "").trim();
  // unescape entities (minimal)
  v = v.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
       .replace(/&quot;/g, '"').replace(/&#39;/g, "'");
  return v;
}
