"""Smoke tests for the DAG executor and pipeline definition.

These tests use a Noop state store and verify the layer ordering, fan-out
expansion, when-clause evaluation, and end-to-end stage execution without
needing any external services.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.contracts import StageContext, StageOutput, StageStatus
from orchestrator.core import (
    DagExecutor,
    WhenExpressionError,
    evaluate_when,
    load_pipeline,
    topological_order,
)
from orchestrator.registry import default_registry


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class _NoopState:
    def get_checkpoint(self, *a, **k):
        return None

    def save_checkpoint(self, *a, **k):
        return None


class _NoopLogger:
    def debug(self, *a, **k): pass
    info = warning = error = debug


def _make_factory(workdir: Path):
    def factory(spec, attempt):
        return StageContext(
            job_id="test", job_run_id="run-1", stage_id=spec.id, app="news",
            attempt=attempt, logger=_NoopLogger(), state=_NoopState(),
            record_tokens=lambda u: None, record_metric=lambda n, v: None,
            secrets={}, workdir=str(workdir),
        )
    return factory


# --------------------------------------------------------------------------- #
# Pure unit tests
# --------------------------------------------------------------------------- #

class TestEvaluateWhen:
    def test_score_above_threshold(self):
        assert evaluate_when("filter.score >= 9.0", {"filter": {"score": 9.5}}) is True

    def test_score_below_threshold(self):
        assert evaluate_when("filter.score >= 9.0", {"filter": {"score": 8.9}}) is False

    def test_score_at_threshold(self):
        assert evaluate_when("filter.score >= 9.0", {"filter": {"score": 9.0}}) is True

    def test_boolean_and(self):
        assert evaluate_when("a > 1 and b < 5", {"a": 2, "b": 3}) is True
        assert evaluate_when("a > 1 and b < 5", {"a": 0, "b": 3}) is False

    def test_unknown_name_raises(self):
        with pytest.raises(WhenExpressionError):
            evaluate_when("unknown.field >= 1", {})

    def test_function_call_disallowed(self):
        with pytest.raises(WhenExpressionError):
            evaluate_when("len(x) > 0", {"x": [1, 2, 3]})


class TestTopologicalOrder:
    def test_news_pipeline_layering(self, repo_root):
        pipe = load_pipeline(yaml.safe_load((repo_root / "src/apps/news/pipeline.yml").read_text()))
        layers = topological_order(pipe)
        layer_ids = [[s.id for s in L] for L in layers]
        assert layer_ids == [
            ["collect"],
            ["dedupe"],
            ["filter"],
            ["research"],
            ["report_markdown", "report_json", "meta_export"],
            ["upload"],
        ], f"unexpected layers: {layer_ids}"

    def test_cycle_detection(self):
        from orchestrator.core import Pipeline, StageSpec
        pipe = Pipeline(
            version="1.0",
            name="cyclic",
            stages=[
                StageSpec(id="a", use="stages.collectors.rss", depends_on=["b"]),
                StageSpec(id="b", use="stages.collectors.rss", depends_on=["a"]),
            ],
        )
        with pytest.raises(ValueError, match="cycle"):
            topological_order(pipe)


# --------------------------------------------------------------------------- #
# End-to-end pipeline execution (no Firestore)
# --------------------------------------------------------------------------- #

class TestPipelineExecution:
    def test_full_pipeline_succeeds(self, repo_root, mock_stages, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pipe = load_pipeline(yaml.safe_load((repo_root / "src/apps/news/pipeline.yml").read_text()))
        executor = DagExecutor(
            registry=default_registry,
            max_workers=2,
            context_factory=_make_factory(tmp_path),
        )
        result = executor.run(pipe, job_run_id="test-run")

        assert not result.failed, f"pipeline failed: {result.outputs}"
        expected_stages = {"collect", "dedupe", "filter", "research",
                           "report_markdown", "report_json", "meta_export", "upload"}
        assert set(result.outputs.keys()) == expected_stages
        for sid, out in result.outputs.items():
            assert out.status == StageStatus.SUCCESS, f"{sid} did not succeed: {out.error_message}"

    def test_when_clause_filters_research(self, repo_root, mock_stages, tmp_path, monkeypatch):
        """The mock LLM scores AI=9.5 and Weather=3.0; aggregate score=9.5
        triggers research, which keeps only items with score>=9.0."""
        monkeypatch.chdir(tmp_path)
        pipe = load_pipeline(yaml.safe_load((repo_root / "src/apps/news/pipeline.yml").read_text()))
        executor = DagExecutor(
            registry=default_registry,
            max_workers=2,
            context_factory=_make_factory(tmp_path),
        )
        result = executor.run(pipe, job_run_id="test-run")

        research_out = result.outputs["research"]
        assert research_out.status == StageStatus.SUCCESS
        items = research_out.payload["items"]
        assert len(items) == 1, f"expected 1 high-score item, got {len(items)}"
        assert "AI" in items[0]["title"]

    def test_filter_aggregate_score_present(self, repo_root, mock_stages, tmp_path, monkeypatch):
        """fan_out merge must reconstruct an aggregate `score` field on the
        filter stage's output so the `when:` clause on research can read it."""
        monkeypatch.chdir(tmp_path)
        pipe = load_pipeline(yaml.safe_load((repo_root / "src/apps/news/pipeline.yml").read_text()))
        executor = DagExecutor(
            registry=default_registry,
            max_workers=2,
            context_factory=_make_factory(tmp_path),
        )
        result = executor.run(pipe, job_run_id="test-run")

        filter_out = result.outputs["filter"]
        assert filter_out.status == StageStatus.SUCCESS
        assert "score" in filter_out.payload, "fan_out must produce aggregate score"
        assert filter_out.payload["score"] == 9.5
        assert len(filter_out.payload["items"]) == 2  # both items survive filter (research narrows)
