"""Job scheduler entry point (Phase 5: real Firestore injection).

SYSTEM_DESIGN.md §2.2。GitHub Actions の単一ワークフローから
`python -m orchestrator.scheduler --job <id>` で呼び出される都度起動型エントリ。

責務:
  1. config/jobs.yml を読み込み、`--job` で指定されたジョブを解決。
  2. 対応する pipeline.yml を読み込み、DagExecutor に委譲。
  3. Firebase/Firestore 実接続を構築し、State / Observability に注入。
  4. JobRun ライフサイクル(start/finish)を Firestore に記録。
  5. 終了時に MetricsSink を flush してコスト情報を永続化。

Firebase 未導入環境では自動的に Noop 実装にフォールバックするため、
ローカルでの dry-run やスモークテストでも本スクリプトは正常終了する。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from agent.contracts import StageContext, StageStatus
from orchestrator.core import DagExecutor, load_pipeline
from orchestrator.observability import MetricsSink, get_logger
from orchestrator.registry import default_registry
from orchestrator.state import FirestoreStateStore, JobRunRecord


# --------------------------------------------------------------------------- #
# YAML / jobs.yml helpers
# --------------------------------------------------------------------------- #

def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_job(jobs_doc: Mapping[str, Any], job_id: str) -> Mapping[str, Any]:
    for job in jobs_doc.get("jobs", []):
        if job["id"] == job_id:
            return job
    raise SystemExit(f"Job not found in jobs.yml: {job_id}")


# --------------------------------------------------------------------------- #
# Firebase initialization (with graceful fallback)
# --------------------------------------------------------------------------- #

def _init_firestore(logger: Any) -> Any:
    """Return a Firestore client or None if unavailable.

    Resolution order:
      1. `FIREBASE_SERVICE_ACCOUNT` env var (raw JSON string or path to JSON).
      2. `GOOGLE_APPLICATION_CREDENTIALS` env var (standard path).
      3. Fall back to None → Noop state store.
    """
    try:
        import firebase_admin  # type: ignore
        from firebase_admin import credentials, firestore  # type: ignore
    except ImportError:
        logger.warning("firestore.unavailable", extras={"reason": "firebase_admin not installed"})
        return None

    if firebase_admin._apps:  # type: ignore[attr-defined]
        return firestore.client()

    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    try:
        if raw.startswith("{"):
            # Inline JSON (typical in GitHub Actions secrets).
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                tmp.write(raw)
                sa_path = tmp.name
            cred = credentials.Certificate(sa_path)
            firebase_admin.initialize_app(cred)
        elif raw and Path(raw).exists():
            cred = credentials.Certificate(raw)
            firebase_admin.initialize_app(cred)
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            firebase_admin.initialize_app()
        else:
            logger.warning("firestore.no_credentials", extras={})
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("firestore.init_failed", extras={"error": str(e)})
        return None

    return firestore.client()


# --------------------------------------------------------------------------- #
# Noop implementations (used when Firestore is unavailable)
# --------------------------------------------------------------------------- #

class _NoopStateStore:
    def get_checkpoint(self, job_run_id: str, stage_id: str):
        return None

    def save_checkpoint(self, job_run_id: str, stage_id: str, data):
        return None


class _NoopFirestore:
    """Enough of the Firestore interface for MetricsSink to no-op."""

    def collection(self, _name: str):
        return self

    def document(self, _doc_id: str):
        return self

    def set(self, *_args, **_kwargs):
        return None

    def update(self, *_args, **_kwargs):
        return None

    def get(self):
        return _NoopSnap()


class _NoopSnap:
    exists = False

    def to_dict(self):
        return {}


# --------------------------------------------------------------------------- #
# Stage context factory
# --------------------------------------------------------------------------- #

def _build_context_factory(
    *,
    job_id: str,
    job_run_id: str,
    app: str,
    secrets: Mapping[str, str],
    workdir: Path,
    metrics: MetricsSink,
    state_store: Any,
):
    log = get_logger(f"stage.{app}")

    def factory(spec: Any, attempt: int) -> StageContext:
        def record_tokens(usage: Any) -> None:
            from orchestrator.observability import MetricRecord

            metrics.record(
                MetricRecord(
                    job_run_id=job_run_id,
                    stage_id=spec.id,
                    attempt=attempt,
                    token_usages=[usage],
                )
            )

        def record_metric(name: str, value: float) -> None:
            log.info(
                "metric",
                extra={"extras": {"name": name, "value": value, "stage": spec.id}},
            )

        return StageContext(
            job_id=job_id,
            job_run_id=job_run_id,
            stage_id=spec.id,
            app=app,
            attempt=attempt,
            logger=log,  # type: ignore[arg-type]
            state=state_store,
            record_tokens=record_tokens,
            record_metric=record_metric,
            secrets=secrets,
            workdir=str(workdir),
        )

    return factory


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent platform job runner")
    parser.add_argument("--job", required=True, help="Job id from config/jobs.yml")
    parser.add_argument("--jobs-file", default="config/jobs.yml")
    parser.add_argument("--workdir", default="./.workdir")
    args = parser.parse_args(argv)

    logger = get_logger("scheduler")
    jobs_path = Path(args.jobs_file)
    jobs_doc = _load_yaml(jobs_path)
    job = _find_job(jobs_doc, args.job)

    pipeline_path = Path(job["pipeline"])
    pipeline_doc = _load_yaml(pipeline_path)
    pipeline = load_pipeline(pipeline_doc)

    job_run_id = (
        f"{job['id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"-{uuid.uuid4().hex[:6]}"
    )
    logger.info(
        "job.start",
        extra={
            "extras": {
                "job_run_id": job_run_id,
                "job_id": job["id"],
                "pipeline": str(pipeline_path),
            }
        },
    )

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # Secrets: pass only AGENT_*, GEMINI_*, OPENAI_*, CLOUDFLARE_*, FIREBASE_* through.
    allowed_prefixes = ("AGENT_", "GEMINI_", "OPENAI_", "CLOUDFLARE_", "FIREBASE_")
    secrets: dict[str, str] = {
        k: v
        for k, v in os.environ.items()
        if any(k.startswith(p) for p in allowed_prefixes)
    }

    # Firestore wiring (with graceful fallback).
    firestore_client = _init_firestore(logger)
    if firestore_client is not None:
        state_store: Any = FirestoreStateStore(firestore_client)
        metrics_client: Any = firestore_client
        logger.info("firestore.ready", extra={"extras": {}})
    else:
        state_store = _NoopStateStore()
        metrics_client = _NoopFirestore()
        logger.info("firestore.noop", extra={"extras": {"reason": "fallback"}})

    metrics = MetricsSink(firestore_client=metrics_client)

    # Record job_run start in Firestore (best-effort).
    if isinstance(state_store, FirestoreStateStore):
        trigger_type = job.get("trigger", {}).get("type", "manual")
        try:
            state_store.start_job_run(
                JobRunRecord(
                    job_run_id=job_run_id,
                    job_id=job["id"],
                    app=job["app"],
                    status=StageStatus.RUNNING,
                    trigger_type=trigger_type,
                    started_at=datetime.now(timezone.utc),
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("job_run.start_write_failed", extra={"extras": {"error": str(e)}})

    executor = DagExecutor(
        registry=default_registry,
        max_workers=int(os.environ.get("AGENT_MAX_WORKERS", "4")),
        context_factory=_build_context_factory(
            job_id=job["id"],
            job_run_id=job_run_id,
            app=job["app"],
            secrets=secrets,
            workdir=workdir,
            metrics=metrics,
            state_store=state_store,
        ),
    )

    result = None
    try:
        result = executor.run(pipeline, job_run_id=job_run_id)
    finally:
        metrics.flush()
        if isinstance(state_store, FirestoreStateStore):
            final_status = StageStatus.FAILED if (result is None or result.failed) else StageStatus.SUCCESS
            try:
                state_store.finish_job_run(
                    job_run_id,
                    status=final_status,
                    error_message=None if final_status == StageStatus.SUCCESS else "pipeline reported failures",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("job_run.finish_write_failed", extra={"extras": {"error": str(e)}})

    logger.info(
        "job.finish",
        extra={
            "extras": {
                "job_run_id": job_run_id,
                "failed": bool(result is None or result.failed),
                "stage_count": len(result.outputs) if result else 0,
                "statuses": (
                    {k: v.status.value for k, v in result.outputs.items()} if result else {}
                ),
            }
        },
    )
    return 1 if (result is None or result.failed) else 0


if __name__ == "__main__":
    sys.exit(main())
