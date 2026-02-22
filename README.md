## ai_audit - 生成AI活用コード解析ツール

**AIによるコード監査・アーキテクチャ解析・設計書生成ツール**

社内オンプレミスの LLM（OpenAI互換API）や Ollama で動くモデルを使い、
コードを関数単位にチャンク化して「役割（ウェア）を切り替えながら多重推論する」
アプローチで、4種類のAI支援を提供します。

**Python / JavaScript / TypeScript** に対応しています。

---

### 使い方は2通り

#### ✅ VSCode拡張（推奨）
**Pythonのインストール不要。** VSIX を入れるだけですぐ使えます。
→ [VSCode拡張の使い方](#vscode-拡張機能)

#### 🔧 CLI（開発者・上級者向け）
Python 環境がある方向け。自動化やスクリプト組み込みに。
→ [CLIの使い方](#cli-の使い方)

---

### 機能概要

| 機能 | VSCode | CLI コマンド | 概要 |
|---|:---:|---|---|
| コード監査 | ✅ 保存時自動 | `audit <path>` | セキュリティ・クリーンコードの多重マイクロ監査 |
| 設計思想の抽出 | ✅ コマンド | `extract_why <dir>` | 「なぜこう書いたか（Why）」をリバースエンジニアリング |
| 設計思想の検索 | ✅ コマンド | `search_why "<query>"` | 蓄積した設計思想への自然言語検索 |
| アーキテクチャレビュー | ✅ 右クリック | `review_architecture <dir>` | ASTスケルトン解析による構造レビュー |
| **設計書の逆生成** | ✅ 右クリック | `generate_design_doc <dir>` | コードから詳細・概要設計書を自動生成（v0.3.0〜） |
| 設定管理 | ✅ GUI | `config` | LLM接続設定・モデル切り替え |

---

### VSCode 拡張機能

#### インストール

1. [Releases](https://github.com/t2k2pp/ai_audit/releases) から最新の VSIX をダウンロード
   - Windows: `ai-audit-win-*.vsix`
2. VSCode で `Ctrl+Shift+P` → `Extensions: Install from VSIX...` → ファイルを選択
3. VSCode を再起動

**Python のインストールは不要です。** 実行エンジンは VSIX に同梱されています。

#### 初期設定

インストール後、`Ctrl+Shift+P` → `ai_audit: 接続設定を開く` で以下を入力:

| 設定項目 | 説明 | 例 |
|---|---|---|
| API Base URL | LLM API の URL | `http://192.168.1.40:11434/v1` |
| API Key | API キー | `ollama` |
| Model Name | 使用モデル名 | `qwen2.5-coder:32b` |

#### VSCode からできること

**ファイル保存時（自動）**
- Python / JavaScript / TypeScript を保存すると自動で監査が走る
- 問題箇所に波線 + ホバーで指摘内容を表示

**フォルダを右クリック → コンテキストメニュー**

| メニュー | 説明 |
|---|---|
| `ai_audit: アーキテクチャを解析する` | フォルダ全体の構造をレビュー |
| `ai_audit: ドキュメント生成：設計書` | コードから詳細・概要設計書を生成（v0.3.0〜） |
| `ai_audit: 設計思想を抽出する` | Why 情報をデータベースに蓄積 |

**コマンドパレット（`Ctrl+Shift+P`）**

| コマンド | 説明 |
|---|---|
| `ai_audit: 接続設定を開く` | API 設定画面を開く |
| `ai_audit: モデル一覧を表示・切り替え` | 接続先のモデル一覧から選択 |
| `ai_audit: 現在のファイルを監査する` | 手動で監査を実行 |
| `ai_audit: 現在のファイルを再監査する（キャッシュ無視）` | 強制的に再監査 |
| `ai_audit: 設計思想を検索する` | 蓄積した設計思想を自然言語で検索 |
| `ai_audit: サポート言語一覧を表示` | 対応済み言語の確認 |

#### 設計書逆生成について（v0.3.0〜）

フォルダを右クリック → `ai_audit: ドキュメント生成：設計書` を実行すると、
コードを解析して **2つの設計書** をフォルダ直下に自動生成します：

- `_design_detail.md` … 詳細設計書（関数・クラスレベルの内部構造、Mermaid図付き）
- `_design_overview.md` … 概要設計書（外部向けの機能・構成サマリー）

> **ユースケース1:** ドキュメントがないレガシーコードから設計書を起こしたい
> **ユースケース2:** スパゲッティコードを AI にリファクタリングしてもらうための入力資料を作りたい

途中で中断しても `_design_detail.md` が残っていれば概要生成から再開します。
全体をやり直したい場合は CLI で `--force` オプションを使用してください。

---

### CLI の使い方

Python 環境がある場合は CLI でも利用できます。

#### セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を編集: LLM_API_BASE_URL / LLM_API_KEY / LLM_MODEL_NAME
python main.py config
```

#### コマンド一覧

```bash
# コード監査
python main.py audit src/user_service.py        # 単一ファイル
python main.py audit ./src                      # フォルダ一括
python main.py audit ./src --output-dir ./out   # 出力先指定
python main.py audit ./src --force              # 再監査（キャッシュ無視）

# 設計思想の抽出・検索
python main.py extract_why ./src
python main.py search_why "認証処理の意図は？"
python main.py search_why "レガシー互換" --top-k 10

# アーキテクチャレビュー
python main.py review_architecture ./src
python main.py review_architecture ./src --output review.md

# 設計書の逆生成（v0.3.0〜）
python main.py generate_design_doc ./src
python main.py generate_design_doc ./src --output-dir ./docs
python main.py generate_design_doc ./src --force   # 全体を再生成

# 設定管理
python main.py config
python main.py config model
python main.py config output-tokens 4096
python main.py config output-tokens auto
```

---

### JS/TS サポートについて（v0.3.0〜）

v0.3.0 から JavaScript / TypeScript / TSX に対応しました。

> ⚠️ **注意:** JS/TS 固有のベストプラクティス（型安全性・非同期処理パターン等）の観点は
> 現バージョンでは未対応です。各出力レポートに注記として自動挿入されます。

---

### アーキテクチャ

```
main.py (CLI エントリポイント)
  ├─ config              → ai_audit/config_manager.py
  ├─ audit               → ai_audit/usecase_a.py
  ├─ extract_why         → ai_audit/usecase_b.py
  ├─ search_why          → ai_audit/usecase_b.py
  ├─ review_architecture → ai_audit/usecase_c.py
  └─ generate_design_doc → ai_audit/usecase_d.py  ← v0.3.0〜

ai_audit/ (コア基盤)
  ├─ ast_parser.py      : ASTチャンク化（Python/JS/TS対応）
  ├─ token_counter.py   : 文字数ベースのトークン管理
  ├─ llm_client.py      : LLM API呼び出し（OpenAI互換、リトライ付き）
  ├─ cache_manager.py   : SQLiteキャッシュ（SHA-256で変更検知）
  ├─ wear_manager.py    : ウェア（システムプロンプト）定義
  └─ config_manager.py  : 設定の永続管理

vscode-extension/       (VSCode 拡張)
  └─ src/extension.ts   : 保存イベント → 監査実行 → Diagnostics 表示
```

### データ保存先

デフォルト: `~/.ai_audit/`（`AI_AUDIT_DATA_DIR` 環境変数で変更可）

```
~/.ai_audit/
  ├─ config.json    : 設定（モデル名・トークン数等）
  ├─ cache.db       : SQLiteキャッシュ（監査結果・チャンクハッシュ）
  └─ chroma/        : ChromaDB（設計思想ベクトルDB）
```

設計書の出力先はデフォルトで **解析対象フォルダ直下** です（`--output-dir` で変更可）。
