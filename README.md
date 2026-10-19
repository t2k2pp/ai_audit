## ai_audit - ローカル LLM によるコード解析ツール

**AI によるコード監査・アーキテクチャ解析・設計書生成ツール**

社内オンプレミスや自宅の LLM（OpenAI 互換 API: llama.cpp / vLLM / Ollama など）を使い、
静的解析で確定した事実と LLM の推論を組み合わせて、根拠付きの監査結果・レビュー・設計書を作ります。

**Python / JavaScript / TypeScript / Dart** に対応しています。

### v0.4 の主な変更

- **大コンテキスト対応と 4096 互換の両立**: コンテキスト長に応じて機能を上乗せする 3 段階のティア
  （compact: 〜8K / standard: 〜64K / large: 64K 超）。サーバからコンテキスト長を自動取得します。
- **設計書生成の刷新**: ファイル → モジュール → システムと要約を積み上げ、章ごとに生成。
  図は静的解析から機械生成し、本文の根拠（`[パス:行]`）は存在確認してソースへリンクします。
- **中断・再開**: 途中で止まっても同じコマンドを再実行すると続きから処理します。コード変更後は影響する部分だけ再生成します。
- **監査の精度向上**: 関数単位の監査に呼び出し元・呼び出し先の文脈を同梱、誤検知を落とす検証パス、正確な行番号。SARIF 出力。
- **設計思想機能の簡素化**: ChromaDB 依存を廃止。VSCode 拡張だけで動きます（埋め込みモデルの指定も可能）。
- 不具合修正: 同名メソッドの ID 衝突、クラスとメソッドの二重監査、概要設計書が詳細設計書の先頭しか見ていなかった問題、
  JS/TS の監査結果が VSCode に表示されない問題 など。

詳細は [v0.4 詳細設計書](docs/6.%20詳細設計書_v0.4_大コンテキスト対応.md) を参照してください。

---

### 機能概要

| 機能 | VSCode | CLI コマンド | 概要 |
|---|:---:|---|---|
| コード監査 | ✅ 保存時自動 | `audit <path>` | セキュリティ・可読性・性能・呼び出し契約の多重監査 |
| 設計思想の抽出 | ✅ コマンド | `extract_why <dir>` | 「なぜこう書いたか（Why）」を推定して蓄積 |
| 設計思想の検索 | ✅ コマンド | `search_why "<query>" --dir <dir>` | 蓄積した設計思想を自然言語で検索 |
| アーキテクチャレビュー | ✅ 右クリック | `review_architecture <dir>` | 依存・循環・メトリクスの機械算出＋LLM による評価と改善計画 |
| 設計書の生成 | ✅ 右クリック | `generate_design_doc <dir>` | 概要・詳細・モジュール別設計書、図、ADR（large） |
| 進捗の確認 | - | `status [<dir>]` | 直近ジョブの進捗 |
| 設定管理 | ✅ GUI | `config` / `config probe` | 接続設定、サーバ検出結果・ティアの確認 |

### ティア（4096 互換と機能の上乗せ）

ティアは「1 回のリクエストで使えるトークン数（実効予算）」で自動判定します（`AI_AUDIT_TIER` / `--tier` で指定も可）。

| 機能 | compact（〜8K） | standard（〜64K） | large（64K 超） |
|---|:---:|:---:|:---:|
| 静的解析による図・メトリクス・根拠チェック | ✅ | ✅ | ✅ |
| 設計書: 要約の積み上げ（入らない場合は多段に統合） | ✅ | ✅ | ✅ |
| 設計書: 章ごとの整合性レビューと再生成 | - | ✅ | ✅ |
| 設計書: ADR（git 履歴から設計判断を記録） | - | - | ✅ |
| 監査: 呼び出し元・呼び出し先の文脈 | - | シグネチャ | ソースまで |
| 監査: 誤検知を落とす検証パス | - | ✅ | ✅ |

---

### VSCode 拡張機能

#### インストール

1. [Releases](https://github.com/t2k2pp/ai_audit/releases) から OS に合った VSIX をダウンロード
2. VSCode で `Ctrl+Shift+P` → `Extensions: Install from VSIX...` → ファイルを選択
3. VSCode を再起動

**Python のインストールは不要です。** 実行エンジンは VSIX に同梱されています。

#### 初期設定

`Ctrl+Shift+P` → `ai_audit: 接続設定を開く`

| 設定項目 | 説明 | 例 |
|---|---|---|
| API Base URL | LLM API の URL | `http://192.168.1.40:8080/v1` |
| API Key | API キー（不要なサーバでは空） | |
| Model Name | 使用モデル名 | `qwen3.8-flash-next` |
| Context Length | 最大コンテキスト（空 = 自動取得） | `262144` |
| Tier | ティア（auto = 自動判定） | `auto` |

詳細は [vscode-extension/README.md](vscode-extension/README.md) を参照してください。

---

### CLI の使い方

#### セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を編集: LLM_API_BASE_URL / LLM_MODEL_NAME（必要に応じて LLM_CONTEXT_LENGTH など）
python main.py config probe     # サーバ種別・コンテキスト長・ティアを確認
```

#### コマンド一覧

```bash
# 設計書の生成（既定の出力先: <dir>/_design/）
python main.py generate_design_doc ./src
python main.py generate_design_doc ./src --output-dir ./docs/design
python main.py generate_design_doc ./src --tier compact      # 4096 環境向けの動作を強制
python main.py generate_design_doc ./src --layout legacy     # v0.3 互換の 2 ファイル出力
python main.py generate_design_doc ./src --force             # 途中結果を使わず全再生成
python main.py status ./src                                  # 進捗の確認

# アーキテクチャレビュー
python main.py review_architecture ./src --output review.md

# コード監査
python main.py audit src/user_service.py        # 単一ファイル（<ファイル名>_audit.json）
python main.py audit ./src --sarif audit.sarif  # フォルダ一括（SARIF も出力）
python main.py audit ./src --force              # キャッシュを無視して再監査

# 設計思想の抽出・検索
python main.py extract_why ./src
python main.py search_why "認証処理の意図は？" --dir ./src
python main.py list_why --dir ./src

# 設定
python main.py config
python main.py config model
python main.py config output-tokens 4096
```

#### 設計書の出力

```
<dir>/_design/
  README.md          目次・生成条件（モデル・ティア・対象コミット）・カバレッジ
  overview.md        概要設計書（非エンジニア・再実装を依頼する AI 向け）
  detail.md          詳細設計書（構成・処理フロー・データ・横断的関心事・課題）
  modules/*.md       モジュール別設計（ファイル構成・クラス図・公開シンボル一覧・解説）
  diagrams/*.mmd     静的解析から生成した Mermaid 図
  adr/*.md           設計判断の記録（large ティア＋git 管理下）
  _meta/coverage.json  要約・章・根拠の件数、予算の都合で外した入力、失敗したステップ
```

#### 中断と再開

各ステップの結果は `<dir>/.ai_audit/work/` に保存されます。途中で止まった場合や一部が失敗した場合は、
同じコマンドを再実行すると完了済みの部分を再利用して続きから処理します。コードを変更した場合は、
変更したファイルに関係する部分だけを再生成します。

---

### 推論サーバについて

| サーバ | コンテキスト長の自動取得 | トークン数の実測 |
|---|:---:|:---:|
| llama.cpp（`llama-server`） | ✅ `/props` | ✅ `/tokenize` |
| vLLM | ✅ `/v1/models` | ✅ `/tokenize` |
| Ollama | ✅ `/api/show`（※） | 推定 |
| その他 OpenAI 互換 | `LLM_CONTEXT_LENGTH` で指定 | 推定 |

※ Ollama の実行時コンテキスト長（`num_ctx`）はモデルの最大値より小さく設定されていることがあります。
実際の値を `LLM_CONTEXT_LENGTH` に設定してください。

思考モードを持つモデルは、応答中の思考過程（`<think>` / `reasoning_content`）を自動で取り除きます。
思考の ON/OFF をサーバ固有のパラメータで切り替える場合は `LLM_EXTRA_BODY` を使います
（例: `{"chat_template_kwargs": {"enable_thinking": false}}`。指定方法はサーバ・モデルのドキュメントを確認してください）。

---

### アーキテクチャ

```
main.py                      CLI エントリポイント
ai_audit/
  core/        設定・サーバ検出・予算（ティア）・LLM クライアント・JSON 修復
  index/       ファイル走査・パーサ（Python / JS / TS / Dart）・Code Graph・git 情報
  context/     予算内でのプロンプト組み立て
  pipeline/    ステップの保存・再利用・並列実行（中断再開）
  usecases/    design_doc/（設計書）・architecture・audit・why
  wears/       プロンプト定義（版数付き）
  render/      Mermaid・SARIF
vscode-extension/            VSCode 拡張（ビルド時に ai_audit/ と main.py を同梱）
tests/                       pytest（ダミー LLM サーバで結合テスト）
```

### データ保存先

| 場所 | 内容 |
|---|---|
| `<対象>/.ai_audit/graph.db` | Code Graph（解析結果） |
| `<対象>/.ai_audit/work/` | 各ステップの途中結果（再開・差分更新用） |
| `<対象>/.ai_audit/why.db`, `why.json` | 設計思想 |
| `~/.ai_audit/`（`AI_AUDIT_DATA_DIR`） | サーバ検出結果・トークン補正係数 |

`.ai_audit/` と `_design/` はリポジトリの `.gitignore` に追加することを推奨します。

### 開発

```bash
pip install -r requirements-dev.txt
pytest
cd vscode-extension && npm install && npm run compile
./build_linux.sh   # または build_mac.sh / build_win.ps1
```
