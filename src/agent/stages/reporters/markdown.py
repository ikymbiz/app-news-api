"""Markdown reporter stage.

Deep Research 後のアイテム群を人間可読な Markdown レポートに整形し、
指定パスへ書き出す(ローカル工程。R2 アップロードは distribution レイヤ担当)。
"""

from __future__ import annotations

import hashlib
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
    name = "stages.reporters.markdown"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        output_path = Path(cfg.get("output_path", "artifacts/report.md"))
        title_template = cfg.get("title_template", "Report - {{date}}")

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))

        try:
            body = self._render(items, title_template)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(body, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return self._fail(started, FailureCategory.TRANSIENT, f"{type(e).__name__}: {e}")

        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        artifact = ReportArtifact(
            format="markdown",
            path=str(output_path),
            bytes=len(body.encode("utf-8")),
            checksum=checksum,
        )
        ctx.record_metric("report.markdown.bytes", float(artifact.bytes))
        ctx.logger.info(
            "report.markdown.written",
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
    def _render(items: list[dict[str, Any]], title_template: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = title_template.replace("{{date}}", today)
        lines: list[str] = [f"# {title}", "", f"_Generated: {datetime.now(timezone.utc).isoformat()}_", ""]
        if not items:
            lines.append("_No high-value items found._")
            return "\n".join(lines) + "\n"

        sorted_items = sorted(items, key=lambda x: float(x.get("score", 0)), reverse=True)
        for idx, item in enumerate(sorted_items, 1):
            lines.append(f"## {idx}. {item.get('title', '(untitled)')}")
            lines.append("")
            lines.append(f"- **Source**: {item.get('source', '-')}")
            lines.append(f"- **URL**: {item.get('url', '-')}")
            lines.append(f"- **Score**: {item.get('score', '-')}")
            topics = item.get("topics", []) or []
            if topics:
                lines.append(f"- **Topics**: {', '.join(topics)}")
            reason = item.get("reason")
            if reason:
                lines.append(f"- **Filter Reason**: {reason}")
            lines.append("")
            summary = item.get("summary")
            if summary:
                lines.append("### Summary")
                lines.append("")
                lines.append(summary)
                lines.append("")
            citations = item.get("citations") or []
            if citations:
                lines.append("### Citations")
                lines.append("")
                for c in citations:
                    lines.append(f"- {c}")
                lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

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
