"""LLM-based contextual scoring filter.

Gemini 2.5 Flash Lite を `thinking_budget: 0` + `response_mime_type: application/json`
で呼び出し、各記事に 0.0-10.0 のスコア、理由、トピックを付与する。

pipeline.yml 側で `parallel: true, fan_out: items` が宣言されているが、
本 MVP 実装はシンプルさを優先して**ステージ内でループ処理**する。
(DagExecutor の fan_out 展開は Phase4 で正式対応する前提)

基盤汚染禁止: ニュース固有語彙は持たず、プロンプトファイルと user_context は
config 経由で注入される。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.contracts import (
    FailureCategory,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
    TokenUsage,
)


_SCORE_KEYS = {"score", "reason", "topics"}


class StageImpl:
    name = "stages.filters.llm_score"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        model_name = cfg.get("model", "gemini-2.5-flash-lite")
        prompt_path = Path(cfg.get("prompt_file", "src/apps/news/prompts/filter_prompt.md"))
        temperature = float(cfg.get("temperature", 0.0))
        thinking_budget = int(cfg.get("thinking_budget", 0))
        response_mime_type = cfg.get("response_mime_type", "application/json")

        payload = inputs.payload or {}
        items: list[dict[str, Any]] = list(payload.get("items", []))

        try:
            prompt_template = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            return self._fail(started, FailureCategory.PERMANENT, str(e))

        user_context = self._resolve_user_context(cfg, ctx)

        try:
            client = self._build_client(ctx, model_name, temperature, thinking_budget, response_mime_type)
        except Exception as e:  # noqa: BLE001
            return self._fail(started, FailureCategory.PERMANENT, f"LLM client init: {e}")

        scored: list[dict[str, Any]] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        errors: list[dict[str, str]] = []

        for item in items:
            rendered = self._render_prompt(prompt_template, item, user_context)
            try:
                text, prompt_tok, comp_tok = client(rendered)
            except Exception as e:  # noqa: BLE001
                ctx.logger.warning(
                    "llm_score.call_failed",
                    extras={"item_id": item.get("id"), "error": str(e)},
                )
                errors.append({"item_id": str(item.get("id", "")), "error": str(e)})
                continue

            total_prompt_tokens += prompt_tok
            total_completion_tokens += comp_tok

            parsed = self._parse_json(text)
            if parsed is None:
                errors.append({"item_id": str(item.get("id", "")), "error": "invalid_json"})
                continue
            if not self._validate_schema(parsed):
                errors.append({"item_id": str(item.get("id", "")), "error": "schema_mismatch"})
                continue

            scored.append(
                {
                    **item,
                    "score": float(parsed["score"]),
                    "reason": str(parsed["reason"]),
                    "topics": [str(t) for t in parsed.get("topics", [])],
                }
            )

        usage = TokenUsage(
            model=model_name,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )
        ctx.record_tokens(usage)
        ctx.record_metric("llm_score.items_in", float(len(items)))
        ctx.record_metric("llm_score.items_out", float(len(scored)))
        ctx.record_metric("llm_score.errors", float(len(errors)))
        ctx.logger.info(
            "llm_score.finished",
            extras={
                "in": len(items),
                "out": len(scored),
                "errors": len(errors),
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
            },
        )

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={
                "items": scored,
                "score": max((s["score"] for s in scored), default=0.0),  # when 式で filter.score >= 9.0 参照用
            },
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                token_usage=[usage],
                custom={"errors": errors},
            ),
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_user_context(cfg: dict[str, Any], ctx: StageContext) -> str:
        ref = cfg.get("user_context_ref", "")
        if isinstance(ref, str) and ref.startswith("secrets."):
            env_name = ref.split(".", 1)[1]
            return ctx.secrets.get(env_name) or os.environ.get(env_name) or ""
        return str(cfg.get("user_context", "")) or ""

    @staticmethod
    def _render_prompt(template: str, item: dict[str, Any], user_context: str) -> str:
        rendered = template
        raw = item.get("raw") or {}
        replacements = {
            "{{user_context}}": user_context,
            "{{title}}": str(item.get("title", "")),
            "{{source}}": str(item.get("source", "")),
            "{{source_name}}": str(raw.get("source_name", item.get("source", ""))),
            "{{category}}": str(raw.get("category", "general")),
            "{{language}}": str(raw.get("language", "")),
            "{{published_at}}": str(item.get("published_at", "")),
            "{{url}}": str(item.get("url", "")),
            "{{content}}": str(item.get("content", ""))[:8000],
        }
        for k, v in replacements.items():
            rendered = rendered.replace(k, v)
        return rendered

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        # コードフェンスを剥がす(規約違反時の防御)
        stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            val = json.loads(stripped)
        except json.JSONDecodeError:
            # 先頭の JSON オブジェクト部分のみ抽出を試みる
            m = re.search(r"\{.*\}", stripped, re.DOTALL)
            if not m:
                return None
            try:
                val = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return val if isinstance(val, dict) else None

    @staticmethod
    def _validate_schema(parsed: dict[str, Any]) -> bool:
        if not _SCORE_KEYS.issubset(parsed.keys()):
            return False
        try:
            s = float(parsed["score"])
        except (TypeError, ValueError):
            return False
        if not (0.0 <= s <= 10.0):
            return False
        if not isinstance(parsed["reason"], str):
            return False
        if not isinstance(parsed["topics"], list):
            return False
        return True

    @staticmethod
    def _build_client(
        ctx: StageContext,
        model_name: str,
        temperature: float,
        thinking_budget: int,
        response_mime_type: str,
    ):
        """Gemini クライアントを初期化し、`(prompt) -> (text, prompt_tokens, completion_tokens)` を返す。

        本番では google-generativeai を使用。未導入環境ではダミークライアント(全スコア 0)を返す。
        """
        api_key = ctx.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            ctx.logger.warning("llm_score.stub_client", extras={"reason": "google-generativeai not installed"})
            def stub(_prompt: str) -> tuple[str, int, int]:
                return (json.dumps({"score": 0.0, "reason": "stub", "topics": []}), 0, 0)
            return stub

        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        genai.configure(api_key=api_key)
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "response_mime_type": response_mime_type,
        }
        # thinking_budget は 2.5 系のみ有効。無視されても安全。
        if thinking_budget is not None:
            generation_config["thinking_config"] = {"thinking_budget": thinking_budget}

        model = genai.GenerativeModel(model_name, generation_config=generation_config)

        def call(prompt: str) -> tuple[str, int, int]:
            resp = model.generate_content(prompt)
            text = getattr(resp, "text", "") or ""
            meta = getattr(resp, "usage_metadata", None)
            p_tok = int(getattr(meta, "prompt_token_count", 0) or 0)
            c_tok = int(getattr(meta, "candidates_token_count", 0) or 0)
            return text, p_tok, c_tok

        return call

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
