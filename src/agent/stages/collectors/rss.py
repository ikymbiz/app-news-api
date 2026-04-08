"""RSS/Atom collector stage.

`config/sources.json` を読み込み、有効な全フィードを巡回して `CollectedItem` のリストを返す。
feedparser に依存(無ければ標準ライブラリでフォールバック解析はしない → 明示エラー)。

本モジュールは基盤側(src/agent/)にあり、アプリ固有のソースURL定数を持たない。
ソース定義は必ず StageInput.config 経由(ファイルパス指定)で注入される。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.contracts import (
    CollectedItem,
    FailureCategory,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
)


class StageImpl:
    """RSS collector stage implementation (registry entry class)."""

    name = "stages.collectors.rss"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        sources_file = cfg.get("sources_file", "config/sources.json")
        timeout_seconds = int(cfg.get("fetch_timeout_seconds", 20))
        max_items_per_source = int(cfg.get("max_items_per_source", 50))
        user_agent = cfg.get("user_agent", "agent-platform/1.0")
        max_age_days = int(cfg.get("max_age_days", 0) or 0)
        drop_undated = bool(cfg.get("drop_undated", False))

        # Runtime overrides (editable from Admin SPA without redeploy).
        runtime_file = cfg.get("runtime_file")
        if runtime_file:
            try:
                with Path(runtime_file).open("r", encoding="utf-8") as rf:
                    runtime_doc = json.load(rf)
                collect_overrides = (runtime_doc or {}).get("collect", {}) or {}
                if "max_age_days" in collect_overrides:
                    max_age_days = int(collect_overrides["max_age_days"] or 0)
                if "drop_undated" in collect_overrides:
                    drop_undated = bool(collect_overrides["drop_undated"])
            except FileNotFoundError:
                pass  # runtime.json optional
            except (ValueError, OSError) as e:
                ctx.logger.warning(
                    "rss.runtime_file_invalid",
                    extras={"file": str(runtime_file), "error": str(e)},
                )

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
            if max_age_days > 0
            else None
        )

        try:
            sources = self._load_sources(Path(sources_file))
        except FileNotFoundError as e:
            return self._fail(started, FailureCategory.PERMANENT, str(e))

        try:
            import feedparser  # type: ignore
            import requests  # type: ignore
        except ImportError as e:
            return self._fail(
                started,
                FailureCategory.PERMANENT,
                f"Required dependency missing: {e}",
            )

        collected: list[CollectedItem] = []
        source_errors: list[dict[str, str]] = []
        skipped_old = 0
        skipped_undated = 0

        for src in sources:
            if not src.get("enabled", True):
                continue
            try:
                resp = requests.get(
                    src["feed_url"],
                    headers={"User-Agent": user_agent},
                    timeout=timeout_seconds,
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as e:  # noqa: BLE001
                ctx.logger.warning(
                    "rss.fetch_failed",
                    extras={"source": src["id"], "error": str(e)},
                )
                source_errors.append({"source": src["id"], "error": str(e)})
                continue

            for entry in feed.entries[:max_items_per_source]:
                url = getattr(entry, "link", "") or ""
                title = getattr(entry, "title", "") or ""
                if not url or not title:
                    continue
                content = (
                    getattr(entry, "summary", None)
                    or getattr(entry, "description", None)
                    or ""
                )
                published_at = self._parse_published(entry)
                if cutoff is not None:
                    if published_at is None:
                        if drop_undated:
                            skipped_undated += 1
                            continue
                    elif published_at < cutoff:
                        skipped_old += 1
                        continue
                item_id = hashlib.sha256(
                    f"{src['id']}|{url}|{title}".encode("utf-8")
                ).hexdigest()[:24]
                collected.append(
                    CollectedItem(
                        id=item_id,
                        source=src["id"],
                        title=title,
                        url=url,
                        published_at=published_at,
                        content=content,
                        raw={
                            "source_name": src.get("name", src["id"]),
                            "category": src.get("category", ""),
                            "language": src.get("language", ""),
                            "via": src.get("via", "rss"),
                        },
                    )
                )

        ctx.record_metric("collect.items", float(len(collected)))
        ctx.record_metric("collect.source_errors", float(len(source_errors)))
        ctx.record_metric("collect.skipped_old", float(skipped_old))
        ctx.record_metric("collect.skipped_undated", float(skipped_undated))
        ctx.logger.info(
            "rss.collected",
            extras={
                "count": len(collected),
                "errors": len(source_errors),
                "skipped_old": skipped_old,
                "skipped_undated": skipped_undated,
                "max_age_days": max_age_days,
            },
        )

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={"items": [self._to_dict(i) for i in collected]},
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                artifact_bytes=sum(len(i.content.encode("utf-8")) for i in collected),
                custom={"source_errors": source_errors},
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_sources(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
        sources = doc.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError(f"{path}: 'sources' must be a list")
        return sources

    @staticmethod
    def _parse_published(entry: Any) -> datetime | None:
        for attr in ("published_parsed", "updated_parsed"):
            ts = getattr(entry, attr, None)
            if ts is not None:
                try:
                    return datetime(*ts[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _to_dict(item: CollectedItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "source": item.source,
            "title": item.title,
            "url": item.url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "content": item.content,
            "raw": dict(item.raw),
        }

    @staticmethod
    def _fail(
        started: datetime, category: FailureCategory, message: str
    ) -> StageOutput[dict[str, Any]]:
        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.FAILED,
            payload=None,
            error_category=category,
            error_message=message,
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
            ),
        )
