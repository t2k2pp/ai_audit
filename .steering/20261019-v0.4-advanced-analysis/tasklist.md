# タスクリスト: v0.4

ルール: 1 タスク＝1 コミット。コミットメッセージ先頭にタスク ID。完了時に `[x]` と HANDOFF.md を更新し同じコミットに含める。フェーズ完了時に main へ push。

## フェーズ1: リポジトリ整理・テスト基盤
- [x] T1-01 `.gitignore` 整理（`.steering/` 追跡、コミット済み vsix / out を管理外へ、`*_why.json`）
- [x] T1-02 devcontainer を Python 3.13＋Node に、CLAUDE.md 技術スタック更新、TaskCLI アイデア削除
- [x] T1-03 単一ソース化（ルート main.py を最新化、`vscode-extension/python/` を管理外、ビルドスクリプトを requirements.txt ベースに統一）【B5, B6】
- [x] T1-04 テスト基盤（requirements-dev.txt、pytest 設定、ダミー LLM サーバ、サンプルプロジェクト）

## フェーズ2: 共通基盤
- [x] T2-01 パッケージ再編（core/config, 既存モジュールの移動と import 更新）
- [x] T2-02 server_probe（llama.cpp / vLLM / Ollama / 手動）
- [x] T2-03 budget（実効予算・ティア判定・トークン計測・自己補正）
- [x] T2-04 llm_client v2（json_schema→json_object→修復、思考除去、EXTRA_BODY、リトライ方針、使用量記録）
- [x] T2-05 パーサ v2（シンボル: 修飾名・親子・行範囲・import・呼び出し・継承）【B1, B2】
- [x] T2-06 Code Graph（SQLite、差分更新、問い合わせ）
- [x] T2-07 ジョブストア・パイプラインエンジン（入力ハッシュ、原子的書き込み、再開。status コマンドは T3-07 で CLI と合わせて実装）

## フェーズ3: 設計書生成 v2（T1/T2）
- [x] T3-01 S0/S1 事実抽出（依存図・クラス図 Mermaid、メトリクス、エントリポイント）
- [x] T3-02 S2 ファイル要約（スキーマ、ティア別入力）
- [x] T3-03 S3 モジュール要約（多段 reduce）
- [x] T3-04 S4 システム要約と目次
- [x] T3-05 S5 章生成（コンテキスト組み立ての優先順位）
- [x] T3-06 S6 検証（根拠チェック、整合性レビュー、再生成）【B3, B4】
- [x] T3-07 S7 組み立て（v2 レイアウト、legacy レイアウト、coverage.json）、CLI オプション

## フェーズ4: 設計書生成 v2（T3）
- [x] T4-01 ADR 逆生成（git 履歴）
- [ ] T4-02 T3 のコンテキスト拡張（関連ファイル同梱）とテスト

## フェーズ5: アーキテクチャ解析 v2
- [ ] T5-01 メトリクス＋モジュール要約を使った統合レビュー【B4】

## フェーズ6: 監査 v2・設計思想
- [ ] T6-01 監査 v2（シンボル単位、文脈同梱、JSON Schema、行番号必須、検証パス、キャッシュキー、追加ウェア）【B7, B8】
- [ ] T6-02 SARIF 出力
- [ ] T6-03 設計思想（シンボル ID、埋め込みモデル指定、`_why.json` 出力）

## フェーズ7: 追従・仕上げ
- [ ] T7-01 VSCode 拡張追従（新レイアウト、JS/TS の監査結果パス、行番号、Why JSON、設定項目）
- [ ] T7-02 README・`.env.example`・既存設計書の更新
- [ ] T7-03 全テスト・TypeScript コンパイル確認、振り返り記録
