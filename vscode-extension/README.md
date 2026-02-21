# ai_audit VSCode 拡張機能

AIを使ったコードレビューをVSCodeに統合します。
ファイルを保存するだけで自動的に監査が走り、問題のある行に波線と修正提案が表示されます。

> **対応OS**: Windows / macOS / Linux（OS別にVSIXが異なります）

---

## 利用者の動線（はじめてから使えるまで）

### ステップ 1: VSIXをインストールする

お使いのOSに合ったVSIXファイルを入手してください:

| OS | ファイル名 |
|---|---|
| Windows | `ai-audit-win-*.vsix` |
| macOS | `ai-audit-mac-*.vsix` |
| Linux | `ai-audit-linux-*.vsix` |

1. VSCodeを開く
2. `Ctrl+Shift+P`（mac: `Cmd+Shift+P`） → `Extensions: Install from VSIX...` を選択
3. 上記のVSIXファイルを選択

**Python のインストールは不要です。** 実行エンジンはVSIXに同梱されています。

### ステップ 2: 接続設定を入力する

インストール直後に案内メッセージが表示されます。「設定画面を開く」をクリックしてください。

または `Ctrl+Shift+P` → `ai_audit: 接続設定を開く` でも開けます。

以下の3項目を入力してください:

| 設定項目 | 説明 | 例 |
|---|---|---|
| `aiAudit.apiBaseUrl` | **[必須]** LLM APIのURL | `http://192.168.1.40:11434/v1` |
| `aiAudit.apiKey` | **[必須]** APIキー | `ollama` |
| `aiAudit.modelName` | **[必須]** 使用するモデル名 | `gpt-oss:120b` |

> **モデル名がわからない場合**: `Ctrl+Shift+P` → `ai_audit: モデル一覧を表示・切り替え` を実行すると、接続先のOllamaで使えるモデルが一覧表示され、クリックで選択できます。

### ステップ 3: 使い始める

**これだけです。** Pythonファイルを開いて `Ctrl+S` で保存してください。

数秒後に問題のある行へ波線が出ます。波線にカーソルを合わせると指摘内容と修正提案が表示されます。

---

## 使い方

### 自動監査

Python ファイルを **Ctrl+S で保存するだけ** で監査が始まります。

- 赤波線 = `high` 重要度（セキュリティ脆弱性など）
- 黄波線 = `medium` 重要度（保守性の問題など）
- 青波線 = `low` 重要度（設定で表示/非表示を切り替え可）

### コマンドパレット（Ctrl+Shift+P）

| コマンド | 説明 |
|---|---|
| `ai_audit: 接続設定を開く` | API URL / APIキー / モデル名の設定画面を開く |
| `ai_audit: モデル一覧を表示・切り替え` | 接続先のモデル一覧から使用モデルを選択 |
| `ai_audit: サポート言語一覧を表示` | 対応済み・対応予定の言語一覧を表示 |
| `ai_audit: 現在のファイルを監査する` | 手動で監査を実行 |
| `ai_audit: 現在のファイルを再監査する（キャッシュ無視）` | 強制的に再監査 |
| `ai_audit: 波線表示をクリアする` | 全ての波線を消す |

### ON/OFFの切り替え

設定の `aiAudit.enableOnSave` を `false` にすると、保存時の自動監査が止まります。
手動で実行したい場合だけ使えるモードです。

---

## 設定一覧

`Ctrl+,` → 検索欄に `aiAudit` と入力:

| 設定キー | デフォルト | 説明 |
|---|---|---|
| `aiAudit.apiBaseUrl` | `""` | **[必須]** LLM APIのURL |
| `aiAudit.apiKey` | `""` | **[必須]** APIキー |
| `aiAudit.modelName` | `""` | **[必須]** 使用するモデル名 |
| `aiAudit.maxOutputTokens` | `null` | 最大出力トークン数（nullでモデルのデフォルト） |
| `aiAudit.enableOnSave` | `true` | 保存時に自動監査するか |
| `aiAudit.showInformationDiagnostics` | `false` | low重要度も波線表示するか |

---

## サポート言語

現在は **Python のみ** サポートしています。
コマンドパレットから `ai_audit: サポート言語一覧を表示` で最新の対応状況を確認できます。

---

## よくある質問

**Q. 波線がなかなか出ない**
AIへのリクエスト処理中です。ファイルの大きさによっては数十秒かかることがあります。ステータスバー右下に「監査中...」と表示されている間は処理中です。

**Q. エラーメッセージが出た**
`Ctrl+Shift+P` → `ai_audit: 接続設定を開く` で API URL が正しいか確認してください。

**Q. 別のプロジェクトでも使えますか？**
はい。設定は VSCode 全体（グローバル）に保存されるため、どのプロジェクトを開いても同じ設定で使えます。

**Q. 自動監査をOFFにしたい**
設定の `aiAudit.enableOnSave` を `false` にしてください。

**Q. Pythonはインストール不要ですか？**
はい。実行エンジンはVSIX内に同梱されているため、Python のインストールは不要です。

---

## 開発者向け: VSIXのビルド方法

VSIXを自分でビルドする場合は、`vscode-extension/` フォルダ内のスクリプトを使用します。

```bash
# Windows (コマンドプロンプトで実行)
build_win.bat

# macOS
./build_mac.sh

# Linux
./build_linux.sh
```

各スクリプトは以下を自動実行します:
1. PyInstaller で実行バイナリをビルド（`bin/<os>/main[.exe]`）
2. TypeScript をコンパイル
3. VSIX をパッケージング（`ai-audit-<os>-<version>.vsix`）
