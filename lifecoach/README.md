# ライフコーチアプリ - Local Tools (Tier 1)

要件まとめ・開発計画書に基づき、**ローカル実装可能**で **外部依存ゼロ** の純粋ロジック層 (Tier 1) を Kotlin で実装したもの。
Android 依存を一切持たないため、JVM 環境で単体テストとして動作確認できる。

## 構成

| モジュール | 役割 | パッケージ |
|---|---|---|
| **T0-1** データモデル | 全体で共有するデータ構造 (Step / Routine / Mode / ActionLog / WeekdayDefaultMode / CharacterConfig / ImplicitSignalConfig / ModeChoiceLog) | `com.lifecoach.core.model` |
| **T1-1** Timeline計算エンジン | 出発時刻から逆算してステップ別の計画時刻を算出。順方向計算と遅延圧縮も提供 | `com.lifecoach.core.timeline` |
| **T1-2** Pomodoroサイクルエンジン | 25分集中/5分休憩のフェーズ列を生成する純粋タイマーロジック | `com.lifecoach.core.pomodoro` |
| **T1-3** モード定義ストア | モード CRUD・曜日別デフォルト・今日のモード判定 (明示 → カレンダー → 曜日 → 直近 の優先順位) | `com.lifecoach.core.mode` |
| **T1-4** 計画vs実績 差分エンジン | ステップ単位のズレ・全体差分・過去N日の傾向分析 | `com.lifecoach.core.diff` |
| **T1-5** 曜日パターン検出エンジン | 過去のモード選択ログから曜日ごとの推定モードと信頼度を算出 | `com.lifecoach.core.pattern` |

## 動かす

```bash
cd lifecoach
gradle test            # 全モジュールのテスト
gradle test --tests com.lifecoach.core.timeline.TimelineEngineTest  # 個別
gradle build           # コンパイル＋テスト
```

Java 21+ / Gradle 8.x 必要。

## 設計方針

- **Android 非依存**: `java.time` と Kotlin 標準ライブラリのみ。`AlarmManager` 等の Android API は Tier 2 で別途実装する想定
- **純粋関数志向**: T1-1 / T1-2 / T1-4 / T1-5 は副作用なし。テスタブル
- **インメモリ実装**: T1-3 ModeStore は永続化を持たない。後段で Room と接続する想定
- **テンプレート差し替え可能**: 計画書 T3-2 で要求される「Gemini Nano への差し替え可能なインターフェース」設計の前段として、Tier 1 は純粋ロジックに留める

## 次の段階

- Tier 0 (T0-2 Room): Android プロジェクト追加後に永続化レイヤを実装
- Tier 2 (TTS / Alarm / Foreground Service): Android モジュール追加で実装
- Tier 3 (Character / Template / Routine Runner): Tier 1+2 を組み合わせ
- Tier 4 (Calendar / WiFi): 権限まわりを含む

## 要件との対応

開発計画書 §2 Tier 1 (T1-1 〜 T1-5) を全実装。各モジュールは独立してテスト画面 (JUnit) で動作確認可能。
