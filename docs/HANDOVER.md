# HANDOVER: Current Status & Next Steps

最終更新: 2026-04-08(Phase 16 Admin SPA 実データ接続 + R2 重複処理)

---

## Phase 16 — Admin SPA 実データ接続 & R2 重複処理

ユーザ指示: 「エージェントプラットフォームはモックのままのようだ。実際のデータを使え」 + 「R2 には重複したデータがありそうだから、毎回重複する記事がないか確認し、削除、スキップをする処理を追加すること」。

Phase 15 で v4 モックを `src/admin/index.html` にコピーしたが、本文は依然としてハードコードされたサンプルデータのままだった。本セッションで以下を実装。

### 16.1 Admin SPA を実データ駆動に全面書換

`src/admin/index.html` の `<body>` と `<script>` をほぼ完全に書き直した。

**共通基盤**:
- `localStorage.ap_r2_base` / `ap_worker_base` で配信URLと書込APIのURLを保持(接続設定画面で編集)
- `fetchR2(path)`: R2 公開URL から JSON/text を取得
- `fetchGH(path)`: Worker `/api/github/read?path=...` 経由で GitHub Contents を取得(`{path, sha, size, content}` を返す)
- `writeGH(path, content, message, sha)`: Worker `/api/github/write` 経由で commit
- いずれも `credentials: include` で Cloudflare Access Cookie を送信
- 各画面に loading / empty / error の3状態テンプレ(`.empty-state` クラス)
- `js-yaml` 4.1.0 を `src/admin/vendor/js-yaml.min.js` として同梱(39KB)
- ナビゲーションは lazy load: `go(screen)` のたびに対応する `loadXxx()` が走る

**画面別の実データソース**:

| 画面 | 読込 | 書込 |
|---|---|---|
| ニュース | R2 `news/current_news.json` | — |
| スコア分布 | R2 `news/current_news.json`(ヒストグラム)+ GitHub `config/runtime.json`(取得条件) | GitHub `config/runtime.json` |
| 実行履歴 | R2 `news/meta/runs.json` | — |
| コスト | R2 `news/meta/metrics.json` | — |
| スケジュール | GitHub `config/jobs.yml`(js-yaml で parse) | GitHub `config/jobs.yml`(js-yaml で dump) |
| パイプライン | GitHub `src/apps/news/pipeline.yml` | GitHub 同 |
| プロンプト編集 | GitHub `<stage.config.prompt_file>` | GitHub 同 |
| RSS ソース | GitHub `config/sources.json` | GitHub 同 |
| RSS プレビュー | Worker `/api/rss/preview?url=...` | — |
| 接続設定 | Worker `/api/config`(read-only)+ localStorage | localStorage |

**画面別の主な機能**:
- **ニュース**: カテゴリは `items[].category` から動的に生成。フィルタ(最低スコア・カテゴリ・並び順)と並び順切替はクライアント側。記事タイトルは `item.url` リンク化
- **スコア分布**: ヒストグラムは前 Phase の100ビン(ビン幅0.1)を維持。`loadScore()` が runtime.json から閾値・取得期間・日付不明除外を読んで UI に反映、`saveScoreConfig()` が `config/runtime.json` に書戻し(commit ハッシュをトースト表示)
- **パイプライン**: ステージリストは `pipeline.yml` から生成。`enabled` フラグ ↔ チェックボックスの双方向バインド。SortableJS ドラッグで `PIPELINE_DOC.doc.stages` 配列を入替え、`dirty` フラグを立てる。「保存」前に DAG sanity check(全 `depends_on` が存在するステージか)、通れば `pipeline.yml` 全体を `js-yaml.dump()` で再シリアライズして PUT
- **ステージ削除**: 削除時に他ステージの `depends_on` から該当 ID も自動除去
- **プロンプト編集**: ステージカードのプロンプトをタップ → `prompt_file` を `fetchGH()` → textarea にロード → 編集 → 保存
- **RSS**: トグル/追加/削除で `sources.json` 配列を更新して即 PUT。プレビューは行展開時に Worker プロキシ経由で初回のみ取得(以後キャッシュ)
- **スケジュール**: cron 式入力で追加(分・時のフォームは廃止して cron 直入力に変更 — シンプル化)
- **接続設定**: クライアント側設定のみ編集可。サーバ側 env(GH_REPO 等)は `/api/config` から読取専用表示

### 16.2 Worker に3エンドポイント追加

`src/cloudflare/worker.js`:
- **`GET /api/config`** — non-secret な env(`GH_REPO`, `GH_BRANCH`, `ALLOWED_APPS`, `ACCESS_ALLOWED_EMAILS`)を返す。`GH_PAT` は絶対に返さない。Cloudflare Access ヘッダ検証あり
- **`GET /api/rss/preview?url=...`** — server-side で feed を fetch(`https://` のみ許可、CF cache 5分)して `<item>`/`<entry>` ブロックを正規表現抽出。`title` と `pubDate`/`published`/`updated`/`dc:date` を取り出し、CDATA・HTML タグ・基本 HTML エンティティを処理して上位5件を返す
- 既存の `/api/github/read|write` と同じ `checkAccess()` ガード適用

### 16.3 R2 重複検知ステージ追加

**`src/agent/stages/filters/r2_dedupe.py`**(新規、auto-discover されるので registry 改修不要)

判定キー(2段):
1. **一次**: `_normalize_url()` 後の URL 完全一致(フラグメントと末尾スラッシュ除去)
2. **二次**: `SHA-256(title.lower() + '\\0' + source.lower())` の hex(URL がトラッキングパラメータで揺れるケースの保険)

3つのモード(`config.mode`):
- **`skip`**(デフォルト): 重複した *新規* items を出力から除外
- **`delete`**: 新規側を残す。次段の reporter が `current_news.json` を上書きすることで結果的に古い側の重複が消える
- **`report_only`**: 全件 pass-through、メトリクスとレポートのみ記録

R2 アクセスは boto3(reporters.r2_upload と同方式)。boto3 未導入・バケット未設定・オブジェクト不在のいずれも致命扱いせず空配列で続行(初回実行を想定)。

成果物:
- `ctx.record_metric()` で `r2_dedupe.in / duplicates / out` を記録
- `config.report_path`(任意、デフォルト `artifacts/news/meta/dedup_report.json`)に重複サンプル20件まで含むレポートを出力

**重要**: 新ステージは `pipeline.yml` には**まだ追加していない**。次セッションで Admin SPA のパイプライン画面から(または手動で)追加する。pipeline.yml への追加例:

```yaml
  - id: r2_dedupe
    use: stages.filters.r2_dedupe
    depends_on: [filter]
    config:
      mode: skip
      bucket: agent-platform-artifacts
      key: news/current_news.json
```

そして既存の `report_json` の `depends_on` を `[filter]` から `[r2_dedupe]` に変更する。

### 16.4 既存 R2 データ用ワンショット掃除スクリプト

**`scripts/r2_dedupe_cleanup.py`** — `r2_dedupe` ステージと同じキー判定で R2 上の `current_news.json` を一度だけ掃除する CLI。

```bash
export CLOUDFLARE_R2_ENDPOINT=...
export CLOUDFLARE_R2_KEY=...
export CLOUDFLARE_R2_SECRET=...
python scripts/r2_dedupe_cleanup.py --bucket agent-platform-artifacts --dry-run
# 問題なければ --dry-run を外して実行
python scripts/r2_dedupe_cleanup.py --bucket agent-platform-artifacts
```

list 形式 / `{items: [...]}` ラッパー形式 / `{articles: [...]}` 形式を自動判別。

### 16.5 構文チェック済み

- `python3 -m py_compile` で `r2_dedupe.py` / `r2_dedupe_cleanup.py` / `core.py` が通る
- `node --check src/cloudflare/worker.js` が通る
- `node --check` で index.html の inline JS(37KB)が通る
- ブラウザ実機での動作確認は未実施

### 16.6 まだやっていないこと(次セッション)

優先順位順:

1. **`r2_dedupe` ステージの単体テスト**(`tests/test_r2_dedupe.py`): skip / delete / report_only 各モードの挙動 + URL 正規化エッジケース
2. **`r2_dedupe` を実 pipeline.yml に組み込む**: 上記 16.3 の例を追記。`report_json` / `report_markdown` の `depends_on` も連動修正
3. **R2 にワンショット掃除を実行**(`r2_dedupe_cleanup.py --dry-run` → 本実行)
4. **Cloudflare Access の許可メールアドレス確定**(Phase 14 から持ち越し)
5. **`POST_INSTALL.md` に Cloudflare Access + `wrangler secret put GH_PAT` 手順を記載**
6. **`/api/github/dispatch` エンドポイント**(workflow_dispatch トリガ): スケジュール画面の「今すぐ実行」ボタン用。現状は未配線
7. **既存「データが出ない」バグの切り分け**: Phase 14 §3.3 で挙げた meta_export / r2_upload 周りの調査。SPA 側の loaders は実装済みなので、R2 にファイルが上がってくれば自動的に表示される
8. **ブラウザ実機検証**: モバイル Safari と Chrome で各画面を1回ずつ操作
9. **`runtime.json` のスキーマ確定**: 16.1 で `cfg.collect.max_age_days` / `cfg.collect.drop_undated` / `cfg.filter.threshold` という形で書き込んでいるが、既存の `collectors.rss` / `filters.llm_score` がこの構造を読む前提になっているか確認(対応していなければ各ステージ側に runtime.json マージロジックを追加)
10. **`docs/REQUIREMENTS.md` / `docs/SYSTEM_DESIGN.md` の v4 構造への更新**(Phase 14 から持ち越し)

### 16.7 設計上の留意点

- **`pipeline.yml` の YAML コメントは消える**: js-yaml の `dump` はコメント情報を保持しない。SPA 経由で保存すると元 YAML のコメントが全て失われる。対策案: (a) 重要なコメントは別ファイル(README やコードのドキュメント)に移す、(b) コメント保持型 YAML パーサ(yawn/eemeli yaml 等)に乗り換える。当面は (a) を推奨
- **`fetchR2` が CORS で弾かれる可能性**: R2 公開バケットの CORS 設定で SPA のオリジンを許可する必要あり。`worker.js` の `corsHeaders()` は Worker 自身の応答用なので別件。`POST_INSTALL.md` での手順記載が必要
- **`/api/rss/preview` の正規表現パーサ**: XMLパーサ無しで割り切った実装。pretty-printed でない feed や CDATA 内の入れ子ケースは取り損ねる可能性あり。プレビュー用途で5件返すだけなので許容範囲だが、本格的なフィードバリデーションには使えない
- **`r2_dedupe` の `delete` モードは実質「skip 相当」**: 実装上は「新規側を残し、既存側は report のみ」になっている。本物の `delete`(R2 上のオブジェクトから古い重複を削除)を実装するには `r2_upload` 側で全件再書込が必要だが、現状の reporter はそもそも `current_news.json` を全件上書きしているので結果的に古い重複は消える。ドキュメント上の挙動説明として正確に書いておくこと

---

## Phase 15 — v4 モックの本番実装着手(このセッション)

Phase 14 の引継書(別ファイル `HANDOVER-phase14.md`)で確定した v4 モックを、`agent-platform-step10.zip` のコードベースに実装として落とし込む作業を開始した。指示は「ヒストグラムのビン幅は 0.1 とする」。

### 15.1 完了したもの

- **`src/admin/index.html` を v4 構造に書き換え**(自己完結 single-file)
  - v4 モックをベースに、`MOCK v4` バッジ・4個の `mock-note` 警告ブロック・対応する CSS 規則をすべて削除
  - SortableJS の参照を CDN(`cdn.jsdelivr.net`)から `vendor/Sortable.min.js`(同梱)に変更
  - **ヒストグラムをビン幅 0.1 に変更**
    - 旧: 10 バケット(0-1, 1-2, ..., 9-10)を HTML にハードコード
    - 新: 100 バケット(`N_BINS=100`, `BIN_WIDTH=0.1`)を JS の `renderHistogram()` で動的生成
    - CSS は細バー向けに調整(`.vhist-bar { max-width: 6px }`、`gap: 1px`、`.vhist-count` は非表示)
    - 軸ラベル(0..10)はそのまま 1.0 刻みを維持
    - 閾値線の左位置は従来通り `(v / 10) * 100%`(線形マッピングなので変化なし)
    - 「閾値以上の件数」は `firstAbove = ⌈(v - 0.05) / 0.1⌉` から `i ≥ firstAbove` のバケット合計
  - データ未取得時用のプレースホルダ分布(平均 6.5・σ=1.4 の釣鐘曲線、合計 ~1239)を内蔵
  - `loadScoreData()` を追加。`localStorage.r2_base + /news/current_news.json` を fetch してスコアをビニング(非同期、失敗時は静かにプレースホルダのまま)
  - `DOMContentLoaded` で `renderHistogram()` → `loadScoreData()` の順に実行
- **SortableJS 1.15.0 を `src/admin/vendor/Sortable.min.js` として同梱**(npm レジストリの tarball から 44KB)
- **旧 `src/admin/app.js` / `style.css` を `*.legacy.*` にリネーム**して退避(参照用)
- **`src/orchestrator/core.py` に `enabled` フラグ対応**
  - `StageSpec` に `enabled: bool = True` を追加
  - `load_pipeline()` で `s.get("enabled", True)` をパース
  - 実行ループの `when` 評価より前に `if not spec.enabled: outputs[spec.id] = SKIPPED; continue` を追加
- **`config/pipeline.schema.json` に `enabled` プロパティを追加**(default: true、`additionalProperties: false` を維持しつつ追加)
- **`src/cloudflare/worker.js` に GitHub Contents API プロキシを追加**
  - `GET /api/github/read?path=<repo-relative>` — `env.GH_PAT` を使って Contents API から base64 デコード済み内容を返す
  - `POST /api/github/write` — ボディ `{ path, content, message?, sha? }`。`sha` 未指定時は内部で HEAD して取得し PUT(create / update 両対応)
  - **二段階認証**: 全リクエストの先頭で `checkAccess()` を呼び、`Cf-Access-Authenticated-User-Email` ヘッダが無ければ 401。`env.ACCESS_ALLOWED_EMAILS`(任意、カンマ区切り)が設定されていれば追加で照合し、リストに無いメールは 403
  - `safeRepoPath()` で `..` や絶対パスを拒否
  - CORS を `POST` と `Cf-Access-Authenticated-User-Email` ヘッダに対応
- **`src/cloudflare/wrangler.toml` を更新**
  - `GH_REPO`(`owner/name`)、`GH_BRANCH`、`ACCESS_ALLOWED_EMAILS` を `[vars]` に追加
  - `GH_PAT` は機密のため `wrangler secret put GH_PAT` の手順をコメントで明記

### 15.2 まだやっていない(次セッション必須)

以下は Phase 14 引継書 §3.2 に列挙されていた本番実装項目のうち、このセッションでは時間切れで未着手:

1. **新 index.html の CRUD ボタンを実 API に接続**
   - パイプライン: ドラッグ並替・ON/OFF・追加・削除 → `/api/github/write` で `src/apps/news/pipeline.yml` を PUT
   - RSS ソース: 追加・編集・削除 → `config/sources.json` を PUT
   - スケジュール: 追加・編集・削除 → `config/jobs.yml` を PUT
   - 接続設定: `r2_base` を `localStorage` に保存(これだけは Worker 不要)
   - プロンプト編集: `src/apps/news/prompts/*.md` を PUT
   - 現状はモック由来の DOM 操作のみで、ページ遷移すると変更が消える
2. **RSS フィードプレビュー** — クライアントから直接フィード fetch + 簡易パース、または Worker に `/api/rss/preview` を追加
3. **コスト画面の折れ線グラフのデータソース** — `meta/metrics.json` から日次集計
4. **実行履歴のデータソース** — `meta/runs.json` を読む
5. **runtime.json の去就決定** — Phase 14 で「保存先を runtime.json か pipeline.yml か」が未確定。スコア分布画面の保存ボタンの実装時に決める必要あり
6. **Cloudflare Access 許可メールアドレスの確定** — Phase 14 引継書 §1.4 から未確定のまま
7. **`docs/POST_INSTALL.md` に Cloudflare Access + `wrangler secret put GH_PAT` の手順を追記**
8. **`docs/REQUIREMENTS.md` / `docs/SYSTEM_DESIGN.md` を v4 構造に合わせて改訂**
9. **テスト** — `tests/test_pipeline_smoke.py` に `enabled: false` のステージスキップを検証するケースを追加
10. **既存バグ調査**(Phase 14 §3.3): 実行履歴・コスト画面にデータが出ない問題の切り分け

### 15.3 v4 → 実装で持ち越した設計上の留意点

- **ヒストグラム 100 バー の表示密度**: 320px 幅の端末で各バーは ~3px(1px gap 込み)。視認は可能だが密。`max-width: 6px` にしたので両側に余白ができる。ユーザフィードバックで「狭い」となれば、(a) `.vhist-bars` を `overflow-x: auto` にして横スクロール、(b) 表示時のみ N 個のビンをマージ、のいずれかを検討
- **`stages.catalog.yml` の DAG 整合性**: v4 のドラッグ並替で UI 上は順序を変えられるが、`depends_on` の整合は壊れる可能性。書込時に `topological_order()` を呼んで検証し、不整合なら 400 を返すべき
- **`Cf-Access-Authenticated-User-Email` の信頼**: Cloudflare Access は前段で JWT を検証してこのヘッダを付与する。Access を経由せず Worker に直接来たリクエストにヘッダは付かないので 401 で弾ける。ただし Worker のドメインを Access アプリケーションに紐付けることが必須(紐付けないとヘッダが付与されない)。`docs/POST_INSTALL.md` での手順記載が必要
- **`atob` / `btoa` と非 ASCII**: GitHub Contents API は base64 を要求。`btoa(unescape(encodeURIComponent(text)))` パターンで UTF-8 に対応(日本語のプロンプトファイルが多いため必須)

---

## 0. Phase 13 概要(本セッションの追加変更)

**合意済み要件 3+1**(`docs/REQUIREMENTS.md` に正式記録):
1. プロンプトごとに1モデル割当(YAML frontmatter 方式)
2. プロンプトの CRUD を Admin SPA 上で(GitHub Contents API 経由)
3. Pipeline 内 Stage の入替・追加・削除を Admin SPA 上で(既存部品の組み合わせのみ)
4. 記事確認 UI「ニュース」タブの新設

**実装済み(コード)**:
- `docs/REQUIREMENTS.md` 新規(要件書)
- `config/models.yml` 新規(モデルカタログ、7モデル)
- `config/stages.catalog.yml` 新規(ステージ部品カタログ、8部品)
- `src/apps/news/prompts/filter_prompt.md` frontmatter 追加
- `src/agent/stages/filters/llm_score.py` frontmatter 読み取りロジック追加(`_split_frontmatter`)
- `src/apps/news/pipeline.yml` フォールバックコメント追記
- `src/admin/index.html` 全面書き直し(ニュースタブ追加 / 全ラベル日本語化 / プロンプト CRUD UI / パイプラインエディタ UI / Filter Prompt セクション削除 / js-yaml CDN 追加)
- `src/admin/app.js` 約400行追加(News ローダ・レンダラ / Prompts CRUD / Pipeline エディタ / Catalogs ローダ / GitHub Contents API ヘルパ)
- `src/admin/style.css` 約300行追加(news-card / prompts-layout / prompt-editor / pipeline-stage 等)
- `docs/SYSTEM_DESIGN.md` §4.2 frontmatter 記述追加、§7 Admin SPA Editor 新設
- `README.md` §2.1 / §2.3 追記

**用語改名(全項目日本語化)**:
- ヘッダ: 配信URL / GitHubリポジトリ / アプリ / 再読み込み
- タブ: ニュース(新) / 実行履歴 / ステージ詳細 / コスト / 成果物 / 設定
- 設定セクション: 動作チューニング / プロンプト(新) / パイプライン構成 / RSSソース / 実行スケジュール
- 削除: Filter Prompt セクション(プロンプトに統合)

**実機未確認(重要)**:
- Phase 13 の変更は **1度も実機で動かしていない**。次回セッション開始時の最優先タスクは:
  1. zip を Release に上げて bootstrap
  2. Admin SPA をブラウザで開き、各タブの描画確認
  3. プロンプト CRUD の Save を1回実行(PAT 必須)
  4. パイプラインエディタの Save を1回実行(慎重に、バックアップしてから)
  5. ニュースタブで `current_news.json` が読めるか確認
  6. `agent-platform` 再実行 → llm_score が frontmatter の model を読むか(ログの `llm_score.frontmatter_loaded` で確認)

**既知の懸念点**:
- `js-yaml` を CDN から読んでおり、Cloudflare Workers Static Assets 環境で CSP が問題になる可能性
- `pipeline.yml` を js-yaml で dump するとコメントが消える(pipeline.yml の冒頭コメントと NOTE コメントが失われる)→ 保存前に警告ダイアログが必要かも
- GitHub API の unauthenticated rate limit(60/h)は Prompts 一覧 / Pipeline fetch で消費される。PAT を設定しておけば 5000/h。
- `prompt_temperature` と `response_mime_type` は frontmatter に保存されるが、llm_score.py 側の読み取りは `temperature` と `response_mime_type` のみ対応済み

---

## 1. 現在の状態(サマリ)

- **リポジトリ**: `https://github.com/ikymbiz/app-news-api`
- **最新リリース**: v6(`agent-platform-step10.zip`、118 KB、73 ファイル)
- **デプロイ済みコンポーネント**:
  - GitHub repo に全コードベース展開済み(bootstrap-from-zip 経由)
  - GitHub Actions 3 ワークフロー稼働中(`agent-platform`, `deploy-cloudflare`, `tests`, `bootstrap-from-zip`)
  - Cloudflare Worker(distributor): `https://agent-platform-distributor.s-i-rec070082.workers.dev`
  - Cloudflare R2 バケット 2 個: `agent-platform-artifacts`, `agent-platform-artifacts-preview`
  - Cloudflare Workers Static Assets(Admin SPA): `https://agent-platform-admin.s-i-rec070082.workers.dev`
  - GitHub Secrets 7 個 + Variables 1 個 + PAT_TOKEN 登録済み
- **未デプロイ**: Firebase / Firestore(オプション)、HTA Viewer(Windows 専用)
- **未実行**: Cloudflare デプロイ後の `agent-platform` 再実行(Phase 11.2 + 12 のレポート改善+R2 アップロード経路を実機で確認すべき最重要タスク)

---

## 2. 動作確認済み(Verified working in production)

| 項目 | 確認方法 |
| :--- | :--- |
| `bootstrap-from-zip` workflow + PAT_TOKEN | 複数回の release 公開で 72 ファイル全展開を確認(run #6 以降は green) |
| `tests` workflow(pytest 18→20 件、jsonschema、JS syntax) | push のたびに自動実行され green |
| `main.yml` の手動実行(`agent-platform`)| run #5 で 43s/2m21s で完走、Upload artifacts step 経由で zip ダウンロード可 |
| RSS 収集(31 ソース、1132 件)| run #5 のログで `rss.collected count: 1132 errors: 4` 確認 |
| Dedupe(1132 → 1132、初回なので全件パス) | 同上、`keyword.applied in:1132 out:1132` |
| Gemini 呼び出し(`thinking_config` 削除後) | run #5 のログで `llm_score.finished in:1 out:1 errors:0 prompt_tokens:1813 completion_tokens:73` 確認 |
| Markdown reporter | `current_report.md` が GitHub Actions Artifacts として download 可能だった |
| JSON reporter | `current_news.json` 同上 |
| Firestore 統合テスト(in-memory FakeFirestore) | pytest で 7 件 PASS |
| `_StageLogger` adapter(extras= バグ修正) | Phase 6 の統合テストで再発防止確認 |
| `deploy-cloudflare` ワークフロー全 4 ジョブ | preflight / buckets / worker / pages すべて green を実機で確認 |
| Cloudflare Worker(distributor)デプロイ | GitHub Actions の summary に Distributor URL が出力 |
| Workers Static Assets(Admin SPA)デプロイ | URL を開いて Admin 画面が表示されることを実機で確認 |
| Admin SPA の基本レンダリング | header / nav / Runs view / 入力欄 / Refresh ボタンの表示確認 |
| Admin SPA 設定値の永続化 | localStorage 経由で Distributor URL と GitHub Repo を保存・復元 |
| 8 ステージ pytest E2E(NoopState 経由) | 20 件 PASS |
| `evaluate_when` の `_MISSING` センチネル(空 payload 安全) | リグレッションテスト `test_empty_collector_does_not_crash` PASS |
| `_invoke_fanout` のデフォルト `score=0.0` | 同上 |

---

## 3. 動作未確認 — 最重要(NOT YET TESTED IN PRODUCTION)

これらは Phase 11/12 で実装したが、まだ実機で 1 度も走らせていない項目。**次回のセッションで最初に確認すべき**。

### 3.1. Cloudflare デプロイ後の `agent-platform` 再実行
- **未確認**: Cloudflare R2 への artifact 自動アップロード(`r2_upload` ステージ)
- **未確認**: Worker 経由の `https://agent-platform-distributor.../news/index.json` 取得
- **未確認**: Worker 経由の `https://agent-platform-distributor.../news/current_news.json` 取得
- **未確認**: Worker 経由の `https://agent-platform-distributor.../news/current_report.md` 取得
- **どうやって確認するか**: Actions → agent-platform → Run workflow を 1 回実行し、5〜10 分待ってから Cloudflare R2 ダッシュボードで bucket にファイルが入っていることを確認、Worker URL に curl(またはブラウザでアクセス)

### 3.2. レポートのスコア分布(Phase 11.2)
- **未確認**: `current_report.md` の先頭にスコア分布ヒストグラムが入った状態
- **未確認**: トップ 30 件がスコア順に並んだ状態
- **未確認**: 9.0+ の記事に ★ マーク
- **どうやって確認するか**: 3.1 後にダウンロードした `current_report.md` を開き、ヒストグラムと top 30 が見えることを確認
- **重要な観察ポイント**: スコア分布で 9.0+ が 0 件か、何件か?
  - 0 件 → Google News プロキシのノイズが多すぎる、もしくはプロンプトが厳しすぎる(Phase 12 候補: プロンプト調整 or 閾値変更)
  - 11〜56 件 → 想定通り(全体の 1〜5%)
  - 100 件以上 → プロンプトが甘すぎる

### 3.3. 修正済み 3 RSS URL の動作
- **未確認**: `business-a16z`(a16z.com → Google News プロキシ)が記事を返す
- **未確認**: `newsletter-import-ai`(Substack → Google News プロキシ)が記事を返す
- **未確認**: `newsletter-the-batch`(deeplearning.ai → Google News プロキシ)が記事を返す
- **どうやって確認するか**: 3.1 のラン後、`Run orchestrator` ステップのログで `rss.fetch_failed` を検索 → エラー数が 4 → 1〜2 程度に減っていることを確認

### 3.4. User-Agent 変更の効果
- **未確認**: Mozilla 系 User-Agent への変更が Substack 等の bot ブロックを回避したか
- **どうやって確認するか**: 3.3 と同じ。`rss.fetch_failed` の数で判定

### 3.5. category-aware filter prompt
- **未確認**: 各カテゴリで適切なルーブリックが適用されているか
- **未確認**: `{{category}}` プレースホルダがプロンプトに正しく注入されているか
- **どうやって確認するか**: `current_news.json` の各 item の `topics` フィールドを見て、カテゴリに応じたタグが付いているか確認

### 3.6. Admin SPA の各タブの実機動作
- **未確認**: **Runs タブ**(Firestore 未接続なので空、ただし「empty state」が正しく表示されるか)
- **未確認**: **Stages タブ**(同上)
- **未確認**: **Cost タブ**(同上)
- **未確認**: **Artifacts タブ**(R2 から `news/index.json` を取得して objects 一覧を表示)
- **未確認**: **Settings タブ** ★ 重要
  - sources.json から 31 ソースが GitHub raw URL 経由で取得され、カテゴリ別の色分けで表示
  - pipeline.yml の中身が `<pre>` で表示
  - filter_prompt.md の中身が `<pre>` で表示
  - jobs.yml の中身が `<pre>` で表示
  - 各セクションの「Edit on GitHub →」ボタンが GitHub 編集画面に正しく飛ぶ
- **どうやって確認するか**: Admin SPA を開いて Distributor URL と GitHub Repo を入力 → Refresh → 各タブをタップして表示内容を確認

### 3.7. Worker smoke test URL 自動解決(Phase 9)
- **半確認**: deploy-cloudflare.yml の worker ジョブ内で Cloudflare API から workers.dev サブドメインを取得して curl
- **確認できたこと**: ジョブ自体は green
- **未確認**: smoke test が実際に 200 を返したか、それとも 404 を「許容範囲」として扱ったか
- **どうやって確認するか**: deploy-cloudflare run #3 の worker ジョブのログで `Smoke test URL` 行を確認

### 3.8. Firestore 接続(完全に未テスト)
- **未確認**: Firebase service account JSON を `FIREBASE_SERVICE_ACCOUNT` secret に登録した場合の挙動
- **未確認**: 実 Firestore SDK での `where`/`order_by`/`limit` の動作(in-memory FakeFirestore でしか検証していない)
- **未確認**: Admin SPA の Runs/Stages/Cost ビューに実データが表示される
- **どうやって確認するか**: Firebase Console でプロジェクト作成 → サービスアカウント JSON を `FIREBASE_SERVICE_ACCOUNT` secret に登録 → agent-platform 再実行 → Admin SPA で確認

### 3.9. HTA Viewer(Windows 専用)
- **未確認**: ユーザは Android 環境のため、HTA Viewer は本セッション中に一度もテストされていない
- **どうやって確認するか**: Windows PC で `client/hta/viewer.hta` を開き、Distributor URL を入力して Sync ボタンを押す

### 3.10. cron 自動実行
- **未確認**: 毎日 22:30 UTC(07:30 JST)の自動実行
- **どうやって確認するか**: 翌朝 8 時頃に Actions タブで自動 run があるか確認

---

## 4. 既知のバグ(Known bugs, fix pending)

| バグ | 影響 | 修正案 |
| :--- | :--- | :--- |
| **Admin SPA ヘッダの flex 横はみ出し** | 縦持ちスマホで Distributor URL + GitHub Repo の右に配置される App select と Refresh ボタンが画面外に出てタップ不可。横持ちにすれば回避可能 | `src/admin/style.css` の `.config` に `flex-wrap: wrap` を追加。`.config input` の `min-width: 200px` を `min-width: 0; width: 100%` に変更 |
| **deploy-cloudflare.yml の summary URL に `..workers.dev`(ダブルドット)** | 実害なし(コピペ時に手動修正必要) | `pages` ジョブの summary テンプレートで `<your-subdomain>` 部分を実際のサブドメイン取得に置き換える(worker ジョブと同じ Cloudflare API 呼び出し) |
| **`google-generativeai==0.8.3` で `thinking_config` がサポートされない** | Phase 11 で削除済み。thinking が有効のままなので per-call コストが約 2 倍になる(数 cent/月レベル) | SDK が `thinking_budget` 公式パラメータを公開した時点で再有効化。`google-genai` 新パッケージへの移行も検討 |
| **`r2_upload` ステージの認証エラー時の挙動が暗黙的** | secrets 不備時に `r2_upload.missing_credentials` warning だけ出てステージは SUCCESS 扱い。気付きにくい | failed カウンタを増やすか、エラー閾値を超えたら FAILED 扱いにする |
| **`current_news.json` の item_count が `items` 配列長と整合しない可能性** | Phase 11.2 で json reporter を `top 100` にキャップしたが、item_count は元の全件数を使っているか? | 要確認 |

---

## 5. 次回やるべきこと(優先順)

### 5.1. 最優先 — 実機検証(セッション開始直後にやる)

1. **`agent-platform` を 1 回再実行** — Cloudflare 接続後初の実行
2. **`current_report.md` を確認** — スコア分布ヒストグラム + top 30 が出ているか
3. **R2 にファイルがあるか確認** — Cloudflare ダッシュボードの R2 → `agent-platform-artifacts` バケット
4. **Admin SPA の Artifacts タブを確認** — Worker 経由でファイル一覧が見えるか
5. **Admin SPA の Settings タブを確認** — 31 ソースが見えるか
6. **`rss.fetch_failed` のソース ID を確認** — 4 → 1〜2 に減ったか

→ ここまで完了したら「**Cloudflare デプロイを含めた完全な end-to-end ループが回っている**」状態になります。

### 5.2. UX 改善

7. **`.config` の `flex-wrap` 追加** — 縦スマホで Refresh ボタン押下不可問題の修正
8. **deploy-cloudflare summary の URL 修正** — `..workers.dev` ダブルドット解消

### 5.3. プロンプト・ソース調整(5.1 の結果次第)

- 9.0+ が 0 件なら:
  - **オプション A**: `filter_prompt.md` の 9.0+ 基準を緩める
  - **オプション B**: `pipeline.yml` の `when:` を `>= 8.0` に変更
  - **オプション C**: ノイズソース(おそらく Google News 一般日本)を `enabled: false` にする

- 9.0+ が多すぎるなら:
  - プロンプトの 9.0+ 条件を厳しくする

### 5.4. 中期(任意)

- **Firebase / Firestore 接続** — Admin SPA の Runs/Stages/Cost ビューを埋める
- **OpenAI API キー登録** — Deep Research を実動作させる
- **CSS の縦スマホ最適化全般** — モバイルファースト見直し
- **`pyproject.toml`** — `PYTHONPATH=src` を毎回設定する手間の解消(Phase 9 から繰越)
- **`pytest --cov`** — カバレッジ計測(Phase 9 から繰越)
- **secrets ローテーション運用ドキュメント** — 6 ヶ月毎の API token 再発行手順
- **cron 二重管理解消** — `jobs.yml` と `main.yml` の cron 重複

### 5.5. Phase 12 候補(Admin SPA の編集機能化)

現状の Settings タブは閲覧専用 + GitHub 編集リンク。本格的な編集 UI が欲しい場合:
- **案 B**: RSS の有効無効をチェックボックスで切り替え可能にする(GitHub Contents API 経由でコミット)。GitHub PAT が必要
- **案 C**: フル CRUD UI(追加・削除・URL 編集)

ユーザは「動かしたい」段階を脱したら検討する。

---

## 6. このセッションで変更したファイル(Phase 11 + 12)

### 6.1. コア修正(Phase 11.1: `_MISSING` センチネル + thinking_config 削除)

| ファイル | 変更内容 |
| :--- | :--- |
| `src/orchestrator/core.py` | `_Missing` センチネルクラス追加。`evaluate_when` の name/attribute lookup が missing 時に `_MISSING` を返す。比較 op は False を返す。`_invoke_fanout` の merge が常に `score=0.0` をデフォルト注入 |
| `src/agent/stages/filters/llm_score.py` | `_build_client` から `thinking_config` を generation_config から削除。コメントで理由を明記 |
| `tests/test_pipeline_smoke.py` | `test_unknown_name_evaluates_false` / `test_missing_attr_evaluates_false` / `test_empty_collector_does_not_crash` 3 件追加 |

### 6.2. RSS ソース修正(Phase 11.2)

| ファイル | 変更内容 |
| :--- | :--- |
| `config/sources.json` | `business-a16z` / `newsletter-import-ai` / `newsletter-the-batch` を Google News プロキシ URL に切り替え |
| `src/apps/news/pipeline.yml` | `collect` ステージの `user_agent` を `agent-platform/1.0` から `Mozilla/5.0 (compatible; ...)` に変更 |

### 6.3. レポート構造変更(Phase 11.3)

| ファイル | 変更内容 |
| :--- | :--- |
| `src/apps/news/pipeline.yml` | `report_markdown` / `report_json` / `meta_export` の `depends_on` を `[research]` から `[filter]` に変更。`max_items: 30` / `high_value_threshold: 9.0` / `max_items: 100` を config に追加 |
| `src/agent/stages/reporters/markdown.py` | `_render` がスコア分布ヒストグラムを先頭に出力。トップ N 件を表示。9.0+ には ★ マーク |
| `src/agent/stages/reporters/json.py` | `max_items` config を honor、スコア順ソートして上位 N 件にキャップ |
| `tests/test_pipeline_smoke.py` | `test_news_pipeline_layering` の期待 layer 構造を更新(reporter が research と同じ layer に) |

### 6.4. Admin SPA Settings タブ追加(Phase 12)

| ファイル | 変更内容 |
| :--- | :--- |
| `src/admin/index.html` | nav に Settings ボタン追加。GitHub Repo 入力欄追加。`view-settings` セクション全体追加(sources table + 3 つの raw file viewer + Edit on GitHub buttons) |
| `src/admin/app.js` | `state.githubRepo` 追加。`loadSettings()` が GitHub raw URL から sources.json / pipeline.yml / filter_prompt.md / jobs.yml を fetch。`renderSources()` / `renderRawFiles()` / `updateEditLinks()` 追加 |
| `src/admin/style.css` | `.settings-section` / `.edit-link` / `.cat-*`(7 カテゴリの色分け)/ `.raw-file` / `p.hint` 追加 |

### 6.5. Workers Static Assets 移行(Phase 12)

| ファイル | 変更内容 |
| :--- | :--- |
| `src/admin/wrangler.toml` | 新規作成。`[assets] directory = "."` で SPA を Workers Static Assets としてデプロイ |
| `.github/workflows/deploy-cloudflare.yml` | `pages` ジョブを `wrangler pages deploy` から `wrangler deploy`(Workers Static Assets)に切り替え |

### 6.6. main.yml 拡張(Upload artifacts)

| ファイル | 変更内容 |
| :--- | :--- |
| `.github/workflows/main.yml` | `Run orchestrator` ステップの後に `Upload artifacts` ステップ追加(`actions/upload-artifact@v4`)。Cloudflare 未設定時でも結果を Actions Artifacts から download 可能 |

### 6.7. ドキュメント更新

| ファイル | 変更内容 |
| :--- | :--- |
| `docs/HANDOVER.md` | 本ドキュメント(全面書き直し) |
| `docs/FAILURE_LOG.md` | Phase 11 の `_MISSING` バグ + thinking_config バグ + 3 RSS URL バグ + report dependency バグの教訓を追加(未反映の場合あり) |

---

## 7. リリース履歴

| タグ | 日時 | 内容 |
| :--- | :--- | :--- |
| v1 | 初回 | Phase 10 の zip(本体) |
| v2 | core.py のバグ修正後 | (実は v1 と内容ほぼ同じ?ユーザ操作不明) |
| v3 | thinking_config 削除 + URL 修正 | このセッション中盤 |
| v4 | reporter from filter + ヒストグラム | Phase 11.3 |
| v5 | (使用されたか不明) | |
| v6 | Workers Static Assets + Settings タブ | このセッション最終 |

**注意**: ユーザは v4 までのリリースで bootstrap-from-zip を実行した可能性があるが、Phase 11.2 + 12 の最新 zip(v6 相当)を実機で動かしたあとの **agent-platform 再実行はまだしていない**。これが §3.1 の最重要未確認項目になる。

---

## 8. このセッションで最大の学び

1. **「動くはず」と「動いた」は別物**: pytest 20 件 PASS でも、実機では `thinking_config` の SDK 互換性問題、RSS URL の実在性問題、研究ステージスキップ時のレポート空問題、Cloudflare の Pages → Workers Static Assets 移行問題、CSS flex-wrap 不足、PAT_TOKEN 必須化、wrangler.toml 必須化、と次々に発覚した。実機で 1 回走らせる価値は単体テスト 100 個分以上ある。

2. **GitHub Actions の workflows 権限制約**: `GITHUB_TOKEN` は設定で最大権限を与えても `.github/workflows/` 配下を push できない。これを回避するには PAT(`workflow` scope 付き)が必須。

3. **Cloudflare の製品統合タイミング**: Pages と Workers Static Assets の統合が進行中で、新規アカウントでは「Direct Upload」が Pages ではなく Workers を作成する。`wrangler pages deploy` ではなく `wrangler deploy` + `[assets]` 設定への移行が必要。

4. **ユーザに渡す zip のファイル名は固定すべき**: `agent-platform-step10.zip` のままで内容だけ更新する戦略により、bootstrap workflow の `asset_name` 入力を毎回変えなくて済んだ。リリース番号(v1〜v6)はリリースタグ側で表現。

5. **「キーは一箇所」への執着は正しい**: GitHub Settings の Secrets/Variables ページが唯一の編集ポイントになるよう削減・統合したことで、ユーザの混乱を最小化できた。

---

以上。このドキュメントだけで次のセッションが開始できる。
