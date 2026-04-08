# SYSTEM DESIGN: Agent Platform & News Reference Implementation

## 1. レイヤ構成
1. **Scheduler Layer**: GitHub Actions cron。`config/jobs.yml` のジョブを起動トリガーに変換。
2. **Orchestrator Layer**: `src/orchestrator/` による DAG 実行・状態管理・観測性。
3. **Stage Layer**: `src/agent/stages/` のプラガブル部品。
4. **Application Layer**: `src/apps/<n>/` の用途固有パイプラインとプロンプト。
5. **Distribution Layer**: Cloudflare Workers / R2 による静的JSON配信。
6. **Client Layer**: 管理画面 SPA と HTA ビューア。

## 2. オーケストレータ仕様
### 2.1. 実行制御(機能A)
- `pipeline.yml` を DAG として解釈。`depends_on` による依存関係、`when` による条件分岐、`parallel` による並列実行をサポート。
- ステージ間データ受け渡しは `contracts.py` のスキーマに従う構造化オブジェクトを使用。

### 2.2. スケジューリング(機能B)
- `config/jobs.yml` に全ジョブを集約。`schedule`(cron)、`manual`、`webhook` の3トリガーを許容。
- GitHub Actions ワークフローは単一で、起動時に `jobs.yml` を読み対象ジョブを分岐実行。

### 2.3. 状態管理とリトライ(機能C)
- Firestore コレクション `job_runs` / `stage_runs` に状態(`pending/running/success/failed/skipped`)を記録。
- ステージ単位のチェックポイント保存により、失敗時は次回起動で未完了ステージから再開可能。
- リトライポリシーは `jobs.yml` で宣言(`max`, `backoff: linear|exponential`, `timeout`)。

### 2.4. 観測性(機能E)
- 実行ログ、LLMトークン消費・推定コスト、所要時間、成果物サイズを `observability.py` が一元収集し Firestore `metrics` に保存。
- 管理画面から全ジョブ・全ステージの実行履歴とコストを可視化。

### 2.5. 将来拡張(機能D)
- マルチエージェント協調は現行非対応。観測性基盤上の成果物参照機構として後付けする。

## 3. パイプラインとステージ契約
### 3.1. パイプライン定義スキーマ(概要)
```yaml
stages:
  - id: <string>
    use: <stages.<category>.<module>>
    depends_on: [<id>, ...]
    when: <expression>         # 任意
    parallel: <bool>           # 任意
    config: { ... }            # ステージ固有設定
```

### 3.2. ステージ I/O 契約
- 各ステージは `inputs: dict` を受け取り `outputs: dict` を返す純関数的インタフェース。
- 副作用(DB書き込み・外部API呼び出し)はオーケストレータ提供のコンテキスト経由で行う。

## 4. ニュース用リファレンス実装(`src/apps/news/`)
### 4.1. パイプライン
1. **collect** — `stages.collectors.rss`(`config/sources.json` を参照)
2. **dedupe** — Firestore ハッシュ照合
3. **filter** — `stages.filters.llm_score`(**Gemini 2.5 Flash Lite**、`apps/news/prompts/filter_prompt.md`)
4. **research** — `stages.researchers.deep_research`(`when: filter.score >= 9.0`)
5. **report** — `stages.reporters.markdown` + `json`

### 4.2. Filter ステージ詳細
- モデル: **プロンプトの YAML frontmatter で指定**(REQUIREMENTS.md §1.1)。デフォルトは Gemini 2.5 Flash Lite。
- pipeline.yml の `model:` はフォールバック値として機能する(frontmatter があれば上書きされる)。
- 設定: `response_mime_type: application/json`、`thinking_budget: 0` 相当
- 出力: `{score: float, reason: string, topics: string[]}`
- プロンプト本体は `src/apps/news/prompts/filter_prompt.md`。先頭に `---` で囲まれた YAML frontmatter(`model`, `temperature`, `description` 等)を持つ。

## 5. データ配信プロトコル
1. Reporter が `current_news.json` を生成し Cloudflare R2 にアップロード。
2. Cloudflare Workers がキャッシュ配信。
3. HTA クライアントが差分同期(Delta Sync)。

## 6. クライアント仕様(HTA)
- **Local Persistence**: ローカル `data/` への保存。
- **Incremental Merge**: 新旧データ統合(`client/hta/local_logic.js`)。
- **Librarian Mode**: 月単位でJSONを物理分割しパース負荷を軽減。

## 7. Admin SPA Editor
REQUIREMENTS.md で合意された編集可能領域を Admin SPA (`src/admin/`) から GitHub Contents API 経由で直接編集する。

### 7.1. ビュー構成
- **ニュース**: 配信URL から `current_news.json` を取得し、記事カードを表示(スコア・カテゴリ・並び順でフィルタ)。
- **実行履歴 / ステージ詳細 / コスト / 成果物**: 従来通りの観測ビュー。
- **設定**: 編集可能領域を集約。
  - 動作チューニング(`config/runtime.json`)
  - プロンプト(`src/apps/<app>/prompts/*.md` の CRUD)
  - パイプライン構成(`src/apps/<app>/pipeline.yml` のステージ編集)
  - RSSソース(閲覧のみ、GitHub 編集リンク)
  - 実行スケジュール(閲覧のみ、GitHub 編集リンク)

### 7.2. 書き込みパターン
すべての Save 操作は GitHub Contents API を使用する:
1. GET で現在の SHA を取得
2. 編集内容を base64 エンコードして PUT
3. SHA 不一致(409)時は再取得を強制

PAT は `agent-admin-github-pat` キーで localStorage に保存され、動作チューニングの入力欄から設定する。すべての編集機能で共用される。

### 7.3. カタログファイル
Admin SPA のドロップダウン候補となる2つのカタログファイル:
- `config/models.yml` — プロンプトの使用モデル選択肢
- `config/stages.catalog.yml` — パイプライン構成のステージ追加候補

どちらも git 管理下にあり、真実の源は常に git である。

### 7.4. 将来課題
- 新規 Stage 部品コードの UI 生成(コード生成 + PR 作成フロー)
- プロンプトの Jinja2 変数展開プレビュー
- パイプライン編集時の DAG グラフ可視化
