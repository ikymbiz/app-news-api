#!/usr/bin/env python3
"""One-shot cleanup: deduplicate news/current_news.json on R2.

stages.filters.r2_dedupe を導入する前に、R2 上に既に重複したまま蓄積されている
current_news.json をワンショットで掃除するためのユーティリティ。

使い方:

    export CLOUDFLARE_R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
    export CLOUDFLARE_R2_KEY=...
    export CLOUDFLARE_R2_SECRET=...
    python scripts/r2_dedupe_cleanup.py \\
        --bucket agent-platform-artifacts \\
        --key news/current_news.json \\
        [--dry-run]

判定キーは r2_dedupe ステージと同じ:
  - 一次: 正規化済み URL の完全一致
  - 二次: (title.lower() + source.lower()) の SHA-256

実行すると before/after の件数と削除サンプルを表示し、--dry-run でなければ
重複除去後の JSON で R2 オブジェクトを上書きする。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    if u.endswith("/") and not u.endswith("://"):
        u = u[:-1]
    return u or None


def title_source_hash(title: str | None, source: str | None) -> str | None:
    if not (title and source):
        return None
    h = hashlib.sha256()
    h.update(title.strip().lower().encode("utf-8"))
    h.update(b"\x00")
    h.update(source.strip().lower().encode("utf-8"))
    return h.hexdigest()


def item_keys(item: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        normalize_url(item.get("url") or item.get("link")),
        title_source_hash(item.get("title"), item.get("source")),
    )


def dedupe(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for it in items:
        url, ts = item_keys(it)
        match_keys = [k for k in (url, ts) if k]
        if any(k in seen for k in match_keys):
            removed.append(it)
            continue
        for k in match_keys:
            seen.add(k)
        kept.append(it)
    return kept, removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--key", default="news/current_news.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 is required: pip install boto3", file=sys.stderr)
        return 2

    endpoint = os.environ.get("CLOUDFLARE_R2_ENDPOINT")
    access_key = os.environ.get("CLOUDFLARE_R2_KEY")
    secret_key = os.environ.get("CLOUDFLARE_R2_SECRET")
    if not (endpoint and access_key and secret_key):
        print(
            "Missing CLOUDFLARE_R2_ENDPOINT / CLOUDFLARE_R2_KEY / CLOUDFLARE_R2_SECRET",
            file=sys.stderr,
        )
        return 2

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

    print(f"Fetching s3://{args.bucket}/{args.key}")
    obj = client.get_object(Bucket=args.bucket, Key=args.key)
    raw = obj["Body"].read()
    data = json.loads(raw)
    if isinstance(data, list):
        items = data
        wrap = False
        wrapper: dict[str, Any] = {}
    elif isinstance(data, dict):
        wrap = True
        wrapper = dict(data)
        items = list(data.get("items") or data.get("articles") or [])
    else:
        print("Unexpected JSON shape", file=sys.stderr)
        return 1

    print(f"Before: {len(items)} items")
    kept, removed = dedupe(items)
    print(f"After:  {len(kept)} items  ({len(removed)} duplicates removed)")
    if removed:
        print("Sample of removed (first 5):")
        for r in removed[:5]:
            print(f"  - {r.get('url') or r.get('link')!s}  | {r.get('title')!r}")

    if args.dry_run:
        print("Dry run; not writing back.")
        return 0

    if wrap:
        if "articles" in wrapper:
            wrapper["articles"] = kept
        else:
            wrapper["items"] = kept
        out_bytes = json.dumps(wrapper, ensure_ascii=False, indent=2).encode("utf-8")
    else:
        out_bytes = json.dumps(kept, ensure_ascii=False, indent=2).encode("utf-8")

    client.put_object(
        Bucket=args.bucket,
        Key=args.key,
        Body=out_bytes,
        ContentType="application/json; charset=utf-8",
    )
    print("Wrote back deduplicated current_news.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
