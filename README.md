# AI-Driven Agent Platform (with News Reference Implementation)

用途を差し替え可能な汎用AIエージェント基盤。パイプライン定義とオーケストレータにより、ニュース監視・論文監視・価格監視等の多様な自動化ジョブを、最小コストかつ AI-Only で運用する。初期リファレンス実装としてニュース収集・Deep Research・レポート生成エージェントを同梱する。

> **🚀 Quick Start**: zip を GitHub repo に展開した後は **[`docs/POST_INSTALL.md`](docs/POST_INSTALL.md)** を順に進めてください。GitHub Web UI のクリックだけで(ローカルコマンド不要で)自動稼働まで到達します。所要時間 約 10 分、合計 8 個のキー入力。

## 1. プロジェクトの目的
汎用的なステージ部品とオーケストレータを中核とし、「パイプライン定義(YAML) + プロンプト + ソース設定」の差し替えだけで、任意のドメインに転用可能なエージェント基盤を提供する。最初のユースケースとして、ニュースから高価値情報のみを抽出し背景調査付きレポートを自動生成する。

## 2. 要件定義 (Requirements)
### 2.1. 基盤要件(汎用エージェント基盤)
- **パイプライン定義**: `apps/<name>/pipeline.yml` で DAG・依存関係・条件分岐を宣言。
- **ステージ部品のプラガビリティ**: Collector / Filter / Researcher / Reporter 等を部品化し `src/agent/stages/` から動的ロード。
- **オーケストレータ**: ジョブ実行制御、スケジューリング、状態管理、リトライ、観測性を提供。
- **アプリケーション分離**: 用途固有ロジックは `src/apps/<name>/` 配下に隔離し、基盤コードを汚染しない。

### 2.2. オーケストレータ機能
- **A. 実行制御**: DAG 解釈、ステージ間データ受け渡し、条件分岐(`when:`)、並列実行。
- **B. スケジューリング**: `config/jobs.yml` による複数ジョブの一元管理。cron / 手動 / Webhook トリガー対応。
- **C. 状態管理とリトライ**: Firestore に実行状態を永続化。失敗時のリトライ、チェックポイント再開、タイムアウト制御。
- **E. 観測性**: 実行ログ、LLMトークン/コスト追跡、メトリクスを一元収集し管理画面で可視化。
- **(将来拡張) D. マルチエージェント協調**: 現時点では対象外。観測性基盤上に後付け可能とする。

### 2.3. ニュース用リファレンス実装の機能
- 多角的RSS収集、LLMによる文脈適合性スコアリング(0-10)、Deep Research、Markdownレポート生成、管理UI、HTA閲覧クライアント。

### 2.4. 非機能要件
- **AI-Only Development**: 全コード・設計をAIが制御し、人間の直接編集を排除。
- **Cost Minimization**: GitHub Actions / Cloudflare 無料枠活用、軽量/高性能モデルの2段使い分け、静的JSON配信、オーケストレータは常駐させず都度起動。
- **Operational Continuity**: `HANDOVER.md` による無断セッション継続。
- **Portability**: ニュース以外の用途にパイプライン差し替えのみで転用可能。

## 3. 主要仕様 (Specifications)
### 3.1. 技術スタック
- **実行基盤**: GitHub Actions(Python / Node.js)。オーケストレータは都度起動型で常駐サーバを持たない。
- **状態/メタデータ**: Firebase Firestore
- **配信層**: Cloudflare Workers / R2(静的JSON配信)
- **管理画面**: HTML / JS / CSS(SPA)
- **閲覧ツール**: HTA(HTML Application) + JScript/VBScript
- **AIモデル**:
  - Filter: **Gemini 2.5 Flash Lite**(thinking抑制、`response_mime_type: application/json` 前提)
  - Deep Research: GPT-4o Deep Research / Gemini 2.5 Pro

### 3.2. アーキテクチャ概要
```
Scheduler(GitHub Actions cron)
        │
        ▼
Orchestrator(都度起動 Pythonプロセス)
  ├─ Registry: ステージ部品を解決
  ├─ DAG Engine: pipeline.yml を解釈
  ├─ State Store: Firestore に実行状態を記録
  └─ Observability: ログ/コスト収集
        │
        ▼
Stages(プラガブル部品)
  collectors → filters → researchers → reporters
        │
        ▼
Distribution: Cloudflare R2 静的JSON
        │
        ▼
Clients: 管理画面(Web) / HTA ビューア
```

### 3.3. ニュース用データフロー(リファレンス)
1. Orchestrator が `jobs.yml` の cron で起動。
2. `apps/news/pipeline.yml` を解釈し、RSS巡回 → 重複除外。
3. Gemini 2.5 Flash Lite でスコアリング(0-10)。
4. スコア 9.0 以上を Deep Research に投入。
5. レポートを `current_news.json` に統合し Cloudflare R2 へデプロイ。
6. HTA クライアントが差分同期。

## 4. 開発・運用ルール
詳細は以下を参照:
- `docs/PRINCIPLE.md`: 根本原則と10ステップ・ワークフロー
- `docs/DEVELOPMENT_RULES.md`: ディレクトリ構造とAI操作ルール
- `docs/SYSTEM_DESIGN.md`: オーケストレータ・パイプライン・各ステージ仕様
- `docs/FAILURE_LOG.md`: リスクと学習履歴
- `docs/HANDOVER.md`: 現在の作業状態と次タスク
