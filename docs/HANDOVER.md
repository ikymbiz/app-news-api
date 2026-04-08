# HANDOVER: Current Status & Next Steps

最終更新: 2026-04-08(Phase 11 + 12 実機デプロイセッション後)

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
