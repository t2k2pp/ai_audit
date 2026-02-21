## ai_audit - 生成AI活用CLIツール

**120Bモデル × 4096トークン環境向け 生成AI活用CLIツール**

社内オンプレミスの GPT-oss-120b（OpenAI互換API）や Ollama で動くモデルを使い、
コードを関数単位にチャンク化して「役割（ウェア）を切り替えながら多重推論する」
アプローチで、3種類のAI支援を提供します。

VSCode 拡張（`vscode-extension/`）を使うと、ファイル保存時に自動で監査が走り、
エディタ上に問題箇所を波線で表示します（ターミナル操作不要）。

---

### 機能概要

| コマンド | ユースケース | 概要 |
|---|---|---|
| `audit <path>` | A | セキュリティ・クリーンコードの多重マイクロ監査（ファイル／フォルダ対応） |
| `extract_why <dir>` | B | 設計思想（Why）のリバースエンジニアリングと蓄積 |
| `search_why "<query>"` | B | 蓄積した設計思想への自然言語検索 |
| `review_architecture <dir>` | C | ASTスケルトン解析によるアーキテクチャレビュー |
| `config` | - | LLM接続設定・モデル切り替え・出力トークン数の設定 |

---

### セットアップ

```bash
# 1. 依存パッケージのインストール
pip install -r requirements.txt

# 2. 環境変数の設定
cp .env.example .env
# .env を開いて接続先を確認・編集する
#   LLM_API_BASE_URL  : Ollama や OpenAI 互換 API の URL
#   LLM_API_KEY       : API キー（Ollama の場合は "ollama" 等で OK）
#   LLM_MODEL_NAME    : 使用モデル名

# 3. （推奨）設定の確認
python main.py config

# 4. （推奨）モデル一覧を確認して使用モデルを選択
python main.py config model
```

---

### 使い方

#### 設定管理

```bash
# 現在の設定を表示
python main.py config

# Ollamaのモデル一覧を表示してインデックスで切り替え
python main.py config model

# 最大出力トークン数を設定（大きいほど詳細な指摘、推論時間は増加）
python main.py config output-tokens 4096

# モデルのデフォルト最大値を使用（指定なし）
python main.py config output-tokens auto
```

#### ユースケースA：コード監査

```bash
# 単一ファイルを監査 → <ファイル名>_audit.json を出力
python main.py audit src/user_service.py

# フォルダ一括監査 → 各ファイルの _audit.json + _summary_audit.json を出力
python main.py audit ./src

# 出力先ディレクトリを指定（元のフォルダ構造を保持）
python main.py audit ./src --output-dir ./audit_results

# キャッシュを無視して再監査
python main.py audit src/user_service.py --force
```

#### ユースケースB：設計思想の抽出と検索

```bash
# ディレクトリ内の全関数の設計思想を抽出してDBに蓄積
python main.py extract_why ./src

# 蓄積した設計思想を自然言語クエリで検索
python main.py search_why "レガシーAPIとの互換性のために複雑な実装をしている箇所"

# 検索結果の件数を指定（デフォルト: 5件）
python main.py search_why "認証処理" --top-k 10
```

#### ユースケースC：アーキテクチャレビュー

```bash
# レビュー結果を標準出力に表示
python main.py review_architecture ./src

# Markdownファイルに保存
python main.py review_architecture ./src --output architecture_review.md
```

#### ヘルプ

```bash
python main.py --help
python main.py audit --help
python main.py config --help
```

---

### VSCode 拡張機能

`vscode-extension/` ディレクトリに VSCode 拡張が含まれています。

**Before（拡張なし）:**
ターミナルで `python main.py audit src/foo.py` を手動実行 → JSON を開いて確認

**After（拡張あり）:**
Ctrl+S で保存するだけ → 数秒後に問題行へ赤/黄波線 → ホバーで指摘内容を確認

詳細は [`vscode-extension/README.md`](vscode-extension/README.md) を参照してください。

---

### アーキテクチャ

```
main.py (CLI エントリポイント)
  ├─ config             → ai_audit/config_manager.py
  ├─ audit              → ai_audit/usecase_a.py
  ├─ extract_why        → ai_audit/usecase_b.py
  ├─ search_why         → ai_audit/usecase_b.py
  └─ review_architecture → ai_audit/usecase_c.py

ai_audit/ (コア基盤)
  ├─ ast_parser.py      : ASTチャンク化（関数・クラス単位）
  ├─ token_counter.py   : 文字数ベースのトークン管理（2000文字上限）
  ├─ llm_client.py      : LLM API呼び出し（OpenAI互換、リトライ付き）
  ├─ cache_manager.py   : SQLiteキャッシュ（SHA-256で変更検知）
  ├─ wear_manager.py    : ウェア（システムプロンプト）定義
  └─ config_manager.py  : 設定の永続管理（config.json / .env / デフォルト）

vscode-extension/       (VSCode 拡張)
  └─ src/extension.ts   : 保存イベント → 監査実行 → Diagnostics 表示
```

### データ保存先

デフォルト: `~/.ai_audit/`（`AI_AUDIT_DATA_DIR` 環境変数で変更可）

```
~/.ai_audit/
  ├─ config.json    : CLI で変更した設定（モデル名・トークン数等）
  ├─ cache.db       : SQLite キャッシュ（監査結果・チャンクハッシュ）
  └─ chroma/        : ChromaDB（設計思想ベクトルDB）
```
