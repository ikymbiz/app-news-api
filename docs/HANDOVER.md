# HANDOVER: Current Status & Next Steps

## 1. 現在の作業ステータス
- **フェーズ**: Phase 10 — AI OSINT 用本番ソースカタログの組み込み + category-aware filter prompt + LinkedIn 専門家リファレンス文書化。ニュース用リファレンス実装が「動くデモ」から「実運用可能な AI OSINT パイプライン」へ昇格。
- **完了した項目**(フェーズ1〜9): 全機能 + Cloudflare 自動デプロイ + secrets 削減 + POST_INSTALL ガイド + pytest 18 テスト + CI ワークフロー + Worker smoke test 自動化。
- **完了した項目**(フェーズ10 / 本セッション):
  - **`config/sources.json` 全面置換**(3 → **31 ソース**) — ユーザ提供の AI OSINT カタログを 7 カテゴリで構造化:
    - `policy` × 5(EU Commission, OECD, NIST, UK AISI, White House OSTP)
    - `lab` × 5(OpenAI, DeepMind, Anthropic, Meta AI, Hugging Face)
    - `papers` × 5(arXiv cs.AI / cs.LG / cs.CL, Papers with Code, conferences)
    - `risk` × 5(AI Incident DB, Partnership on AI, CAIS, MIT AI Risk Repo, OpenAI Safety)
    - `business` × 4(TechCrunch AI, The Information, a16z, MIT Tech Review)
    - `newsletter` × 3(Import AI, The Batch, Ben's Bites)
    - `japan` × 4(METI, デジタル庁, 理研, Google News JP)
  - **Google News プロキシ戦略**: RSS 公式サポートのないサイト(EU Commission, NIST, AISI, AI Incident DB, METI 等)については `https://news.google.com/rss/search?q=site:<domain>...` の形で Google News RSS を経由。各ソースに `via: google_news_proxy` メタデータを付与し、Filter ステージが「二次情報の可能性」として加味できるようにした。
  - **`category` メタデータ伝播**:
    - `src/agent/stages/collectors/rss.py` を更新: `CollectedItem.raw` に `category`, `language`, `via`, `source_name` を格納
    - `src/agent/stages/filters/llm_score.py` の `_render_prompt` に `{{category}}`, `{{language}}`, `{{source_name}}` プレースホルダを追加
  - **`src/apps/news/prompts/filter_prompt.md` 全面改訂** — category-aware ルーブリック:
    - 7 カテゴリそれぞれに「8.0+ の条件 / 9.0+ の条件 / 減点要因」を定義
    - `policy`: EU AI Act / NIST RMF / 大統領令の新規発表・改訂を 8.0+
    - `lab`: フロンティアモデルの新リリース・新ベンチマークを 8.0+、パラダイム転換は 9.0+
    - `papers`: 主要会議採択 + 再現性確認を 8.0+、SOTA 大幅更新は 9.0+
    - `risk`: 実発生インシデント・脆弱性開示を 8.0+、規制発動引き金は 9.0+
    - `business`: 大型資金調達・主要 M&A を 8.0+、市場構造変化は 9.0+
    - `newsletter`: 一次情報を伴う独自分析を 8.0+
    - `japan`: 経産省・デジタル庁ガイドライン・国内大手戦略を 8.0+
    - Universal Rubric は overrides として温存、Decision Guardrails に「Google News プロキシは一次ソースよりやや厳しく評価」を追加
  - **`docs/OSINT_REFERENCE.md` 新設** — LinkedIn 専門家 20 名(研究 5 / 政策 3 / ビジネス 3 / 日本 3、計 14 名 + ユーザ提示分)を **手動チェックリスト** として記録。LinkedIn 個人ページは認証必須・RSS 非対応のため自動収集対象外。`AGENT_USER_CONTEXT` への組み込み例も同梱。「LinkedIn 自動監視は法的グレーゾーンのためデフォルトから除外」と明記。
  - **`docs/POST_INSTALL.md` 更新** — `AGENT_USER_CONTEXT` の例を AI OSINT ユースケースに更新(具体的な観点と人物名を含む)。

- **検証結果(本セッション実機確認)**:
  - 18 / 18 pytest 全 PASS(0.62s)— rss.py / llm_score.py の変更後も既存テストが回帰なし
  - `sources.json` JSON 構文 OK、31 ソース × 7 カテゴリの分布を確認
  - 全 Python AST パース OK

## 2. 運用上の重要な注意点

### 2.1. RSS フィード URL の信頼性

`config/sources.json` に登録した直接 RSS URL のうち、以下は **本番動作で要再確認**:

| ソース | 想定 URL | 確認状況 |
| :--- | :--- | :--- |
| OpenAI Blog | `https://openai.com/blog/rss.xml` | サイト改装で URL 変更の可能性 |
| DeepMind Blog | `https://deepmind.google/blog/rss.xml` | 同上 |
| Meta AI Blog | `https://ai.meta.com/blog/rss/` | 同上 |
| Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | 一般的パターン |
| TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` | WordPress 標準パターン |
| MIT Tech Review | `https://www.technologyreview.com/feed/` | 同上 |
| a16z | `https://a16z.com/feed/` | 同上 |
| Import AI | `https://importai.substack.com/feed` | Substack 標準 |
| The Batch | `https://www.deeplearning.ai/the-batch/feed/` | 確認推奨 |
| arXiv cs.AI/cs.LG/cs.CL | `http://export.arxiv.org/rss/...` | 公式安定 URL |

`rss.py` collector は **per-source エラー隔離** で 1 ソース失敗でも他に影響なし、
`StageMetrics.custom["source_errors"]` に記録されるため、初回実行のログで
失敗ソースを特定 → URL 修正 → 再実行 のループが可能。

### 2.2. LLM コスト試算(31 ソース × デイリー実行)

- 1 ソースあたり最大 50 items → 全体最大 1,550 items / 日
- 1 item あたり Filter プロンプトで約 800 tokens(プロンプトテンプレ込み)
- 入力合計: 約 1.24M tokens / 日
- Gemini 2.5 Flash Lite 単価: $0.0001 / 1k 入力 tokens → **$0.124 / 日 = 約 $3.7 / 月**
- 出力(JSON 1 個 ≈ 50 tokens): 1,550 × 50 × $0.0004 / 1k ≈ **$0.031 / 日 = 約 $0.93 / 月**
- **Filter 段合計: 約 $4.6 / 月**
- Deep Research(score>=9.0、想定 1〜3 件 / 日): GPT-4o Deep Research $0.005-$0.015 / 1k tokens × 3k tokens × 3 件 = **約 $0.135 / 日 = 約 $4 / 月**
- **総合計: 月 $8〜10 程度**(GitHub Actions / Cloudflare R2 / Firebase は無料枠内)

### 2.3. dedupe の重要性

31 ソースのうち Google News プロキシは **同じ事件を別 URL で複数回返す** 可能性が高い。`stages.filters.keyword`(mode=dedupe) は url+title の sha256 で重複除外しているため、そのまま機能する。ただし「同じ事件で違うタイトル」のケースは漏れるため、必要なら Phase 11 で URL 正規化(クエリパラメータ除去 + リダイレクト追跡)を追加検討。

## 3. 次回実施すべき作業内容
- **タスク1**: 実機での初回実行 — `POST_INSTALL.md` の手順を消化後、`Actions → agent-platform → news-manual` を起動し、`StageMetrics.custom["source_errors"]` を確認して失敗ソースの URL を修正。
- **タスク2**: Filter ステージのプロンプト効果検証 — 初回実行後、`current_news.json` の `score` 分布を見て、9.0+ 件数が想定通り(全体の 1〜5%)になっているか確認。プロンプトのチューニング余地を見つける。
- **タスク3**: Google News プロキシソースの URL クエリチューニング — 各 `site:` フィルタが想定通りの記事を返すか手動確認。例えば EU Commission の `site:digital-strategy.ec.europa.eu` が機能しない場合はキーワードベースに切り替え。
- **タスク4**: `OSINT_REFERENCE.md` の専門家リストを定期更新する運用 — 半年に 1 回、所属変更 / 退職 / 新規重要人物を反映。
- **タスク5**(Phase 9 から繰越): `pyproject.toml` 化、pytest-cov 導入、secrets ローテーション運用 doc、cron 二重管理解消。

## 4. 未解決事項・保留中の要件
- **RSS URL の動作確認**: Phase 10 では URL を「最も可能性の高いパターン」で記載した。実機検証で要修正(初回実行 1 回で大半判明する)。
- **dedupe の URL 正規化**: 同一事件の別 URL が重複除外されない可能性。Phase 11 候補。
- **LinkedIn 自動収集**: 法的・利用規約的にグレーのため除外。ユーザが必要なら RSS Bridge / Google Alerts 経由で手動追加可能(`OSINT_REFERENCE.md §6` 参照)。
- **`category=newsletter` の Substack RSS 信頼性**: Substack URL パターンは比較的安定だが、移行や独自ドメイン化で変わる可能性。
- **arXiv cs.AI のノイズ**: 1 日数百件公開されるため、Filter ステージで大半が低スコアになる前提。プロンプトで「実装伴う / SOTA 改善」を明示的に高評価する設定を Phase 10 で反映済み。
- **Phase 9 から繰越**: 実機検証、`pyproject.toml`、pytest-cov、secrets ローテーション、cron 二重管理。
- **機能D(マルチエージェント協調)**: 据え置き。
