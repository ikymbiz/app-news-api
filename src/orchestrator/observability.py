"""Observability: logs, token/cost tracking, metrics aggregation.

SYSTEM_DESIGN.md §2.4。Firestore `metrics` コレクションへ集約書き込みする。
ステージは `StageContext.record_tokens` / `record_metric` 経由で呼び出す。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.contracts import TokenUsage


# --------------------------------------------------------------------------- #
# Structured JSON logger
# --------------------------------------------------------------------------- #

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = getattr(record, "extras", None)
        if isinstance(extras, dict):
            base.update(extras)
        return json.dumps(base, ensure_ascii=False, default=str)


class _StageLogger:
    """Adapter that lets stage code call ``logger.info(msg, extras={...})``
    using the natural keyword name, while still emitting through the standard
    Python logging machinery and our JSON formatter.

    Background: ``logging.Logger.info`` only accepts ``extra={...}`` (singular),
    not ``extras=...``. Stage implementations across the codebase use the
    ``extras=`` form to match the ``contracts.Logger`` Protocol, so this
    adapter translates the call site convention into the stdlib convention.
    """

    def __init__(self, base: logging.Logger) -> None:
        self._base = base

    def _emit(self, level: int, msg: str, **kwargs: Any) -> None:
        extras = kwargs.pop("extras", None)
        extra = kwargs.pop("extra", None) or {}
        if extras is not None:
            # Avoid stomping on a caller-provided "extras" key in extra={}.
            extra.setdefault("extras", extras)
        # Forward only kwargs that stdlib logging understands.
        self._base.log(level, msg, extra=extra)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, **kwargs)


def get_logger(name: str) -> _StageLogger:
    base = logging.getLogger(name)
    if not base.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_JsonFormatter())
        base.addHandler(h)
        base.setLevel(logging.INFO)
        base.propagate = False
    return _StageLogger(base)


# --------------------------------------------------------------------------- #
# Token/cost pricing (USD per 1K tokens) — 概算。確定値は settings.json で上書き。
# --------------------------------------------------------------------------- #

DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # model: (prompt_per_1k, completion_per_1k)
    "gemini-2.5-flash-lite": (0.0001, 0.0004),
    "gemini-2.5-pro": (0.00125, 0.005),
    "gpt-4o-deep-research": (0.005, 0.015),
}


def estimate_cost_usd(usage: TokenUsage, pricing: Mapping[str, tuple[float, float]] | None = None) -> float:
    table = pricing or DEFAULT_PRICING
    p_in, p_out = table.get(usage.model, (0.0, 0.0))
    return (usage.prompt_tokens / 1000.0) * p_in + (usage.completion_tokens / 1000.0) * p_out


# --------------------------------------------------------------------------- #
# Metrics collector
# --------------------------------------------------------------------------- #

@dataclass
class MetricRecord:
    job_run_id: str
    stage_id: str
    attempt: int
    token_usages: list[TokenUsage] = field(default_factory=list)
    custom_metrics: dict[str, float] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsSink:
    """Firestore metrics コレクションへの書き込み。

    Phase6 で write-through 方式に変更:
      - record() は呼び出し時点で即座に Firestore へ書き込む。
      - flush() は後方互換のための no-op(将来バッチ送信に戻す余地は残す)。
      - これにより meta_export がジョブ実行中に metrics を読み取れる。
    """

    COLLECTION = "metrics"

    def __init__(self, firestore_client: Any, pricing: Mapping[str, tuple[float, float]] | None = None) -> None:
        self._client = firestore_client
        self._pricing = pricing or DEFAULT_PRICING

    def record(self, rec: MetricRecord) -> None:
        for u in rec.token_usages:
            if u.estimated_cost_usd == 0.0:
                u.estimated_cost_usd = estimate_cost_usd(u, self._pricing)
        rec.total_cost_usd = sum(u.estimated_cost_usd for u in rec.token_usages)
        doc_id = f"{rec.job_run_id}__{rec.stage_id}__{rec.attempt}"
        try:
            self._client.collection(self.COLLECTION).document(doc_id).set(
                _serialize_metric(rec)
            )
        except Exception:  # noqa: BLE001
            # Best-effort: never let observability break the pipeline.
            pass

    def flush(self) -> None:
        """No-op in write-through mode. Kept for API compatibility."""
        return None


def _serialize_metric(rec: MetricRecord) -> dict[str, Any]:
    d = asdict(rec)
    d["token_usages"] = [asdict(u) for u in rec.token_usages]
    return d
