"""Stage component registry and dynamic loader.

SYSTEM_DESIGN.md §2 / DEVELOPMENT_RULES.md §4 に基づき、
`stages.<category>.<module>` 形式の識別子からステージ実装クラスを動的解決する。

- 基盤(src/agent/stages/)配下のモジュールを規約的に探索。
- アプリ固有の use 名(例: "stages.custom.xxx")も、登録ディレクトリを拡張すれば解決可能。
- ステージ実装は `contracts.Stage` Protocol を満たすこと(static duck typing)。
"""

from __future__ import annotations

import importlib
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

from agent.contracts import Stage

_USE_PATTERN = re.compile(
    r"^stages\.(?P<category>collectors|filters|researchers|reporters)\.(?P<module>[a-z][a-z0-9_]*)$"
)

_ENTRY_CLASS_CANDIDATES = ("StageImpl", "Stage", "Component")


@dataclass(frozen=True)
class StageRef:
    category: str
    module: str

    @property
    def import_path(self) -> str:
        return f"agent.stages.{self.category}.{self.module}"


class RegistryError(RuntimeError):
    pass


class StageRegistry:
    """スレッドセーフなステージ解決キャッシュ。"""

    def __init__(self) -> None:
        self._cache: dict[str, Stage] = {}
        self._overrides: dict[str, Callable[[], Stage]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def register(self, use: str, factory: Callable[[], Stage]) -> None:
        """テスト用途やアプリ固有拡張のための手動登録。"""
        self._validate(use)
        with self._lock:
            self._overrides[use] = factory
            self._cache.pop(use, None)

    def resolve(self, use: str) -> Stage:
        """`use` 文字列からステージインスタンスを取得。"""
        self._validate(use)
        with self._lock:
            cached = self._cache.get(use)
            if cached is not None:
                return cached
            factory = self._overrides.get(use)
            if factory is not None:
                instance = factory()
            else:
                instance = self._load_from_module(use)
            self._cache[use] = instance
            return instance

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate(use: str) -> StageRef:
        m = _USE_PATTERN.match(use)
        if not m:
            raise RegistryError(
                f"Invalid stage use identifier: {use!r}. "
                "Expected format: stages.<collectors|filters|researchers|reporters>.<module>"
            )
        return StageRef(category=m.group("category"), module=m.group("module"))

    def _load_from_module(self, use: str) -> Stage:
        ref = self._validate(use)
        try:
            module = importlib.import_module(ref.import_path)
        except ImportError as e:
            raise RegistryError(
                f"Stage module not importable: {ref.import_path} ({e})"
            ) from e

        cls: Any = None
        for name in _ENTRY_CLASS_CANDIDATES:
            cls = getattr(module, name, None)
            if cls is not None:
                break
        if cls is None:
            raise RegistryError(
                f"{ref.import_path} に {'/'.join(_ENTRY_CLASS_CANDIDATES)} のいずれも見つかりません"
            )

        instance = cls() if isinstance(cls, type) else cls
        # Duck-type check: run メソッドが存在するか。
        if not callable(getattr(instance, "run", None)):
            raise RegistryError(
                f"{ref.import_path}.{cls} does not implement Stage.run()"
            )
        return instance  # type: ignore[return-value]


# Module-level singleton
default_registry = StageRegistry()
