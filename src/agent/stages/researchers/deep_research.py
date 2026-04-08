"""Deep research stage.

高スコア記事(通常 filter.score >= 9.0)の背景調査を行い、要約と引用を付与する。

プロバイダ:
  - 一次: OpenAI GPT-4o Deep Research
  - 二次(フォールバック): Gemini 2.5 Pro

`max_summary_chars` で HTA 転送量最適化のため要約長を制限する。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from agent.contracts import (
    FailureCategory,
    StageContext,
    StageInput,
    StageMetrics,
    StageOutput,
    StageStatus,
    TokenUsage,
)


class StageImpl:
    name = "stages.researchers.deep_research"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        primary_model = cfg.get("model", "gpt-4o-deep-research")
        fallback_model = cfg.get("fallback_model", "gemini-2.5-pro")
        max_chars = int(cfg.get("max_summary_chars", 4000))
        include_citations = bool(cfg.get("include_citations", True))

        payload = inputs.payload or {}
        # filter ステージの payload は {"items": [...], "score": float}
        candidates: list[dict[str, Any]] = [
            i for i in payload.get("items", []) if float(i.get("score", 0)) >= 9.0
        ]

        researched: list[dict[str, Any]] = []
        usages: list[TokenUsage] = []
        errors: list[dict[str, str]] = []

        primary = self._build_openai_client(ctx, primary_model)
        fallback = self._build_gemini_client(ctx, fallback_model)

        for item in candidates:
            query = self._build_query(item)
            text, usage, err = self._call_with_fallback(primary, fallback, query, ctx)
            if err:
                errors.append({"item_id": str(item.get("id", "")), "error": err})
                continue
            summary = self._truncate(text, max_chars)
            researched.append(
                {
                    **item,
                    "summary": summary,
                    "citations": self._extract_citations(text) if include_citations else [],
                    "research_model": usage.model,
                }
            )
            usages.append(usage)

        total_prompt = sum(u.prompt_tokens for u in usages)
        total_completion = sum(u.completion_tokens for u in usages)
        ctx.record_metric("deep_research.candidates", float(len(candidates)))
        ctx.record_metric("deep_research.completed", float(len(researched)))
        ctx.record_metric("deep_research.errors", float(len(errors)))
        ctx.logger.info(
            "deep_research.finished",
            extras={
                "candidates": len(candidates),
                "completed": len(researched),
                "errors": len(errors),
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
            },
        )
        for u in usages:
            ctx.record_tokens(u)

        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=StageStatus.SUCCESS,
            payload={"items": researched},
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                token_usage=usages,
                custom={"errors": errors},
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_query(item: dict[str, Any]) -> str:
        return (
            f"以下のニュース記事について、背景・関連事実・今後の影響を含む"
            f"包括的な調査レポートを日本語で作成してください。\n\n"
            f"タイトル: {item.get('title', '')}\n"
            f"ソース: {item.get('source', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"本文抜粋:\n{str(item.get('content', ''))[:2000]}"
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"

    @staticmethod
    def _extract_citations(text: str) -> list[str]:
        # URL 抽出(ナイーブ実装)
        import re
        urls = re.findall(r"https?://[^\s)\]>\"']+", text)
        # 重複除外、順序保持
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out[:20]

    def _call_with_fallback(
        self,
        primary: Callable[[str], tuple[str, TokenUsage]] | None,
        fallback: Callable[[str], tuple[str, TokenUsage]] | None,
        query: str,
        ctx: StageContext,
    ) -> tuple[str, TokenUsage, str | None]:
        last_err: str | None = "no_provider_available"
        for name, fn in (("primary", primary), ("fallback", fallback)):
            if fn is None:
                continue
            try:
                text, usage = fn(query)
                return text, usage, None
            except Exception as e:  # noqa: BLE001
                ctx.logger.warning(
                    "deep_research.call_failed",
                    extras={"provider": name, "error": str(e)},
                )
                last_err = f"{name}:{type(e).__name__}:{e}"
                continue
        return "", TokenUsage(model="none"), last_err

    # ------------------------------------------------------------------ #
    # Provider clients
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_openai_client(ctx: StageContext, model_name: str):
        api_key = ctx.secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        try:
            import openai  # type: ignore
        except ImportError:
            ctx.logger.warning("deep_research.openai_unavailable")
            return None
        if not api_key:
            ctx.logger.warning("deep_research.openai_no_key")
            return None
        client = openai.OpenAI(api_key=api_key)

        def call(prompt: str) -> tuple[str, TokenUsage]:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            usage_meta = getattr(resp, "usage", None)
            p = int(getattr(usage_meta, "prompt_tokens", 0) or 0)
            c = int(getattr(usage_meta, "completion_tokens", 0) or 0)
            return text, TokenUsage(
                model=model_name,
                prompt_tokens=p,
                completion_tokens=c,
                total_tokens=p + c,
            )

        return call

    @staticmethod
    def _build_gemini_client(ctx: StageContext, model_name: str):
        api_key = ctx.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            return None
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)

        def call(prompt: str) -> tuple[str, TokenUsage]:
            resp = model.generate_content(prompt)
            text = getattr(resp, "text", "") or ""
            meta = getattr(resp, "usage_metadata", None)
            p = int(getattr(meta, "prompt_token_count", 0) or 0)
            c = int(getattr(meta, "candidates_token_count", 0) or 0)
            return text, TokenUsage(
                model=model_name,
                prompt_tokens=p,
                completion_tokens=c,
                total_tokens=p + c,
            )

        return call
