"""R2 dedupe filter stage.

直前ステージ(通常は filter / report_json の前)で渡された items を、R2 上の
既存 `current_news.json` と突き合わせて重複検知する。重複の判定キーは2段:

  1. 一次キー: `url` の完全一致(末尾スラッシュとフラグメントは正規化)
  2. 二次キー: `(title + source)` を SHA-256 した hex(URL がトラッキングパラメータで
     揺れるケースの保険)

動作モード(`config.mode`):
  - skip          : 重複した *新規* items を出力から除外(既存側はそのまま)。デフォルト。
  - delete        : 既存側を新しい方で置き換え(generated_at が新しい方を残す)。
                    具体的には新規側を残し、出力 items に既存の非重複分を **追加しない**
                    (つまり次段の reporter が新しい配列を current_news.json として上書きする
                    既存挙動を信頼する)。報告のみ。
  - report_only   : 何も除外せず、検知件数を metrics と meta/dedup_report.json に記録。

成果物:
  - 戻り値 payload.items: モードに応じてフィルタされた items
  - meta_export 等が拾えるよう、ctx.record_metric で in/dup/out を記録
  - 任意で meta/dedup_report.json をローカル artifacts に書く
    (config.report_path、デフォルト artifacts/news/meta/dedup_report.json)

R2 アクセスは boto3 の S3 互換クライアントを使う(reporters.r2_upload と同方式)。
boto3 未導入時 / 既存ファイル未存在時はスタブ動作(全件 pass-through)で success。

pipeline.yml 例:

  - id: r2_dedupe
    use: stages.filters.r2_dedupe
    depends_on: [filter]
    config:
      mode: skip
      bucket: agent-platform-artifacts
      key: news/current_news.json
      report_path: artifacts/news/meta/dedup_report.json
      endpoint_url_env: CLOUDFLARE_R2_ENDPOINT
      access_key_env: CLOUDFLARE_R2_KEY
      secret_key_env: CLOUDFLARE_R2_SECRET
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.contracts import (
    FailureCategory,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
)


class StageImpl:
    name = "stages.filters.r2_dedupe"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        mode = cfg.get("mode", "skip")
        if mode not in {"skip", "delete", "report_only"}:
            return self._fail(
                started,
                FailureCategory.PERMANENT,
                f"unknown mode: {mode!r} (expected skip/delete/report_only)",
            )

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))
        n_in = len(items)

        try:
            existing = self._load_existing(cfg)
        except Exception as e:  # noqa: BLE001
            # 取得失敗は致命ではない(初回実行など)。pass-through で続行。
            ctx.record_metric("r2_dedupe.load_error", 1.0)
            existing = []

        existing_keys = self._build_key_set(existing)
        kept: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        for it in items:
            keys = self._item_keys(it)
            is_dup = any(k in existing_keys for k in keys)
            if is_dup:
                duplicates.append({
                    "url": it.get("url") or it.get("link"),
                    "title": it.get("title"),
                    "source": it.get("source"),
                    "matched_by": "url" if (keys[0] and keys[0] in existing_keys) else "title_source",
                })
                if mode == "skip":
                    continue
                # delete モードは新規側を残す(次段の reporter が上書きする想定)
                kept.append(it)
            else:
                kept.append(it)

        n_dup = len(duplicates)
        n_out = len(kept) if mode != "report_only" else n_in

        ctx.record_metric("r2_dedupe.in", float(n_in))
        ctx.record_metric("r2_dedupe.duplicates", float(n_dup))
        ctx.record_metric("r2_dedupe.out", float(n_out))

        # レポートを書き出し(任意)
        report_path = cfg.get("report_path")
        if report_path:
            try:
                rp = Path(report_path)
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(
                    json.dumps(
                        {
                            "generated_at": started.isoformat(),
                            "mode": mode,
                            "in": n_in,
                            "duplicates": n_dup,
                            "out": n_out,
                            "samples": duplicates[:20],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:  # noqa: BLE001
                ctx.record_metric("r2_dedupe.report_write_error", 1.0)

        out_items = items if mode == "report_only" else kept
        ended = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={"items": out_items, "dedupe": {"in": n_in, "duplicates": n_dup, "out": len(out_items), "mode": mode}},
            metrics=StageMetrics(
                started_at=started,
                ended_at=ended,
                duration_ms=int((ended - started).total_seconds() * 1000),
                custom={"in": n_in, "duplicates": n_dup, "out": len(out_items)},
            ),
        )

    # --------------------------------------------------------------------- #

    def _load_existing(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """R2 から既存 current_news.json を取得して items 配列を返す。

        boto3 未導入時 / バケット未設定時 / オブジェクト不在時は空配列を返す
        (致命扱いしない — 初回実行を前提に)。
        """
        bucket = cfg.get("bucket")
        key = cfg.get("key", "news/current_news.json")
        if not bucket:
            return []
        try:
            import boto3  # noqa: WPS433
        except ImportError:
            return []
        endpoint = os.environ.get(cfg.get("endpoint_url_env", "CLOUDFLARE_R2_ENDPOINT"))
        access_key = os.environ.get(cfg.get("access_key_env", "CLOUDFLARE_R2_KEY"))
        secret_key = os.environ.get(cfg.get("secret_key_env", "CLOUDFLARE_R2_SECRET"))
        if not (endpoint and access_key and secret_key):
            return []
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
        except Exception:  # noqa: BLE001
            return []
        body = obj["Body"].read()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.get("items") or data.get("articles") or [])
        return []

    @staticmethod
    def _normalize_url(url: str | None) -> str | None:
        if not url:
            return None
        u = url.strip()
        # strip fragment
        if "#" in u:
            u = u.split("#", 1)[0]
        # strip trailing slash (excluding the protocol's '//')
        if u.endswith("/") and not u.endswith("://"):
            u = u[:-1]
        return u or None

    @staticmethod
    def _title_source_hash(title: str | None, source: str | None) -> str | None:
        if not (title and source):
            return None
        h = hashlib.sha256()
        h.update(title.strip().lower().encode("utf-8"))
        h.update(b"\x00")
        h.update(source.strip().lower().encode("utf-8"))
        return h.hexdigest()

    @classmethod
    def _item_keys(cls, item: dict[str, Any]) -> tuple[str | None, str | None]:
        url = cls._normalize_url(item.get("url") or item.get("link"))
        ts = cls._title_source_hash(item.get("title"), item.get("source"))
        return (url, ts)

    @classmethod
    def _build_key_set(cls, items: list[dict[str, Any]]) -> set[str]:
        s: set[str] = set()
        for it in items:
            url, ts = cls._item_keys(it)
            if url:
                s.add(url)
            if ts:
                s.add(ts)
        return s

    @staticmethod
    def _fail(started: datetime, category: FailureCategory, message: str) -> StageOutput[dict[str, Any]]:
        ended = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.FAILED,
            payload=None,
            metrics=StageMetrics(
                started_at=started,
                ended_at=ended,
                duration_ms=int((ended - started).total_seconds() * 1000),
            ),
            failure_category=category,
            failure_message=message,
        )
