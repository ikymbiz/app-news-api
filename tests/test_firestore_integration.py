"""Integration test: scheduler.main() + DagExecutor + FirestoreStateStore.

Uses an in-memory FakeFirestore (from conftest.py) injected into
`scheduler._init_firestore` via monkeypatch. Verifies that:
  - job_runs / stage_runs / metrics collections are written by the runtime
  - meta_export reads them back via where().order_by().limit().stream()
  - the dumped JSON files contain non-zero counts

This test is the regression guard that caught the `extras=` logger bug
in Phase 6. It enforces the "real logger through the real codepath"
discipline that prevents Mock anti-patterns from hiding errors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def integration_run(monkeypatch, fake_firestore, mock_stages, repo_root, chdir_repo):
    """Run scheduler.main() against the fake firestore and return the
    populated FakeFirestore plus the meta directory path."""
    from orchestrator import scheduler

    monkeypatch.setattr(scheduler, "_init_firestore", lambda logger: fake_firestore)

    rc = scheduler.main([
        "--job", "news-manual",
        "--jobs-file", "config/jobs.yml",
        "--workdir", "./.workdir-test",
    ])
    return rc, fake_firestore, repo_root / "artifacts" / "news" / "meta"


class TestFirestoreIntegration:
    def test_scheduler_exits_zero(self, integration_run):
        rc, _, _ = integration_run
        assert rc == 0, "scheduler should exit with status 0 on full success"

    def test_job_run_recorded(self, integration_run):
        _, fs, _ = integration_run
        runs = fs._collections.get("job_runs", {})
        assert len(runs) == 1, f"expected 1 job_run, got {len(runs)}"
        run = next(iter(runs.values()))
        assert run.get("status") == "success"
        assert run.get("job_id") == "news-manual"
        assert run.get("app") == "news"

    def test_stage_runs_recorded(self, integration_run):
        _, fs, _ = integration_run
        stage_runs = fs._collections.get("stage_runs", {})
        # Engine writes 8 stage_run docs (one per stage, attempt=1) + checkpoint
        # docs keyed `*__latest`. Filter to lifecycle docs only.
        lifecycle = {
            k: v for k, v in stage_runs.items()
            if "__" in k and not k.endswith("__latest")
        }
        assert len(lifecycle) >= 8, f"expected >= 8 stage_runs, got {len(lifecycle)}"
        success_count = sum(1 for v in lifecycle.values() if v.get("status") == "success")
        assert success_count >= 8, f"expected >= 8 success stage_runs, got {success_count}"

    def test_metrics_recorded_via_write_through(self, integration_run):
        """MockLLMScore calls record_tokens, which routes through MetricsSink
        in write-through mode. The metrics doc must exist by job end."""
        _, fs, _ = integration_run
        metrics = fs._collections.get("metrics", {})
        assert len(metrics) >= 1, "MetricsSink should have written at least 1 record"

    def test_meta_export_reads_firestore_during_run(self, integration_run):
        """meta_export runs in the same job as the writes; with write-through
        MetricsSink and engine stage_run hooks, it must see non-empty data."""
        _, _, meta_dir = integration_run
        assert meta_dir.exists(), f"meta directory missing: {meta_dir}"

        for name in ("runs.json", "stages.json", "metrics.json"):
            path = meta_dir / name
            assert path.exists(), f"missing meta file: {path}"
            doc = json.loads(path.read_text())
            assert doc["count"] >= 1, f"{name} count should be > 0, got {doc['count']}"

    def test_meta_runs_count_matches_job_runs(self, integration_run):
        _, fs, meta_dir = integration_run
        firestore_runs = len(fs._collections.get("job_runs", {}))
        meta_runs = json.loads((meta_dir / "runs.json").read_text())["count"]
        assert meta_runs == firestore_runs, (
            f"meta_export read {meta_runs} runs but firestore has {firestore_runs}"
        )

    def test_no_pipeline_failures(self, integration_run):
        """Sanity check: no stage_run should be marked failed."""
        _, fs, _ = integration_run
        stage_runs = fs._collections.get("stage_runs", {})
        failed = [k for k, v in stage_runs.items() if v.get("status") == "failed"]
        assert not failed, f"unexpected failed stage_runs: {failed}"
