"""DAG execution engine for the agent platform.

SYSTEM_DESIGN.md §2.1 / §3.1 に基づく実行制御中核。

責務:
  1. pipeline.yml(dict) の読み込みと DAG 構築(トポロジカルソート + 循環検出)。
  2. `depends_on` に従った順次/並列実行。
  3. `when` 式の評価(安全な式評価器)。
  4. `parallel=true` / `fan_out` による要素ごと展開。
  5. ステージ単位の checkpoint 保存とリトライ判定(state.py への委譲)。
  6. StageOutput の収集と後続ステージへの受け渡し。

非責務:
  - スケジューリング(scheduler.py)
  - Firestore I/O の具体実装(state.py)
  - コスト集計の具体実装(observability.py)
"""

from __future__ import annotations

import ast
import operator as op
import time
from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.contracts import (
    FailureCategory,
    Stage,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
)
from orchestrator.registry import StageRegistry, default_registry


# --------------------------------------------------------------------------- #
# Safe expression evaluator for `when:` clauses
# --------------------------------------------------------------------------- #

_ALLOWED_BINOPS: dict[type, Any] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Mod: op.mod,
}
_ALLOWED_CMPOPS: dict[type, Any] = {
    ast.Eq: op.eq,
    ast.NotEq: op.ne,
    ast.Lt: op.lt,
    ast.LtE: op.le,
    ast.Gt: op.gt,
    ast.GtE: op.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_ALLOWED_BOOLOPS: dict[type, Any] = {
    ast.And: all,
    ast.Or: any,
}


class WhenExpressionError(ValueError):
    pass


def evaluate_when(expr: str, scope: Mapping[str, Any]) -> bool:
    """`filter.score >= 9.0` のような式を安全に評価する。

    許可: 比較演算、論理演算、算術演算、属性参照(dot-path)、リテラル、リスト、タプル。
    禁止: 関数呼び出し、lambda、import、属性代入、名前空間汚染。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise WhenExpressionError(f"Invalid when expression {expr!r}: {e}") from e

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in scope:
                raise WhenExpressionError(f"Unknown name in when: {node.id}")
            return scope[node.id]
        if isinstance(node, ast.Attribute):
            target = _eval(node.value)
            if isinstance(target, Mapping):
                if node.attr not in target:
                    raise WhenExpressionError(f"Missing key: {node.attr}")
                return target[node.attr]
            return getattr(target, node.attr)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.Not)):
            v = _eval(node.operand)
            return -v if isinstance(node.op, ast.USub) else (not v)
        if isinstance(node, ast.BoolOp) and type(node.op) in _ALLOWED_BOOLOPS:
            return _ALLOWED_BOOLOPS[type(node.op)](_eval(v) for v in node.values)
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for cmp_op, comparator in zip(node.ops, node.comparators):
                if type(cmp_op) not in _ALLOWED_CMPOPS:
                    raise WhenExpressionError(f"Operator not allowed: {cmp_op}")
                right = _eval(comparator)
                if not _ALLOWED_CMPOPS[type(cmp_op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, (ast.List, ast.Tuple)):
            return [_eval(e) for e in node.elts]
        raise WhenExpressionError(f"Disallowed AST node: {type(node).__name__}")

    result = _eval(tree)
    return bool(result)


# --------------------------------------------------------------------------- #
# Pipeline data structures
# --------------------------------------------------------------------------- #

@dataclass
class StageSpec:
    id: str
    use: str
    depends_on: list[str] = field(default_factory=list)
    when: str | None = None
    parallel: bool = False
    fan_out: str | None = None
    checkpoint: bool = True
    timeout_seconds: int | None = None
    retry: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pipeline:
    version: str
    name: str
    stages: list[StageSpec]
    defaults: dict[str, Any] = field(default_factory=dict)


def load_pipeline(doc: Mapping[str, Any]) -> Pipeline:
    stages = [
        StageSpec(
            id=s["id"],
            use=s["use"],
            depends_on=list(s.get("depends_on", []) or []),
            when=s.get("when"),
            parallel=bool(s.get("parallel", False)),
            fan_out=s.get("fan_out"),
            checkpoint=bool(s.get("checkpoint", True)),
            timeout_seconds=s.get("timeout_seconds"),
            retry=dict(s.get("retry", {}) or {}),
            config=dict(s.get("config", {}) or {}),
        )
        for s in doc["stages"]
    ]
    return Pipeline(
        version=str(doc["version"]),
        name=str(doc.get("name", "")),
        stages=stages,
        defaults=dict(doc.get("defaults", {}) or {}),
    )


def topological_order(pipeline: Pipeline) -> list[list[StageSpec]]:
    """層ごとの実行単位(同層は並列実行可)を返す。循環があれば例外。"""
    by_id = {s.id: s for s in pipeline.stages}
    indeg: dict[str, int] = {s.id: 0 for s in pipeline.stages}
    children: dict[str, list[str]] = defaultdict(list)
    for s in pipeline.stages:
        for dep in s.depends_on:
            if dep not in by_id:
                raise ValueError(f"Stage {s.id!r} depends on unknown stage {dep!r}")
            indeg[s.id] += 1
            children[dep].append(s.id)

    layers: list[list[StageSpec]] = []
    frontier = deque([sid for sid, d in indeg.items() if d == 0])
    seen = 0
    while frontier:
        layer = list(frontier)
        frontier.clear()
        layers.append([by_id[sid] for sid in layer])
        seen += len(layer)
        for sid in layer:
            for child in children[sid]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    frontier.append(child)
    if seen != len(pipeline.stages):
        raise ValueError("Pipeline contains a cycle")
    return layers


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #

@dataclass
class ExecutionResult:
    job_run_id: str
    outputs: dict[str, StageOutput[Any]]
    failed: bool


class DagExecutor:
    def __init__(
        self,
        registry: StageRegistry | None = None,
        max_workers: int = 4,
        context_factory: Any = None,
    ) -> None:
        self._registry = registry or default_registry
        self._max_workers = max_workers
        self._context_factory = context_factory  # Callable[[StageSpec, int], StageContext]

    def run(self, pipeline: Pipeline, job_run_id: str) -> ExecutionResult:
        layers = topological_order(pipeline)
        outputs: dict[str, StageOutput[Any]] = {}
        failed = False

        for layer in layers:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures: dict[Future[StageOutput[Any]], StageSpec] = {}
                for spec in layer:
                    # when 式評価
                    if spec.when and not self._evaluate_when(spec.when, outputs):
                        outputs[spec.id] = StageOutput(status=StageStatus.SKIPPED)
                        continue
                    # 親の失敗は子を SKIP
                    if any(
                        outputs.get(dep, StageOutput(status=StageStatus.FAILED)).status
                        != StageStatus.SUCCESS
                        and outputs.get(dep) is not None
                        and outputs[dep].status == StageStatus.FAILED
                        for dep in spec.depends_on
                    ):
                        outputs[spec.id] = StageOutput(status=StageStatus.SKIPPED)
                        continue
                    futures[pool.submit(self._run_stage, spec, outputs)] = spec

                for fut, spec in futures.items():
                    result = fut.result()
                    outputs[spec.id] = result
                    if result.status == StageStatus.FAILED:
                        failed = True

        return ExecutionResult(job_run_id=job_run_id, outputs=outputs, failed=failed)

    # ------------------------------------------------------------------ #
    # Stage execution (retry, fan-out, checkpoint hook)
    # ------------------------------------------------------------------ #

    def _run_stage(
        self,
        spec: StageSpec,
        outputs: Mapping[str, StageOutput[Any]],
    ) -> StageOutput[Any]:
        max_retries = int(spec.retry.get("max", 0))
        attempt = 0
        last_error: StageOutput[Any] | None = None

        while attempt <= max_retries:
            attempt += 1
            self._record_stage_start(spec, attempt)
            attempt_started = time.monotonic()
            if spec.parallel and spec.fan_out:
                result = self._invoke_fanout(spec, outputs, attempt)
            else:
                result = self._invoke_once(spec, outputs, attempt)
            duration_ms = int((time.monotonic() - attempt_started) * 1000)
            self._record_stage_finish(spec, attempt, result, duration_ms)

            if result.status == StageStatus.SUCCESS:
                if spec.checkpoint:
                    self._save_checkpoint(spec, attempt, result)
                return result

            last_error = result
            if not self._should_retry(result, spec, attempt, max_retries):
                break
            self._sleep_backoff(spec, attempt)

        return last_error or StageOutput(status=StageStatus.FAILED)

    def _record_stage_start(self, spec: StageSpec, attempt: int) -> None:
        """Best-effort: write a stage_run RUNNING record via the state store
        if it exposes the duck-typed lifecycle method."""
        try:
            ctx = self._build_context(spec, attempt)
            recorder = getattr(ctx.state, "record_stage_run_start", None)
            if recorder is not None:
                recorder(ctx.job_run_id, spec.id, spec.use, attempt)
        except Exception:  # noqa: BLE001
            pass

    def _record_stage_finish(
        self,
        spec: StageSpec,
        attempt: int,
        result: StageOutput[Any],
        duration_ms: int,
    ) -> None:
        try:
            ctx = self._build_context(spec, attempt)
            recorder = getattr(ctx.state, "record_stage_run_finish", None)
            if recorder is not None:
                recorder(
                    ctx.job_run_id,
                    spec.id,
                    attempt,
                    result.status,
                    duration_ms,
                    result.error_category,
                    result.error_message,
                )
        except Exception:  # noqa: BLE001
            pass

    def _invoke_once(
        self,
        spec: StageSpec,
        outputs: Mapping[str, StageOutput[Any]],
        attempt: int,
    ) -> StageOutput[Any]:
        """Single invocation of the stage with a full payload."""
        stage: Stage = self._registry.resolve(spec.use)
        ctx = self._build_context(spec, attempt)
        started = time.monotonic()
        payload = self._build_payload(spec, outputs)
        stage_input: StageInput[Any] = StageInput(
            payload=payload,
            upstream={k: v for k, v in outputs.items() if k in spec.depends_on},
            config=spec.config,
        )
        try:
            return stage.run(ctx, stage_input)
        except Exception as e:  # noqa: BLE001
            now = datetime.now(timezone.utc)
            return StageOutput(
                status=StageStatus.FAILED,
                error_category=FailureCategory.TRANSIENT,
                error_message=f"{type(e).__name__}: {e}",
                metrics=StageMetrics(
                    started_at=now,
                    finished_at=now,
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
            )

    def _invoke_fanout(
        self,
        spec: StageSpec,
        outputs: Mapping[str, StageOutput[Any]],
        attempt: int,
    ) -> StageOutput[Any]:
        """Fan-out invocation: expand payload[fan_out] into per-element inputs
        and run the stage in parallel across elements."""
        stage: Stage = self._registry.resolve(spec.use)
        base_payload = self._build_payload(spec, outputs)
        fan_key = spec.fan_out or "items"

        if not isinstance(base_payload, dict) or not isinstance(base_payload.get(fan_key), list):
            # Fall back to single invocation if the fan-out key is absent/invalid.
            return self._invoke_once(spec, outputs, attempt)

        elements: list[Any] = base_payload[fan_key]
        upstream_map = {k: v for k, v in outputs.items() if k in spec.depends_on}
        started = time.monotonic()
        started_dt = datetime.now(timezone.utc)

        def _one(element: Any) -> StageOutput[Any]:
            ctx = self._build_context(spec, attempt)
            element_payload = {**base_payload, fan_key: [element]}
            stage_input: StageInput[Any] = StageInput(
                payload=element_payload,
                upstream=upstream_map,
                config=spec.config,
            )
            try:
                return stage.run(ctx, stage_input)
            except Exception as e:  # noqa: BLE001
                now = datetime.now(timezone.utc)
                return StageOutput(
                    status=StageStatus.FAILED,
                    error_category=FailureCategory.TRANSIENT,
                    error_message=f"{type(e).__name__}: {e}",
                    metrics=StageMetrics(started_at=now, finished_at=now),
                )

        results: list[StageOutput[Any]] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for r in pool.map(_one, elements):
                results.append(r)

        # Merge: concatenate successful element outputs' fan_key lists.
        merged_items: list[Any] = []
        merged_tokens: list[Any] = []
        merged_custom: dict[str, Any] = {"fanout_errors": []}
        any_failed = False
        for r in results:
            if r.status != StageStatus.SUCCESS:
                any_failed = True
                merged_custom["fanout_errors"].append(r.error_message)
                continue
            if isinstance(r.payload, dict):
                merged_items.extend(r.payload.get(fan_key, []))
            merged_tokens.extend(r.metrics.token_usage)

        finished_dt = datetime.now(timezone.utc)
        merged_metrics = StageMetrics(
            started_at=started_dt,
            finished_at=finished_dt,
            duration_ms=int((time.monotonic() - started) * 1000),
            token_usage=merged_tokens,
            custom=merged_custom,
        )

        # Preserve aggregate keys (e.g. "score" set by llm_score) by computing
        # a max across successful element payloads when present.
        aggregate: dict[str, Any] = {fan_key: merged_items}
        if merged_items and all(isinstance(i, dict) and "score" in i for i in merged_items):
            aggregate["score"] = max(float(i["score"]) for i in merged_items)

        if any_failed and not merged_items:
            return StageOutput(
                status=StageStatus.FAILED,
                error_category=FailureCategory.TRANSIENT,
                error_message="all fan-out elements failed",
                metrics=merged_metrics,
            )
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload=aggregate,
            metrics=merged_metrics,
        )

    def _save_checkpoint(
        self, spec: StageSpec, attempt: int, result: StageOutput[Any]
    ) -> None:
        """Persist the stage output payload as a checkpoint. Best-effort:
        checkpoint failures never fail the stage."""
        try:
            ctx = self._build_context(spec, attempt)
            payload = result.payload if isinstance(result.payload, dict) else {"value": result.payload}
            ctx.state.save_checkpoint(ctx.job_run_id, spec.id, payload)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _build_payload(
        spec: StageSpec, outputs: Mapping[str, StageOutput[Any]]
    ) -> Any:
        if not spec.depends_on:
            return None
        if len(spec.depends_on) == 1:
            return outputs[spec.depends_on[0]].payload
        return {dep: outputs[dep].payload for dep in spec.depends_on}

    def _build_context(self, spec: StageSpec, attempt: int) -> StageContext:
        if self._context_factory is None:
            raise RuntimeError(
                "DagExecutor.context_factory is not configured. "
                "Inject a factory that builds StageContext from spec+attempt."
            )
        return self._context_factory(spec, attempt)  # type: ignore[no-any-return]

    @staticmethod
    def _should_retry(
        result: StageOutput[Any], spec: StageSpec, attempt: int, max_retries: int
    ) -> bool:
        if attempt > max_retries:
            return False
        retry_on = spec.retry.get(
            "retry_on", ["transient", "rate_limit", "timeout"]
        )
        if "any" in retry_on:
            return True
        cat = (result.error_category.value if result.error_category else "permanent")
        return cat in retry_on

    @staticmethod
    def _sleep_backoff(spec: StageSpec, attempt: int) -> None:
        initial = float(spec.retry.get("initial_delay_seconds", 10))
        backoff = spec.retry.get("backoff", "exponential")
        delay = initial * (2 ** (attempt - 1)) if backoff == "exponential" else initial * attempt
        time.sleep(min(delay, 300.0))

    @staticmethod
    def _evaluate_when(
        expr: str, outputs: Mapping[str, StageOutput[Any]]
    ) -> bool:
        scope = {sid: out.payload for sid, out in outputs.items() if out.payload is not None}
        return evaluate_when(expr, scope)
