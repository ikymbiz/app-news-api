"""Keyword / dedupe filter stage (dual-purpose per mode).

pipeline.yml の `config.mode` で動作を切替:
  - mode: dedupe  → 直前ステージ `items` を `state.seen_items` と照合し新規のみ残す。
  - mode: include → 指定キーワード(config.include) を含む記事のみ残す。
  - mode: exclude → 指定キーワード(config.exclude) を含む記事を除外。

dedupe モードは Firestore の `seen_items` コレクション(settings.json で上書き可)を
照会・更新する。チェックポイントは `ctx.state.save_checkpoint` に委譲し、
本ステージ内で直接 Firestore を触らない(基盤汚染禁止の原則)。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
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
    name = "stages.filters.keyword"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        mode = cfg.get("mode", "include")

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))

        try:
            if mode == "dedupe":
                kept = self._dedupe(ctx, items, cfg)
            elif mode == "include":
                kept = self._filter_by_keywords(items, cfg.get("include", []), keep=True)
            elif mode == "exclude":
                kept = self._filter_by_keywords(items, cfg.get("exclude", []), keep=False)
            else:
                return self._fail(
                    started,
                    FailureCategory.PERMANENT,
                    f"Unknown keyword filter mode: {mode}",
                )
        except Exception as e:  # noqa: BLE001
            return self._fail(started, FailureCategory.TRANSIENT, f"{type(e).__name__}: {e}")

        ctx.record_metric("keyword.in", float(len(items)))
        ctx.record_metric("keyword.out", float(len(kept)))
        ctx.logger.info(
            "keyword.applied",
            extras={"mode": mode, "in": len(items), "out": len(kept)},
        )

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={"items": kept},
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _hash(item: dict[str, Any], fields: list[str]) -> str:
        mat = "|".join(str(item.get(f, "")) for f in fields)
        return hashlib.sha256(mat.encode("utf-8")).hexdigest()

    def _dedupe(
        self,
        ctx: StageContext,
        items: list[dict[str, Any]],
        cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        fields = list(cfg.get("hash_fields", ["url", "title"]))
        # チェックポイントから過去のハッシュ集合を復元
        seen_key = f"seen_items::{ctx.app}"
        prior = ctx.state.get_checkpoint(ctx.job_run_id, seen_key) or {}
        seen: set[str] = set(prior.get("hashes", []))

        kept: list[dict[str, Any]] = []
        for item in items:
            h = self._hash(item, fields)
            if h in seen:
                continue
            seen.add(h)
            kept.append(item)

        # 次回用に保存(最新 N 件のみ保持することで容量肥大化を防ぐ)
        max_keep = int(cfg.get("max_seen", 50_000))
        seen_list = list(seen)[-max_keep:]
        ctx.state.save_checkpoint(
            ctx.job_run_id,
            seen_key,
            {"hashes": seen_list, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        return kept

    @staticmethod
    def _filter_by_keywords(
        items: list[dict[str, Any]],
        keywords: list[str],
        keep: bool,
    ) -> list[dict[str, Any]]:
        if not keywords:
            return items
        lowered = [k.lower() for k in keywords]
        out: list[dict[str, Any]] = []
        for item in items:
            haystack = f"{item.get('title', '')} {item.get('content', '')}".lower()
            hit = any(k in haystack for k in lowered)
            if (keep and hit) or (not keep and not hit):
                out.append(item)
        return out

    @staticmethod
    def _fail(
        started: datetime, category: FailureCategory, message: str
    ) -> StageOutput[dict[str, Any]]:
        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.FAILED,
            error_category=category,
            error_message=message,
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
            ),
        )
