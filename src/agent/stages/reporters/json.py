"""JSON reporter stage.

Deep Research 済みアイテム群を配信用 JSON (current_news.json 相当) にまとめ、
Cloudflare R2 等へアップロードする前段の静的ファイルを生成する。

スキーマバージョンを明示し、HTA クライアントの差分同期(Delta Sync)用に
`generated_at` / `item.id` / `item.checksum` を出力する。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.contracts import (
    FailureCategory,
    ReportArtifact,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
)


class StageImpl:
    name = "stages.reporters.json"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        output_path = Path(cfg.get("output_path", "artifacts/current.json"))
        schema_version = str(cfg.get("schema_version", "1.0"))
        max_chars = int(cfg.get("max_summary_chars", 4000))

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))

        try:
            doc = self._build_document(items, schema_version, max_chars, ctx.job_run_id)
            serialized = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return self._fail(started, FailureCategory.TRANSIENT, f"{type(e).__name__}: {e}")

        checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        artifact = ReportArtifact(
            format="json",
            path=str(output_path),
            bytes=len(serialized.encode("utf-8")),
            checksum=checksum,
        )
        ctx.record_metric("report.json.bytes", float(artifact.bytes))
        ctx.record_metric("report.json.items", float(len(items)))
        ctx.logger.info(
            "report.json.written",
            extras={"path": artifact.path, "bytes": artifact.bytes, "items": len(items)},
        )

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={
                "artifact": {
                    "format": artifact.format,
                    "path": artifact.path,
                    "bytes": artifact.bytes,
                    "checksum": artifact.checksum,
                }
            },
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                artifact_bytes=artifact.bytes,
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_document(
        items: list[dict[str, Any]],
        schema_version: str,
        max_chars: int,
        job_run_id: str,
    ) -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        for item in items:
            summary = str(item.get("summary", ""))
            if len(summary) > max_chars:
                summary = summary[: max_chars - 1].rstrip() + "…"
            item_payload = {
                "id": item.get("id"),
                "source": item.get("source"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "score": item.get("score"),
                "reason": item.get("reason"),
                "topics": item.get("topics", []),
                "summary": summary,
                "citations": item.get("citations", []),
                "research_model": item.get("research_model"),
            }
            item_checksum = hashlib.sha256(
                json.dumps(item_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16]
            item_payload["checksum"] = item_checksum
            normalized.append(item_payload)

        return {
            "schema_version": schema_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_run_id": job_run_id,
            "item_count": len(normalized),
            "items": normalized,
        }

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
