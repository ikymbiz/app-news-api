# OSINT_REFERENCE: LinkedIn Experts & Auxiliary Sources

このドキュメントは、自動収集パイプラインの **対象外** だが OSINT 観点で
重要な参照情報を集約する。LinkedIn 個人ページは認証が必要で RSS フィードを
持たないため、`config/sources.json` には含まれていない。代わりに、人的
ネットワークの**手動チェックリスト**としてここに記録する。

定期的に(週1回程度)以下の人物の最近の投稿・発表・コメントを直接確認することを
推奨する。Admin SPA 上に新しい職務発表 / 組織変更 / 警告コメントが
表示されない情報の補完として機能する。

## 1. 研究・技術(深層学習・基盤モデル)

| 氏名 | 所属 | 専門領域 | フォロー価値 |
| :--- | :--- | :--- | :--- |
| Yoshua Bengio | Mila | 深層学習・AI 倫理 | 倫理的観点からの研究批評 |
| Geoffrey Hinton | 元 Google | ニューラルネット | AGI / リスクへの長期的議論 |
| Ilya Sutskever | (旧 OpenAI、現 SSI) | 基盤モデル | 技術最前線の動向 |
| Demis Hassabis | DeepMind | AGI | 長期視点・科学応用 |
| Fei-Fei Li | Stanford | コンピュータビジョン・AI 倫理 | 社会 × AI の交差点 |

## 2. 政策・リスク・安全性

| 氏名 | 所属 | 専門領域 | フォロー価値 |
| :--- | :--- | :--- | :--- |
| Max Tegmark | MIT / FLI | AI リスク | 長期リスクの公的議論 |
| Stuart Russell | UC Berkeley | 安全設計 | "Beneficial AI" の理論的中核 |
| Helen Toner | CSET (Georgetown) | 米中政策 | 米国 AI 政策の内部視点 |

## 3. ビジネス・投資

| 氏名 | 所属 | 専門領域 | フォロー価値 |
| :--- | :--- | :--- | :--- |
| Andrew Ng | DeepLearning.AI / Landing AI | AI 教育・実務 | エンタープライズ AI 適用 |
| Sam Altman | OpenAI | AI 経営・戦略 | 業界構造の予兆 |
| Reid Hoffman | Greylock | ベンチャー投資 | 未来洞察・ネットワーク効果 |

## 4. 日本

| 氏名 | 所属 | 専門領域 | フォロー価値 |
| :--- | :--- | :--- | :--- |
| 松尾豊 | 東京大学 | 深層学習 | 日本 AI 研究の中心 |
| 安宅和人 | 慶應義塾大学 / Yahoo! Japan | データ戦略 | ビジネス × データの架橋 |
| 落合陽一 | 筑波大学 | メディア AI | 学際・未来洞察 |

## 5. 自動収集との関係

`config/sources.json` の **`risk` / `lab` / `policy`** カテゴリの記事に
本リストの人物名が現れた場合、Filter ステージのスコアが自然に押し上げられる
(プロンプトの user_context に「以下の専門家の発言を重視」と書く運用が可能)。

例えば `AGENT_USER_CONTEXT` を以下のように設定すると効果的:

```
日本在住のソフトウェアエンジニアで、AI 基盤・分散システム・低コスト
クラウド運用に関心がある。特に以下の動向を重視する:
  - フロンティアモデル(GPT / Claude / Gemini)の新リリース
  - EU AI Act / NIST RMF / 米大統領令の規制動向
  - AI Incident Database に登録される実害事例
  - Yoshua Bengio / Geoffrey Hinton / Stuart Russell 等の長期リスク論
  - 松尾豊 / 安宅和人 等の日本国内 AI 政策論
この観点で記事を評価してください。
```

## 6. 拡張のヒント

LinkedIn を本格的に自動監視するには:
- LinkedIn 公式 API は厳しく制限されており実用的でない
- 代替として **RSS Bridge** などのサードパーティリレーを自前ホストする
- もしくは Google Alerts(`https://www.google.com/alerts`)で人物名 +
  `linkedin.com` を監視し、その RSS を `config/sources.json` に追加する

これらは法的・利用規約的にグレーゾーンを含むため、本プロジェクトの
デフォルト構成からは意図的に除外している。
