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
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "If-None-Match, Content-Type",
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
