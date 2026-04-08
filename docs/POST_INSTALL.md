# POST_INSTALL: zip 展開後のセットアップ手順

このドキュメントは「GitHub に zip を展開した後、ニュースエージェントが
自動稼働するまで」の最短ルートを示します。すべての作業は **GitHub の
Web UI のみ** で完結します(ローカルでコマンドを叩く必要はありません)。

**所要時間**: 約 10 分。**クリック数**: 約 15 回(キーの取得を除く)。

---

## 0. 事前準備:アカウントとキーを揃える

以下 4 サービスのアカウントとキーを先に取得してください。各サービスとも
無料枠で動作します(取得画面の URL は §1 の各 secret 行に書いてあります)。

| サービス | 必要なもの |
| :--- | :--- |
| Google AI Studio | API キー(Gemini 2.5 Flash Lite 用) |
| OpenAI Platform | API キー(GPT-4o Deep Research 用) |
| Cloudflare | アカウントID + R2 API トークン(データ用)+ Account API トークン(管理用) |
| Firebase(任意) | Firestore プロジェクト + サービスアカウント JSON |

Firebase は **省略可** です。省略した場合、Admin SPA の Runs / Stages / Cost
ビューが空になりますが、ニュース取得・配信パイプライン本体は動作します。

---

## 1. GitHub Secrets を入力する(7 個、Firebase を使う場合)

repo 画面右上の **Settings** → 左サイドバー **Secrets and variables → Actions**
を開き、**New repository secret** ボタンを 7 回クリックして以下を順に登録します。

| # | Name | 値の取得元 |
| :--- | :--- | :--- |
| 1 | `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey |
| 2 | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| 3 | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare ダッシュボード右サイドバー(32-hex) |
| 4 | `CLOUDFLARE_R2_KEY` | Cloudflare R2 → Manage R2 API tokens → Create API token → "Object Read & Write" → **Access Key ID** |
| 5 | `CLOUDFLARE_R2_SECRET` | 同上 → **Secret Access Key**(token 作成時に1回だけ表示) |
| 6 | `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → Create Token。スコープ: `Workers Scripts:Edit`, `Cloudflare Pages:Edit`, `Workers R2 Storage:Edit` |
| 7 | `FIREBASE_SERVICE_ACCOUNT`(任意) | Firebase Console → プロジェクト設定 → サービスアカウント → 新しい秘密鍵を生成 → ダウンロードした JSON ファイルの **中身全体** をコピペ |

**注意点**:

- `#3 CLOUDFLARE_ACCOUNT_ID` と `#4 CLOUDFLARE_R2_KEY` は **別物**。前者はアカウント識別子(URL に含まれる ID)、後者は S3 互換 API のアクセスキー。
- `#4/#5 R2 keys` と `#6 API token` も **別物**。R2 keys はデータ書込み専用(S3 API)、API token は Worker / Pages / バケット作成用(管理 API)。同じ Cloudflare アカウントでも目的別に2種類の credential が必要です。
- `#7 FIREBASE_SERVICE_ACCOUNT` を入力しない場合、ニュースパイプラインは動作しますが Admin SPA の管理画面が空になります。後から追加できます。

---

## 2. GitHub Variables を入力する(1 個)

同じページの **Variables** タブに切り替えて **New repository variable** を1回クリック:

| # | Name | 値の例 |
| :--- | :--- | :--- |
| 8 | `AGENT_USER_CONTEXT` | `日本在住のソフトウェアエンジニアで、AI 基盤・分散システム・低コストクラウド運用に関心がある。特に以下の動向を重視する: フロンティアモデル(GPT/Claude/Gemini)の新リリース、EU AI Act/NIST RMF/米大統領令の規制動向、AI Incident Database に登録される実害事例、Bengio/Hinton/Russell 等の長期リスク論、松尾豊/安宅和人 等の日本国内 AI 政策論。この観点で記事を評価してください。` |

これは機密ではない個人設定なので **Variables**(平文、ログに表示される)に
入れます。Filter ステージのプロンプトテンプレートの `{{user_context}}`
プレースホルダに注入されます。後から自由に書き換え可能です。

---

## 3. Cloudflare へ初回デプロイする(クリック2回)

1. repo 上部の **Actions** タブを開く
2. 左サイドバーから **deploy-cloudflare** ワークフローを選択
3. 右上の **Run workflow** ボタン → **Branch: main**, **target: all** のまま緑の **Run workflow** をクリック

ワークフローが順に実行されます(約 2 分):

- **preflight**: secrets 存在確認
- **buckets**: R2 バケット 2 個を冪等作成
- **worker**: `wrangler deploy` で Worker を `agent-platform-distributor.<account>.workers.dev` に公開
- **pages**: `wrangler pages deploy` で Admin SPA を `agent-platform-admin.pages.dev` に公開

4 ジョブすべてが ✓ になったら、Worker と Admin SPA の URL が
ワークフロー実行ログの最後に出力されます。

---

## 4. ニュースパイプラインの初回実行(クリック2回)

1. **Actions** タブ → **agent-platform** ワークフローを選択
2. **Run workflow** → **job: news-manual** のまま **Run workflow** をクリック

約 5 分で完了します。ステージごとの進行状況はワークフロー実行画面で
リアルタイムに確認できます:

```
collect → dedupe → filter (Gemini 2.5 Flash Lite) →
research (GPT-4o, score>=9.0 の記事のみ) →
report_markdown + report_json + meta_export →
upload (Cloudflare R2)
```

実行終了後、`current_news.json` が R2 バケットに上がっています。

---

## 5. Admin SPA を開いて確認する(クリック1回)

ブラウザで **`https://agent-platform-admin.pages.dev`** を開きます
(URL は §3 の pages ジョブのログに表示されます)。

1. 上部の **Distributor URL** 欄に Worker の URL
   (`https://agent-platform-distributor.<account>.workers.dev`)を貼る
2. **Refresh** ボタンをクリック
3. **Runs / Stages / Cost / Artifacts** の 4 ビューにジョブ実行履歴と
   コスト集計が表示されれば成功

---

## 6. 自動運転に切り替える

これ以降、`.github/workflows/main.yml` の cron 設定により **毎日 07:30 JST(22:30 UTC)** に自動実行されます。手動で起動したい場合のみ §4 を繰り返します。

cron スケジュールを変更したい場合は repo の `.github/workflows/main.yml` の
`cron:` 行を編集して push するだけです(再デプロイ不要)。

---

## トラブルシューティング

### deploy-cloudflare が preflight で失敗する
→ §1 の secrets 6 個 + §1 #6 の API token がすべて登録されているか
Settings → Secrets で確認。secret は名前が大文字小文字まで完全一致する必要があります。

### deploy-cloudflare の worker ジョブが "Authentication error" で失敗する
→ `CLOUDFLARE_API_TOKEN` のスコープ不足。Cloudflare → API Tokens で
   `Workers Scripts:Edit`, `Cloudflare Pages:Edit`, `Workers R2 Storage:Edit`
   の **3 つすべて** が含まれているか確認。

### main.yml の upload ステージが "missing credentials" 警告で空の uploaded を返す
→ `CLOUDFLARE_R2_KEY` または `CLOUDFLARE_R2_SECRET` 未設定。
   または `CLOUDFLARE_ACCOUNT_ID` 未設定(エンドポイントの導出に必要)。

### main.yml は green だが Admin SPA に Runs が表示されない
→ `FIREBASE_SERVICE_ACCOUNT` 未設定なら期待動作(Firestore に書き込まないため
   meta_export が空ダンプを出力)。Firebase を使う場合はサービスアカウント JSON
   をそのままペーストしてください(改行を含む JSON のままで OK)。

### secrets を更新したいが、変更が反映されない
→ secrets の値を変更しても既存のワークフロー実行は影響を受けません。
   §3 / §4 の **Run workflow** を再度クリックして新しい値で実行してください。

---

## キーの保管場所まとめ(透明性のため)

| 何が | どこに | 誰が読むか |
| :--- | :--- | :--- |
| §1 の 7 secrets | GitHub 暗号化ストレージ | ワークフロー実行時に環境変数として注入(ログにはマスクされる) |
| §2 の 1 variable | GitHub 平文ストレージ | 同上(ログにそのまま表示される) |
| Firebase service account | ワークフロー実行中のみ tempfile | scheduler.py が起動時に書込み、プロセス終了で破棄 |
| 永続化 | **GitHub のみ** | ローカルディスク・Firestore・R2 にはキーは一切書き込まれません |

唯一の信頼境界は **GitHub アカウント** です。GitHub アカウント自体に
2要素認証(TOTP / WebAuthn)を有効化することを強く推奨します。
