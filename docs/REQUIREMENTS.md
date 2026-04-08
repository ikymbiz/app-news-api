# REQUIREMENTS: Editable Pipeline & Prompt Management

最終確定: 2026-04-08 / 本書は実装に先立つ「合意済み要件」のスナップショット。

このドキュメントは `docs/PRINCIPLE.md` の Documentation First 原則に基づき、
コードに先立って合意された3つの新規要件と、それを実装するための UI 構成変更、
および用語の改名を一元的に記録する。

---

## 1. 機能要件(合意済み)

### 1.1. プロンプトごとに1モデルを割り当てる
- 各プロンプトに対して使用する LLM モデルを **1つだけ** 指定できる。
- モデルの指定は **プロンプトファイル自身の YAML frontmatter** で行う。
- pipeline.yml 側の `model:` 指定は後方互換のため残すが、frontmatter があればそちらが優先される。
- 利用可能モデルの一覧は `config/models.yml` に集約する。

### 1.2. プロンプトの CRUD を Admin SPA 上で行う
- Admin SPA の「設定 → プロンプト」セクションから以下を実行できる:
  - 一覧表示(List)
  - 本文と frontmatter の編集(Update)
  - 新規作成(Create、雛形から生成)
  - 削除(Delete、Pipeline から参照されている場合は警告)
- 保存は GitHub Contents API 経由のコミット(SHA 取得 → PUT)。
- PAT は既存の `agent-admin-github-pat` localStorage キーを共用。

### 1.3. Pipeline 内 Stage の入替・追加・削除
- Admin SPA の「設定 → パイプライン構成」セクションを **編集可能** にする。
- 既存の Stage 部品(`src/agent/stages/` 配下)を組み合わせて DAG を編集する。
- 新しい Stage 部品コードそのものを UI から生成する機能は **将来課題**(本要件には含めない)。
- 利用可能な Stage 部品の一覧は `config/stages.catalog.yml` に集約する。
- 編集対象は `src/apps/<app>/pipeline.yml` のみ。
- 保存は GitHub Contents API 経由。

### 1.4. 記事確認 UI(ニュースタブ)
- ナビに「ニュース」タブを新設する。
- 配信URL から `current_news.json` を取得して、スコア順に記事カードを表示する。
- 各カードは: タイトル / ソース / カテゴリ / スコア / 理由 / トピック / 元記事リンク を表示。

---

## 2. UI 構成変更

### 2.1. タブ構成(変更点のみ)
- **追加**: 「ニュース」タブ(ナビの左端、デフォルト active)
- 既存5タブ(実行履歴 / ステージ詳細 / コスト / 成果物 / 設定)はそのまま

### 2.2. 設定タブ内のセクション(変更点のみ)
- **追加**: 「プロンプト」セクション(動作チューニングの直下)
- **削除**: 「Filter Prompt」セクション(プロンプトに統合)
- **編集モード追加**: 「パイプライン構成」セクション(現状の `<pre>` 表示にステージ操作 UI を併設)
- 他のセクション(動作チューニング / RSSソース / 実行スケジュール)は表示・機能とも変更なし

---

## 3. 用語改名(全項目日本語化)

### ヘッダ
| 旧 | 新 |
|---|---|
| Distributor URL | 配信URL |
| GitHub Repo | GitHubリポジトリ |
| App | アプリ |
| Refresh | 再読み込み |

### タブ
| 旧 | 新 |
|---|---|
| Runs | 実行履歴 |
| Stages | ステージ詳細 |
| Cost | コスト |
| Artifacts | 成果物 |
| (新) | ニュース |
| Settings | 設定 |

### 設定セクション
| 旧 | 新 |
|---|---|
| Runtime (editable) | 動作チューニング |
| RSS Sources | RSSソース |
| Pipeline Definition | パイプライン構成 |
| (新) | プロンプト |
| Filter Prompt | (削除) |
| Jobs / Schedule | 実行スケジュール |

### 動作チューニング内のフォーム
| 旧 | 新 |
|---|---|
| 取得期間(日数) | 取得期間(日数)※据え置き |
| 日付なし記事を捨てる | 日付不明の記事を除外 |
| ★ ハイライト閾値(0-10) | ★ ハイライト閾値(0-10)※据え置き |
| GitHub PAT(contents:write) | GitHubアクセストークン |
| Save to GitHub | 保存 |
| Reload from GitHub | 再取得 |

---

## 4. ファイル変更計画(参考)

### 新規
- `config/models.yml` — モデルカタログ
- `config/stages.catalog.yml` — Stage 部品カタログ
- `docs/REQUIREMENTS.md` — 本ファイル

### 修正
- `src/apps/news/prompts/filter_prompt.md` — frontmatter 追加
- `src/agent/stages/filters/llm_score.py` — frontmatter から model を読む
- `src/apps/news/pipeline.yml` — 後方互換コメント追記
- `src/admin/index.html` — タブ追加・セクション追加・全ラベル日本語化
- `src/admin/app.js` — News ローダ、プロンプト CRUD、Pipeline 編集
- `docs/SYSTEM_DESIGN.md` — frontmatter / Admin Editor の節を追記
- `docs/HANDOVER.md` — Phase 13 として記録
- `README.md` — §2.1 / §2.3 に1行ずつ追記

---

## 5. 範囲外(明示)

以下は本要件には含めず、将来課題として残す:
- 新しい Stage 部品コードの UI 生成(コード生成 + PR 作成)
- マルチアプリ管理(現状 `news` の1アプリ固定)
- プロンプトのバージョン履歴 UI(git log を見ればよいので不要)
- DAG のグラフ可視化(リスト形式で十分)
- モデルのフォールバック / レース戦略(1プロンプト1モデルで確定)
