"""Stage I/O contract definitions for the generic agent platform.

SYSTEM_DESIGN.md §3.2 に対応するステージ入出力契約の草案。
各ステージは (StageContext, StageInput) を受け取り StageOutput を返す純関数的インタフェース。
副作用(DB 書き込み、外部 API 呼び出しのロギング、コスト計上)はすべて
StageContext 経由でオーケストレータに委譲する。

本モジュールは基盤(src/agent/)配下であり、アプリ固有の語彙(news, RSS 等)を
含めてはならない(DEVELOPMENT_RULES.md §1 基盤汚染の禁止)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar


# --------------------------------------------------------------------------- #
# 1. Status enums
# --------------------------------------------------------------------------- #

class StageStatus(str, Enum):
    """SYSTEM_DESIGN.md §2.3 に準拠した実行ステータス。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class FailureCategory(str, Enum):
    """リトライ判定に使用する失敗カテゴリ。jobs.schema.json の retry_on と対応。"""

    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PERMANENT = "permanent"


# --------------------------------------------------------------------------- #
# 2. Observability primitives (SYSTEM_DESIGN.md §2.4)
# --------------------------------------------------------------------------- #

@dataclass
class TokenUsage:
    """LLM トークン使用量。observability.py が集計し Firestore metrics に書き込む。"""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class StageMetrics:
    """ステージ単位で収集する観測データ。"""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    token_usage: list[TokenUsage] = field(default_factory=list)
    artifact_bytes: int = 0
    custom: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 3. Stage execution context (orchestrator → stage の唯一の副作用窓口)
# --------------------------------------------------------------------------- #

class StateStore(Protocol):
    """Firestore ベースの状態ストア抽象。state.py が実装する。"""

    def get_checkpoint(self, job_run_id: str, stage_id: str) -> Mapping[str, Any] | None: ...
    def save_checkpoint(self, job_run_id: str, stage_id: str, data: Mapping[str, Any]) -> None: ...


class Logger(Protocol):
    def debug(self, msg: str, **kwargs: Any) -> None: ...
    def info(self, msg: str, **kwargs: Any) -> None: ...
    def warning(self, msg: str, **kwargs: Any) -> None: ...
    def error(self, msg: str, **kwargs: Any) -> None: ...


@dataclass
class StageContext:
    """ステージに渡される実行コンテキスト。

    ステージは本オブジェクトを通じてのみ副作用を行う。直接 Firestore や
    外部 API のクライアントを初期化してはならない。
    """

    job_id: str
    job_run_id: str
    stage_id: str
    app: str
    attempt: int
    logger: Logger
    state: StateStore
    record_tokens: Callable[[TokenUsage], None]
    record_metric: Callable[[str, float], None]
    secrets: Mapping[str, str]
    workdir: str


# --------------------------------------------------------------------------- #
# 4. Stage I/O envelopes
# --------------------------------------------------------------------------- #

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


@dataclass
class StageInput(Generic[TInput]):
    """オーケストレータが依存先ステージの出力を束ねて渡すエンベロープ。

    payload には当該ステージの直接入力(通常は直前ステージ出力)、
    upstream には id → StageOutput のマップが格納される。when 式評価時にも参照される。
    """

    payload: TInput
    upstream: Mapping[str, "StageOutput[Any]"] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class StageOutput(Generic[TOutput]):
    """ステージの返却エンベロープ。"""

    status: StageStatus
    payload: TOutput | None = None
    metrics: StageMetrics = field(default_factory=StageMetrics)
    error_category: FailureCategory | None = None
    error_message: str | None = None


# --------------------------------------------------------------------------- #
# 5. Stage protocol
# --------------------------------------------------------------------------- #

class Stage(Protocol, Generic[TInput, TOutput]):
    """すべてのステージ部品が満たすべきインタフェース。"""

    name: str

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[TInput],
    ) -> StageOutput[TOutput]: ...


# --------------------------------------------------------------------------- #
# 6. Canonical payload dataclasses (基盤汎用の中間データ型)
# --------------------------------------------------------------------------- #
#
# これらは特定アプリに属さない汎用 DTO。ニュース固有フィールドは apps/news
# 側で拡張サブクラスを用意する(基盤汚染の禁止)。
# --------------------------------------------------------------------------- #

@dataclass
class CollectedItem:
    """Collector ステージの出力単位。"""

    id: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    content: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ScoredItem:
    """Filter ステージの出力単位。"""

    item: CollectedItem
    score: float
    reason: str
    topics: list[str] = field(default_factory=list)


@dataclass
class ResearchedItem:
    """Researcher ステージの出力単位。"""

    item: ScoredItem
    summary: str
    citations: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class ReportArtifact:
    """Reporter ステージの出力単位。Distribution Layer へ引き渡される。"""

    format: str  # "markdown" | "json" | その他
    path: str
    bytes: int
    checksum: str | None = None
