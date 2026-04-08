# DEVELOPMENT RULES: AI-Only Operations

## 1. AIコーディング・操作プロトコル
- **全アセット同期**: コード変更は必ず定義ディレクトリに反映し、`SYSTEM_DESIGN.md` との完全一致を確認する。
- **出力形式統一**: 成果物は1つのZIPとしてパッケージング可能な構造で提示する。
- **基盤汚染の禁止**: `src/orchestrator/` および `src/agent/` にアプリ固有の文字列・ロジック(例: "news", "RSS URL定数")を混入させない。

## 2. 齟齬検証(Alignment Check)ルール
- **対応表照合**: コード出力時、`SYSTEM_DESIGN.md` のどのセクションに基づくかを明示しセルフチェック。
- **契約検証**: ステージ追加/変更時は `agent/contracts.py` と `pipeline.schema.json` の双方に齟齬がないか検証。
- **乖離時アクション**: `FAILURE_LOG.md` に記録しユーザー承認を求める。

## 3. 引き継ぎファイル(HANDOVER.md)の運用
- **毎ターン更新**: 現ステータスと次着手作業を詳細記述。
- **文脈維持**: 前後を知らないAIが当ファイルのみで続行可能とする。

## 4. ディレクトリ構造定義(Ideal Asset Map)
AI はこの構造を逸脱してはならない。

- `/README.md` — プロジェクト概要・要件・仕様
- `/docs/` — `PRINCIPLE.md`, `DEVELOPMENT_RULES.md`, `SYSTEM_DESIGN.md`, `FAILURE_LOG.md`, `HANDOVER.md`
- `/src/orchestrator/` — 汎用エージェント基盤の中核
  - `core.py` — DAG実行エンジン
  - `scheduler.py` — ジョブ起動制御
  - `state.py` — Firestoreベース状態管理・リトライ
  - `registry.py` — ステージ部品の登録/解決
  - `observability.py` — ログ・メトリクス・コスト追跡
- `/src/agent/` — 汎用ステージ部品群
  - `contracts.py` — ステージ I/O スキーマ定義
  - `stages/collectors/` — `rss.py`, `api.py`, `webhook.py`
  - `stages/filters/` — `llm_score.py`, `keyword.py`
  - `stages/researchers/` — `deep_research.py`, `web_search.py`
  - `stages/reporters/` — `markdown.py`, `json.py`, `slack.py`
- `/src/apps/` — 用途固有のリファレンス実装
  - `news/pipeline.yml` — ニュース用パイプライン定義
  - `news/prompts/` — `filter_prompt.md` ほか
- `/src/cloudflare/` — `worker.js`, `wrangler.toml`
- `/src/admin/` — 管理画面(`index.html`, `app.js`, `style.css`)
- `/client/hta/` — `viewer.hta`, `local_logic.js`
- `/config/` — `jobs.yml`, `jobs.schema.json`, `pipeline.schema.json`, `settings.json.template`, `sources.json`
- `/.github/workflows/main.yml` — オーケストレータ起動ワークフロー

## 5. コスト削減とリソース制約
- **LLMレイヤリング**: 軽量(Filter: Gemini 2.5 Flash Lite)と高性能(Deep Research)の2段使い分け。
- **静的配信戦略**: HTA は R2 の静的JSONのみを参照。
- **非常駐オーケストレータ**: GitHub Actions の cron で都度起動、常駐サーバを持たない。
- **思考抑制**: Gemini 2.5 系の thinking モードはコスト要因のため、Filter 用途では `thinking_budget: 0` 相当で運用しJSON のみ返させる。
