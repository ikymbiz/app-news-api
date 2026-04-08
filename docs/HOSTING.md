# HOSTING: Distribution Layer & Admin SPA Deployment

本ドキュメントは Phase 4〜7 で構築した Distribution 層と Client 層の
デプロイ手順を整理する。SYSTEM_DESIGN.md §5・§6 に対応。

Phase 7 で **`.github/workflows/deploy-cloudflare.yml`** を新設し、
Cloudflare Worker と Admin SPA のデプロイ・R2 バケット作成を完全自動化した。
本ドキュメントの §1〜§2 が自動化された主経路、§3 以降は手動運用や代替方式の参考情報。

---

## 0. 必要な GitHub Actions Secrets(完全リスト)

エージェント基盤の運用に必要な secrets は **「ランタイム用」と「デプロイ用」の2種類** に分かれる。同じ Cloudflare アカウントでも目的別に異なる credential を使う点に注意。

### 0.1. ランタイム secrets(`.github/workflows/main.yml` がオーケストレータを実行する際に使用)

| 名前 | 用途 | 取得元 |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Filter ステージ(Gemini 2.5 Flash Lite) | Google AI Studio |
| `OPENAI_API_KEY` | Deep Research ステージ(GPT-4o) | platform.openai.com |
| `CLOUDFLARE_R2_ACCOUNT` | R2 アカウントID | Cloudflare ダッシュボード |
| `CLOUDFLARE_R2_KEY` | **R2 S3互換アクセスキー**(データ書込み専用) | R2 → Manage R2 API tokens |
| `CLOUDFLARE_R2_SECRET` | R2 S3互換シークレット | 同上 |
| `CLOUDFLARE_R2_ENDPOINT` | `https://<account>.r2.cloudflarestorage.com` | 上記 token 作成時に表示 |
| `FIREBASE_SERVICE_ACCOUNT` | Firestore 接続用サービスアカウントの **raw JSON 文字列** | Firebase Console → プロジェクト設定 → サービス アカウント |
| `AGENT_USER_CONTEXT` | `filter_prompt.md` の `{{user_context}}` 注入値 | ユーザ自身が作成 |

### 0.2. デプロイ secrets(`.github/workflows/deploy-cloudflare.yml` が wrangler でデプロイする際に使用)

| 名前 | 用途 | 取得元 |
| :--- | :--- | :--- |
| `CLOUDFLARE_API_TOKEN` | **wrangler の認証**(Worker / Pages / R2 のデプロイ権限) | Cloudflare → My Profile → API Tokens → Create Token。スコープ: `Workers Scripts:Edit`, `Cloudflare Pages:Edit`, `Workers R2 Storage:Edit` |
| `CLOUDFLARE_ACCOUNT_ID` | wrangler のターゲット account | ダッシュボード右サイドバー |

**重要**: `CLOUDFLARE_R2_KEY/SECRET`(0.1)と `CLOUDFLARE_API_TOKEN`(0.2)は **別物**。前者は S3 API でデータを put するためのキー(データ平面)、後者は Worker や Pages の構成変更を行う bearer token(管理平面)。両方が必要。

### 0.3. キーが保存される実体パス

- GitHub に登録された secrets は **GitHub の暗号化ストレージ** に保管される。読み取りはワークフロー実行時の env 注入経由のみ(ログにマスクされる)。
- ランタイム実行中は scheduler.py が **AGENT_/GEMINI_/OPENAI_/CLOUDFLARE_/FIREBASE_** プレフィックスの環境変数だけを `StageContext.secrets` にコピーし、ステージへ渡す。Firestore やローカルディスクには **書き込まれない**(Firebase service account だけは tempfile に一時書き込みされ、プロセス終了で破棄)。
- Cloudflare Worker は現状機密値を持たない。Worker secrets(`wrangler secret put` で登録するもの)を使う必要が出たら本ドキュメントを更新する。

---

## 1. 初回セットアップ(自動化済み)

1. **Cloudflare アカウント** を持っている前提。
2. 上記 §0.1 + §0.2 の **全 9 secrets** を GitHub repo に登録。
3. `main` ブランチに `src/cloudflare/**` または `src/admin/**` の変更を push、または **Actions → deploy-cloudflare → Run workflow** を手動起動。
4. ワークフローが順次実行:
   - **preflight** ジョブ: 必要 secrets の存在確認
   - **buckets** ジョブ: `agent-platform-artifacts` / `agent-platform-artifacts-preview` を冪等に作成
   - **worker** ジョブ: `wrangler deploy` で Worker を `agent-platform-distributor.<account>.workers.dev` に公開
   - **pages** ジョブ: `wrangler pages deploy src/admin --project-name agent-platform-admin` で Admin SPA を Pages に公開
5. 完了後、Pages の URL を Admin SPA で開き、Worker URL を Distributor URL 欄に貼って Refresh。

`workflow_dispatch` の `target` 入力で `buckets` / `worker` / `pages` のいずれか1ジョブだけ動かすこともできる(初回は `all`)。

## 2. ランタイム実行(news パイプライン)

1. §0.1 の 8 secrets が登録済みであることを確認。
2. **Actions → agent-platform → Run workflow** を選び、`news-manual` を指定して起動(または cron `30 22 * * *` を待つ)。
3. ワークフローが pipeline.yml の 8 ステージを実行し、`current_news.json` などを R2 にアップロード。
4. Admin SPA でジョブの実行履歴・コスト・ステージ詳細を確認。
5. HTA Viewer(Windows)で Sync ボタンを押し、ニュース記事を取得・閲覧。



Admin SPA は以下のパスを Worker 経由で取得する:

```
GET https://<distributor>/news/index.json          # R2 オブジェクト一覧
GET https://<distributor>/news/meta/runs.json      # job_runs ダンプ(meta_export 出力)
GET https://<distributor>/news/meta/stages.json    # stage_runs ダンプ
GET https://<distributor>/news/meta/metrics.json   # metrics ダンプ
GET https://<distributor>/news/current_news.json   # 最新ニュース成果物
```

`worker.js` の `ALLOWED_APPS` チェックは先頭セグメント(`news`)のみ見るため、
`meta/` 配下も同じ許可で通過する。サブパスは R2 オブジェクトキーとして
`news/meta/runs.json` の形でアップロードされ、`meta_export` ステージが
出力した `artifacts/news/meta/*.json` を `r2_upload` ステージが
サブディレクトリ構造を保ったままアップロードする(Phase6 で実装済み)。

## 5. HTA クライアント配布

`client/hta/viewer.hta` + `local_logic.js` をユーザの Windows 端末上の
任意ディレクトリに配置するだけで動作する。初回起動時に Distributor URL を
入力し Sync ボタンを押すと、`data/{app}/items-{YYYY-MM}.json` に
Librarian Mode で月別分割保存される。

ETag 付き HTTP(`If-None-Match`)による差分同期はデフォルトで有効。
Worker 側が 304 を返した場合はネットワーク帯域・R2 課金ともに発生しない。

## 6. 動作確認チェックリスト(Phase 7 自動化後)

**初回デプロイ後:**
1. [ ] GitHub Actions → `deploy-cloudflare` の preflight / buckets / worker / pages 全ジョブが green
2. [ ] Cloudflare ダッシュボードで R2 バケット 2 個・Worker 1 個・Pages プロジェクト 1 個が見える
3. [ ] `curl https://agent-platform-distributor.<account>.workers.dev/news/index.json` が JSON を返す(初回はオブジェクト 0 件)

**初回ランタイム実行後:**
4. [ ] GitHub Actions → `agent-platform` の `news-manual` ジョブが green
5. [ ] R2 ダッシュボードで `news/current_news.json` と `news/meta/runs.json` が見える
6. [ ] Pages の URL を開き、Distributor URL 欄に Worker URL を貼って Refresh、Runs / Stages / Cost ビューにデータが表示される
7. [ ] HTA ビューア(Windows のみ)で Sync ボタンを押すと初回は items を読込、2 回目は ETag 304 になる

## 7. 既知の制約

- Worker の `CORS_ORIGIN = "*"` は本番では Admin SPA のホストに絞ること(`wrangler.toml` の `[vars]` を編集して再デプロイ)。
- `meta_export` は Firestore 接続が必須。ローカル dry-run では空配列を出力する。
- HTA は Windows 専用。macOS/Linux ユーザは Admin SPA を使う想定。
- `deploy-cloudflare.yml` の Worker smoke test は workers.dev サブドメインの推定が困難なため、`DISTRIBUTOR_URL` 環境変数が無い場合スキップする。デプロイ後に手動で curl 確認するか、ワークフロー env に固定値を追加してください。
- `wrangler pages deploy` の初回実行は Pages プロジェクトを自動作成するが、その後カスタムドメインを設定したい場合は Cloudflare ダッシュボードでの手動操作が必要(API 経由でも可だが本ワークフローには未組込み)。

