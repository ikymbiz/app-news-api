"""Meta export reporter stage.

Firestore の `job_runs` / `stage_runs` / `metrics` コレクションから直近 N 日分を
読み出し、Admin SPA が Worker 経由で取得可能な静的 JSON としてダンプする。

出力レイアウト(`output_dir` 配下):
  meta/runs.json      — 直近 N 日の JobRun 一覧
  meta/stages.json    — 直近 N 日の StageRun 一覧
  meta/metrics.json   — 直近 N 日の Metric レコード(コスト集計用)

R2 アップロードは本ステージでは行わない。直後の `stages.reporters.r2_upload`
ステージに出力を渡すことで、単一責務を維持する。

Firestore 接続不在時(ローカル dry-run 等)は空配列をダンプして success を返す。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
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
    name = "stages.reporters.meta_export"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        output_dir = Path(cfg.get("output_dir", "artifacts/meta"))
        days = int(cfg.get("days", 30))
        limit = int(cfg.get("limit", 500))
        since = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            client = self._resolve_firestore(ctx)
        except Exception as e:  # noqa: BLE001
            return self._fail(started, FailureCategory.PERMANENT, f"firestore import: {e}")

        runs = self._query(client, "job_runs", "started_at", since, limit, ctx)
        stages = self._query(client, "stage_runs", "started_at", since, limit, ctx)
        metrics = self._query(client, "metrics", "recorded_at", since, limit, ctx)

        artifacts: list[dict[str, Any]] = []
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, items in (
            ("runs.json", runs),
            ("stages.json", stages),
            ("metrics.json", metrics),
        ):
            path = output_dir / name
            body = json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "window_days": days,
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            path.write_text(body, encoding="utf-8")
            checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
            art = ReportArtifact(
                format="json",
                path=str(path),
                bytes=len(body.encode("utf-8")),
                checksum=checksum,
            )
            artifacts.append(
                {
                    "format": art.format,
                    "path": art.path,
                    "bytes": art.bytes,
                    "checksum": art.checksum,
                }
            )
            ctx.record_metric(f"meta_export.{name}.bytes", float(art.bytes))

        ctx.logger.info(
            "meta_export.done",
            extras={
                "runs": len(runs),
                "stages": len(stages),
                "metrics": len(metrics),
                "output_dir": str(output_dir),
            },
        )

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={"artifacts": artifacts, "artifact": artifacts[0] if artifacts else None},
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                artifact_bytes=sum(a["bytes"] for a in artifacts),
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_firestore(ctx: StageContext):
        """Reach into the injected state store to reuse its Firestore client.

        state store に `_client` が無い(Noop)場合は None を返す。
        """
        state = ctx.state
        return getattr(state, "_client", None)

    @staticmethod
    def _query(
        client: Any,
        collection: str,
        order_field: str,
        since: datetime,
        limit: int,
        ctx: StageContext,
    ) -> list[dict[str, Any]]:
        if client is None:
            ctx.logger.warning("meta_export.noop", extras={"collection": collection})
            return []
        try:
            # Prefer firestore native query API if available.
            coll = client.collection(collection)
            query = coll.where(order_field, ">=", since).order_by(order_field, direction="DESCENDING").limit(limit)  # type: ignore[attr-defined]
            out: list[dict[str, Any]] = []
            for snap in query.stream():
                d = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap)
                d["_id"] = getattr(snap, "id", None)
                out.append(d)
            return out
        except Exception as e:  # noqa: BLE001
            ctx.logger.warning(
                "meta_export.query_failed",
                extras={"collection": collection, "error": str(e)},
            )
            return []

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
