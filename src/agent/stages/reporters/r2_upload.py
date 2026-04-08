"""R2 upload reporter stage.

直前ステージ(通常は reporters.markdown / reporters.json)から渡された
`ReportArtifact` 参照を読み込み、Cloudflare R2(S3 互換 API)にアップロードする。
単一責務のため本ステージはローカルファイル整形も LLM 呼び出しも行わない。

pipeline.yml 例:

  - id: upload
    use: stages.reporters.r2_upload
    depends_on: [report_markdown, report_json]
    config:
      bucket: agent-platform-artifacts
      prefix: news/
      endpoint_url_env: CLOUDFLARE_R2_ENDPOINT
      access_key_env: CLOUDFLARE_R2_KEY
      secret_key_env: CLOUDFLARE_R2_SECRET

boto3 未導入時はスタブ動作(ログのみ)で success を返す。実 I/O は環境側に依存。
"""

from __future__ import annotations

import os
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
)


class StageImpl:
    name = "stages.reporters.r2_upload"

    def run(
        self,
        ctx: StageContext,
        inputs: StageInput[Any],
    ) -> StageOutput[dict[str, Any]]:
        started = datetime.now(timezone.utc)
        cfg = dict(inputs.config)
        bucket = cfg.get("bucket")
        prefix = cfg.get("prefix", "")
        artifacts_root = Path(cfg.get("artifacts_root", "artifacts"))
        endpoint_env = cfg.get("endpoint_url_env", "CLOUDFLARE_R2_ENDPOINT")
        key_env = cfg.get("access_key_env", "CLOUDFLARE_R2_KEY")
        secret_env = cfg.get("secret_key_env", "CLOUDFLARE_R2_SECRET")

        if not bucket:
            return self._fail(started, FailureCategory.PERMANENT, "bucket is required")

        # 上流の複数 reporter から artifact 参照を収集(単数/複数両対応)
        artifacts: list[dict[str, Any]] = []
        for upstream_id, upstream in inputs.upstream.items():
            if not (upstream.payload and isinstance(upstream.payload, dict)):
                continue
            # meta_export 等は payload["artifacts"] にリストを返す。
            multi = upstream.payload.get("artifacts")
            if isinstance(multi, list):
                for art in multi:
                    if isinstance(art, dict) and art.get("path"):
                        artifacts.append({**art, "stage": upstream_id})
                continue
            # markdown/json reporter は payload["artifact"] に単一 dict を返す。
            single = upstream.payload.get("artifact")
            if isinstance(single, dict) and single.get("path"):
                artifacts.append({**single, "stage": upstream_id})

        if not artifacts:
            ctx.logger.warning("r2_upload.no_artifacts", extras={})
            return StageOutput(
                status=StageStatus.SUCCESS,
                payload={"uploaded": []},
                metrics=StageMetrics(started_at=started, finished_at=datetime.now(timezone.utc)),
            )

        client = self._build_client(ctx, endpoint_env, key_env, secret_env)
        uploaded: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        total_bytes = 0

        for art in artifacts:
            local_path = Path(art["path"])
            if not local_path.exists():
                errors.append({"stage": art.get("stage", "?"), "error": "local file missing"})
                continue
            # Derive R2 key by stripping artifacts_root (and optional app segment
            # that duplicates the configured prefix).
            try:
                rel = local_path.resolve().relative_to(artifacts_root.resolve())
            except ValueError:
                rel = Path(local_path.name)
            rel_parts = list(rel.parts)
            prefix_lead = prefix.strip("/").split("/", 1)[0] if prefix else ""
            if rel_parts and prefix_lead and rel_parts[0] == prefix_lead:
                rel_parts = rel_parts[1:]
            rel_str = "/".join(rel_parts) or local_path.name
            key = f"{prefix}{rel_str}".lstrip("/")
            content_type = self._content_type(local_path.name)
            data = local_path.read_bytes()
            try:
                if client is None:
                    ctx.logger.warning(
                        "r2_upload.stub",
                        extras={"key": key, "bytes": len(data)},
                    )
                else:
                    client.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=data,
                        ContentType=content_type,
                        Metadata={"checksum": str(art.get("checksum", ""))},
                    )
            except Exception as e:  # noqa: BLE001
                errors.append({"stage": art.get("stage", "?"), "error": str(e)})
                continue

            total_bytes += len(data)
            uploaded.append(
                {
                    "stage": art.get("stage"),
                    "key": key,
                    "bytes": len(data),
                    "checksum": art.get("checksum"),
                    "content_type": content_type,
                }
            )
            ctx.logger.info(
                "r2_upload.ok",
                extras={"key": key, "bytes": len(data)},
            )

        ctx.record_metric("r2_upload.count", float(len(uploaded)))
        ctx.record_metric("r2_upload.bytes", float(total_bytes))
        ctx.record_metric("r2_upload.errors", float(len(errors)))

        status = StageStatus.SUCCESS if not errors or uploaded else StageStatus.FAILED
        finished = datetime.now(timezone.utc)
        return StageOutput(
            status=status,
            payload={"uploaded": uploaded, "errors": errors, "bucket": bucket},
            error_category=FailureCategory.TRANSIENT if (errors and not uploaded) else None,
            error_message="; ".join(e["error"] for e in errors) if errors and not uploaded else None,
            metrics=StageMetrics(
                started_at=started,
                finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                artifact_bytes=total_bytes,
                custom={"errors": errors},
            ),
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _content_type(name: str) -> str:
        if name.endswith(".json"):
            return "application/json; charset=utf-8"
        if name.endswith(".md"):
            return "text/markdown; charset=utf-8"
        if name.endswith(".html"):
            return "text/html; charset=utf-8"
        return "application/octet-stream"

    @staticmethod
    def _build_client(ctx: StageContext, endpoint_env: str, key_env: str, secret_env: str):
        try:
            import boto3  # type: ignore
        except ImportError:
            ctx.logger.warning("r2_upload.boto3_unavailable", extras={})
            return None

        endpoint = ctx.secrets.get(endpoint_env) or os.environ.get(endpoint_env)
        access_key = ctx.secrets.get(key_env) or os.environ.get(key_env)
        secret_key = ctx.secrets.get(secret_env) or os.environ.get(secret_env)

        # Derive R2 endpoint from account ID if not explicitly set.
        # The R2 S3-compatible endpoint always follows this exact pattern:
        #   https://<account_id>.r2.cloudflarestorage.com
        # This eliminates one secret (CLOUDFLARE_R2_ENDPOINT) from the
        # required configuration set.
        if not endpoint:
            account_id = (
                ctx.secrets.get("CLOUDFLARE_R2_ACCOUNT")
                or ctx.secrets.get("CLOUDFLARE_ACCOUNT_ID")
                or os.environ.get("CLOUDFLARE_R2_ACCOUNT")
                or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
            )
            if account_id:
                endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        if not (endpoint and access_key and secret_key):
            ctx.logger.warning(
                "r2_upload.missing_credentials",
                extras={"endpoint_env": endpoint_env},
            )
            return None

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

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
