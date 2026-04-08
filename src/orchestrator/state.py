"""Firestore-backed state management and retry bookkeeping.

SYSTEM_DESIGN.md §2.3 に基づき、以下の Firestore コレクションを管理する:

  - job_runs     : 1 ジョブ起動あたり 1 ドキュメント
  - stage_runs   : 1 ステージ実行あたり 1 ドキュメント (親: job_run_id)
  - metrics      : 観測データ(observability.py が書き込み)

本モジュールは Firestore クライアントの具体実装を隠蔽し、
contracts.StateStore Protocol に適合する FirestoreStateStore を提供する。
Firebase Admin SDK は呼び出し側で初期化して注入する(テスト容易性のため)。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from agent.contracts import FailureCategory, StageStatus


# --------------------------------------------------------------------------- #
# Data classes written to Firestore
# --------------------------------------------------------------------------- #

@dataclass
class JobRunRecord:
    job_run_id: str
    job_id: str
    app: str
    status: StageStatus
    trigger_type: str  # schedule | manual | webhook
    started_at: datetime
    finished_at: datetime | None = None
    attempt: int = 1
    error_message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageRunRecord:
    job_run_id: str
    stage_id: str
    use: str
    status: StageStatus
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int = 0
    error_category: FailureCategory | None = None
    error_message: str | None = None
    checkpoint: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Firestore client abstraction (tiny subset we depend on)
# --------------------------------------------------------------------------- #

class FirestoreClient(Protocol):
    def collection(self, name: str) -> "FirestoreCollection": ...


class FirestoreCollection(Protocol):
    def document(self, doc_id: str) -> "FirestoreDocument": ...


class FirestoreDocument(Protocol):
    def set(self, data: Mapping[str, Any], merge: bool = False) -> None: ...
    def get(self) -> "FirestoreSnapshot": ...
    def update(self, data: Mapping[str, Any]) -> None: ...


class FirestoreSnapshot(Protocol):
    @property
    def exists(self) -> bool: ...
    def to_dict(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# Store implementation
# --------------------------------------------------------------------------- #

class FirestoreStateStore:
    """`contracts.StateStore` に適合する Firestore 実装。"""

    JOB_RUNS = "job_runs"
    STAGE_RUNS = "stage_runs"

    def __init__(self, client: FirestoreClient) -> None:
        self._client = client

    # ---------------- Job run lifecycle ---------------- #

    def start_job_run(self, record: JobRunRecord) -> None:
        self._client.collection(self.JOB_RUNS).document(record.job_run_id).set(
            _serialize(asdict(record))
        )

    def finish_job_run(
        self,
        job_run_id: str,
        status: StageStatus,
        error_message: str | None = None,
    ) -> None:
        self._client.collection(self.JOB_RUNS).document(job_run_id).update(
            _serialize(
                {
                    "status": status.value,
                    "finished_at": datetime.now(timezone.utc),
                    "error_message": error_message,
                }
            )
        )

    # ---------------- Stage run lifecycle ---------------- #

    def start_stage_run(self, record: StageRunRecord) -> None:
        doc_id = f"{record.job_run_id}__{record.stage_id}__{record.attempt}"
        self._client.collection(self.STAGE_RUNS).document(doc_id).set(
            _serialize(asdict(record))
        )

    def finish_stage_run(
        self,
        job_run_id: str,
        stage_id: str,
        attempt: int,
        status: StageStatus,
        duration_ms: int,
        error_category: FailureCategory | None = None,
        error_message: str | None = None,
    ) -> None:
        doc_id = f"{job_run_id}__{stage_id}__{attempt}"
        self._client.collection(self.STAGE_RUNS).document(doc_id).update(
            _serialize(
                {
                    "status": status.value,
                    "finished_at": datetime.now(timezone.utc),
                    "duration_ms": duration_ms,
                    "error_category": error_category.value if error_category else None,
                    "error_message": error_message,
                }
            )
        )

    # ---------------- Engine-facing duck-typed shims ---------------- #
    #
    # DagExecutor calls these via hasattr() so the Protocol stays minimal
    # (only get_checkpoint / save_checkpoint are required of all stores).
    # Both methods are best-effort: write failures are swallowed by the engine.

    def record_stage_run_start(
        self,
        job_run_id: str,
        stage_id: str,
        use: str,
        attempt: int,
    ) -> None:
        self.start_stage_run(
            StageRunRecord(
                job_run_id=job_run_id,
                stage_id=stage_id,
                use=use,
                status=StageStatus.RUNNING,
                attempt=attempt,
                started_at=datetime.now(timezone.utc),
            )
        )

    def record_stage_run_finish(
        self,
        job_run_id: str,
        stage_id: str,
        attempt: int,
        status: StageStatus,
        duration_ms: int,
        error_category: FailureCategory | None = None,
        error_message: str | None = None,
    ) -> None:
        self.finish_stage_run(
            job_run_id=job_run_id,
            stage_id=stage_id,
            attempt=attempt,
            status=status,
            duration_ms=duration_ms,
            error_category=error_category,
            error_message=error_message,
        )

    # ---------------- Checkpoint API (contracts.StateStore) ---------------- #

    def get_checkpoint(
        self, job_run_id: str, stage_id: str
    ) -> Mapping[str, Any] | None:
        # 最新 attempt を検索する実装は本番実装で追加。MVP では最新 attempt=1 を見る。
        doc_id = f"{job_run_id}__{stage_id}__latest"
        snap = self._client.collection(self.STAGE_RUNS).document(doc_id).get()
        if not snap.exists:
            return None
        return snap.to_dict().get("checkpoint")

    def save_checkpoint(
        self, job_run_id: str, stage_id: str, data: Mapping[str, Any]
    ) -> None:
        doc_id = f"{job_run_id}__{stage_id}__latest"
        self._client.collection(self.STAGE_RUNS).document(doc_id).set(
            _serialize({"checkpoint": dict(data), "updated_at": datetime.now(timezone.utc)}),
            merge=True,
        )


# --------------------------------------------------------------------------- #
# Retry decision helper (used by core.py if it delegates decisions to state)
# --------------------------------------------------------------------------- #

def classify_exception(exc: BaseException) -> FailureCategory:
    """例外から FailureCategory を推定する。アプリ固有例外は別途追加。"""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return FailureCategory.TIMEOUT
    if "ratelimit" in name or "429" in str(exc):
        return FailureCategory.RATE_LIMIT
    if "connection" in name or "temporar" in name:
        return FailureCategory.TRANSIENT
    return FailureCategory.PERMANENT


# --------------------------------------------------------------------------- #
# Serialization helper
# --------------------------------------------------------------------------- #

def _serialize(data: Mapping[str, Any]) -> dict[str, Any]:
    """datetime/Enum を Firestore 書き込み可能な形式に正規化。"""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if v is None:
            out[k] = None
        elif isinstance(v, datetime):
            out[k] = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        elif hasattr(v, "value") and hasattr(type(v), "__members__"):  # Enum
            out[k] = v.value
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = v
    return out
