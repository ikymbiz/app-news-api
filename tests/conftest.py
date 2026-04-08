"""Shared pytest fixtures.

Provides:
  - `fake_firestore`: an in-memory FakeFirestore that implements the minimal
    subset of the google.cloud.firestore_v1 API surface used by
    FirestoreStateStore, MetricsSink, and the meta_export stage.
  - `mock_stages`: registers MockCollector / MockLLMScore / MockResearch in
    the default registry, replacing the real implementations for the duration
    of the test.
  - `repo_root`: absolute path to the repository root, for tests that need to
    read pipeline.yml or jobs.yml.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


# --------------------------------------------------------------------------- #
# Fake Firestore (minimal subset of google.cloud.firestore_v1 API)
# --------------------------------------------------------------------------- #

class FakeSnap:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return dict(self._data or {})


class FakeDoc:
    def __init__(self, coll: "FakeCollection", doc_id: str) -> None:
        self._coll = coll
        self.id = doc_id

    def set(self, data: dict, merge: bool = False) -> None:
        if merge and self.id in self._coll._docs:
            self._coll._docs[self.id].update(data)
        else:
            self._coll._docs[self.id] = dict(data)

    def update(self, data: dict) -> None:
        self._coll._docs.setdefault(self.id, {}).update(data)

    def get(self) -> FakeSnap:
        return FakeSnap(self.id, self._coll._docs.get(self.id))


class FakeQuery:
    def __init__(self, coll: "FakeCollection") -> None:
        self._coll = coll
        self._filters: list[tuple[str, str, object]] = []
        self._order: tuple[str, str] | None = None
        self._limit: int | None = None

    def _clone(self) -> "FakeQuery":
        q = FakeQuery(self._coll)
        q._filters = list(self._filters)
        q._order = self._order
        q._limit = self._limit
        return q

    def where(self, field: str, op: str, value: object) -> "FakeQuery":
        q = self._clone()
        q._filters.append((field, op, value))
        return q

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        q = self._clone()
        q._order = (field, direction)
        return q

    def limit(self, n: int) -> "FakeQuery":
        q = self._clone()
        q._limit = n
        return q

    def stream(self):
        results: list[FakeSnap] = []
        for doc_id, data in self._coll._docs.items():
            ok = True
            for field, op, val in self._filters:
                dv = data.get(field)
                if dv is None:
                    ok = False
                    break
                try:
                    if op == ">=" and not (dv >= val):
                        ok = False
                        break
                    if op == "==" and not (dv == val):
                        ok = False
                        break
                except TypeError:
                    ok = False
                    break
            if ok:
                results.append(FakeSnap(doc_id, data))
        if self._order:
            field, direction = self._order
            results.sort(
                key=lambda s: s._data.get(field) or datetime.min,
                reverse=(direction == "DESCENDING"),
            )
        if self._limit is not None:
            results = results[: self._limit]
        return iter(results)


class FakeCollection:
    def __init__(self, store: "FakeFirestore", name: str) -> None:
        self._store = store
        self.name = name
        self._docs: dict[str, dict] = store._docs_for(name)

    def document(self, doc_id: str) -> FakeDoc:
        return FakeDoc(self, doc_id)

    def where(self, *a, **k) -> FakeQuery:
        return FakeQuery(self).where(*a, **k)

    def order_by(self, *a, **k) -> FakeQuery:
        return FakeQuery(self).order_by(*a, **k)

    def limit(self, n: int) -> FakeQuery:
        return FakeQuery(self).limit(n)

    def stream(self):
        return FakeQuery(self).stream()


class FakeFirestore:
    def __init__(self) -> None:
        self._collections: dict[str, dict] = {}

    def _docs_for(self, name: str) -> dict:
        return self._collections.setdefault(name, {})

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)


# --------------------------------------------------------------------------- #
# Pytest fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def chdir_repo(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    monkeypatch.chdir(repo_root)


@pytest.fixture
def fake_firestore() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def mock_stages() -> Iterator[None]:
    """Register mock collector / llm_score / research in the default registry,
    overriding any prior registration. The fixture restores the registry to
    its previous state after the test."""
    from agent.contracts import StageOutput, StageStatus, StageMetrics, TokenUsage
    from orchestrator.registry import default_registry

    class MockCollector:
        name = "stages.collectors.rss"

        def run(self, ctx, inputs):
            return StageOutput(
                status=StageStatus.SUCCESS,
                payload={"items": [
                    {"id": "a1", "source": "mock", "title": "AI breakthrough",
                     "url": "https://x/1", "published_at": "2026-04-08",
                     "content": "Big news", "raw": {}},
                    {"id": "a2", "source": "mock", "title": "Weather",
                     "url": "https://x/2", "published_at": "2026-04-08",
                     "content": "Rain", "raw": {}},
                ]},
                metrics=StageMetrics(),
            )

    class MockLLMScore:
        name = "stages.filters.llm_score"

        def run(self, ctx, inputs):
            items = inputs.payload.get("items", [])
            scored = [
                {**it, "score": 9.5 if "AI" in it["title"] else 3.0,
                 "reason": "m", "topics": ["t"]}
                for it in items
            ]
            ctx.record_tokens(TokenUsage(
                model="gemini-2.5-flash-lite",
                prompt_tokens=100, completion_tokens=20, total_tokens=120,
            ))
            return StageOutput(
                status=StageStatus.SUCCESS,
                payload={
                    "items": scored,
                    "score": max((x["score"] for x in scored), default=0.0),
                },
                metrics=StageMetrics(),
            )

    class MockResearch:
        name = "stages.researchers.deep_research"

        def run(self, ctx, inputs):
            items = [x for x in inputs.payload.get("items", []) if x.get("score", 0) >= 9.0]
            out = [{**x, "summary": f"research: {x['title']}", "citations": [],
                    "research_model": "mock"} for x in items]
            return StageOutput(status=StageStatus.SUCCESS,
                               payload={"items": out},
                               metrics=StageMetrics())

    # Save and replace overrides.
    saved = dict(default_registry._overrides)
    saved_cache = dict(default_registry._cache)
    default_registry._overrides.clear()
    default_registry._cache.clear()
    default_registry.register("stages.collectors.rss", lambda: MockCollector())
    default_registry.register("stages.filters.llm_score", lambda: MockLLMScore())
    default_registry.register("stages.researchers.deep_research", lambda: MockResearch())

    try:
        yield
    finally:
        default_registry._overrides.clear()
        default_registry._overrides.update(saved)
        default_registry._cache.clear()
        default_registry._cache.update(saved_cache)


@pytest.fixture
def clean_artifacts(repo_root: Path) -> Iterator[None]:
    """Remove test artifacts directory before and after each test."""
    import shutil
    artifacts = repo_root / "artifacts"
    workdir = repo_root / ".workdir-test"
    for p in (artifacts, workdir):
        if p.exists():
            shutil.rmtree(p)
    try:
        yield
    finally:
        for p in (artifacts, workdir):
            if p.exists():
                shutil.rmtree(p)
