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
        max_items = int(cfg.get("max_items", 30))
        high_value_threshold = float(cfg.get("high_value_threshold", 9.0))

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))

        try:
            body = self._render(items, title_template, max_items, high_value_threshold)
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
    def _render(items: list[dict[str, Any]], title_template: str,
                max_items: int = 30, high_value_threshold: float = 9.0) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title = title_template.replace("{{date}}", today)
        lines: list[str] = [f"# {title}", "", f"_Generated: {datetime.now(timezone.utc).isoformat()}_", ""]
        if not items:
            lines.append("_No items collected._")
            return "\n".join(lines) + "\n"

        # Score distribution summary
        scores = [float(it.get("score", 0)) for it in items]
        buckets = {
            "9.0+": sum(1 for s in scores if s >= 9.0),
            "8.0-8.9": sum(1 for s in scores if 8.0 <= s < 9.0),
            "7.0-7.9": sum(1 for s in scores if 7.0 <= s < 8.0),
            "6.0-6.9": sum(1 for s in scores if 6.0 <= s < 7.0),
            "5.0-5.9": sum(1 for s in scores if 5.0 <= s < 6.0),
            "<5.0": sum(1 for s in scores if s < 5.0),
        }
        avg = sum(scores) / len(scores) if scores else 0.0
        lines.append(f"**Items collected**: {len(items)} | **Avg score**: {avg:.2f} | **Showing top {min(max_items, len(items))}**")
        lines.append("")
        lines.append("**Score distribution:**")
        for label, count in buckets.items():
            if count > 0:
                bar = "█" * min(40, count)
                lines.append(f"- `{label:8s}` {count:4d}  {bar}")
        lines.append("")
        lines.append("---")
        lines.append("")

        sorted_items = sorted(items, key=lambda x: float(x.get("score", 0)), reverse=True)[:max_items]
        for idx, item in enumerate(sorted_items, 1):
            score = float(item.get("score", 0))
            badge = " ★" if score >= high_value_threshold else ""
            lines.append(f"## {idx}.{badge} {item.get('title', '(untitled)')}  `[{score:.1f}]`")
            lines.append("")
            lines.append(f"- **Source**: {item.get('source', '-')}")
            lines.append(f"- **URL**: {item.get('url', '-')}")
            topics = item.get("topics", []) or []
            if topics:
                lines.append(f"- **Topics**: {', '.join(topics)}")
            reason = item.get("reason")
            if reason:
                lines.append(f"- **Reason**: {reason}")
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
