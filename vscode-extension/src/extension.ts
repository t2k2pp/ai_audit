/**
 * ai_audit VSCode 拡張機能 (v0.4)
 *
 * 利用者の動線:
 *   1. VSIXをVSCodeにインストール
 *   2. コマンドパレット → "ai_audit: 接続設定を開く" → API URL / APIキー / モデル名を入力
 *   3. 初回起動時に Python 未検出 / 設定未入力なら案内メッセージを表示
 *   4. Pythonファイルを保存するだけで監査が走り、波線で結果が出る
 *
 * 設定は VSCode の設定画面で管理する（.env / config.json は利用者が意識しない）
 * main.py 起動時に VSCode 設定を環境変数として渡すことで .env を不要にする
 */

import * as cp from "child_process";
import * as fs from "fs";
import * as http from "http";
import * as path from "path";
import * as vscode from "vscode";

// ---------------------------------------------------------------------------
// サポート言語定義（将来の拡張に備えて一元管理）
// ---------------------------------------------------------------------------
const SUPPORTED_LANGUAGES: Array<{
  id: string;
  label: string;
  status: "supported" | "planned";
  since: string;
}> = [
  { id: "python",          label: "Python",           status: "supported", since: "v0.1.0" },
  { id: "javascript",      label: "JavaScript",       status: "supported", since: "v0.3.0" },
  { id: "javascriptreact", label: "JavaScript (JSX)", status: "supported", since: "v0.3.0" },
  { id: "typescript",      label: "TypeScript",       status: "supported", since: "v0.3.0" },
  { id: "typescriptreact", label: "TypeScript (TSX)", status: "supported", since: "v0.3.0" },
  { id: "dart",            label: "Dart",             status: "supported", since: "v0.3.1" },
  { id: "go",         label: "Go",         status: "planned",   since: "-" },
  { id: "csharp",     label: "C#",         status: "planned",   since: "-" },
];

/** 拡張子 → 言語 ID（エクスプローラーから呼ばれた場合の判定用） */
const EXT_TO_LANG: Record<string, string> = {
  ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
  ".jsx": "javascriptreact", ".ts": "typescript", ".tsx": "typescriptreact", ".dart": "dart",
};

// ---------------------------------------------------------------------------
// グローバル状態
// ---------------------------------------------------------------------------
let diagnosticCollection: vscode.DiagnosticCollection;
const runningAudits = new Set<string>();
let statusBarItem: vscode.StatusBarItem;
let extensionPath: string;

// 設計思想 CodeLens + TreeView 用
let whyLensProvider: AiAuditWhyLensProvider | undefined;
let whyTreeProvider: AiAuditWhyTreeProvider | undefined;

// ---------------------------------------------------------------------------
// 有効化エントリポイント
// ---------------------------------------------------------------------------
export function activate(context: vscode.ExtensionContext) {
  extensionPath = context.extensionPath;

  diagnosticCollection = vscode.languages.createDiagnosticCollection("ai_audit");
  context.subscriptions.push(diagnosticCollection);

  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = "$(shield) ai_audit";
  statusBarItem.tooltip = "クリックして設定を開く";
  statusBarItem.command = "aiAudit.openSettings";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // 起動時に必須設定チェック
  checkSetupOnStartup(context);

  // ファイル保存時に自動監査（サポート言語のみ）
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      if (!cfg.get<boolean>("enableOnSave", true)) { return; }
      const supported = SUPPORTED_LANGUAGES.find(
        (l) => l.id === doc.languageId && l.status === "supported"
      );
      if (supported) {
        runAudit(doc.uri.fsPath, false);
      }
    })
  );

  // コマンド登録
  // エクスプローラー右クリックから呼ばれると uri 引数が渡される
  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.auditCurrentFile", (uri?: vscode.Uri) => {
      const filePath = uri?.fsPath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
      if (!filePath) { return; }
      const langId = uri
        ? (EXT_TO_LANG[path.extname(filePath).toLowerCase()] ?? "")
        : (vscode.window.activeTextEditor?.document.languageId ?? "");
      const supported = SUPPORTED_LANGUAGES.find(
        (l) => l.id === langId && l.status === "supported"
      );
      if (!supported) {
        vscode.window.showWarningMessage(
          `ai_audit: このファイル形式はまだサポートされていません。` +
          `サポート言語: コマンドパレットから "ai_audit: サポート言語一覧を表示" で確認できます。`
        );
        return;
      }
      runAudit(filePath, false);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.auditCurrentFileForce", (uri?: vscode.Uri) => {
      const filePath = uri?.fsPath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
      if (!filePath) { return; }
      runAudit(filePath, true);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.auditFolder", async (uri?: vscode.Uri) => {
      // エクスプローラー右クリック → uri あり、コマンドパレット → フォルダ選択ダイアログ
      let folderPath: string | undefined = uri?.fsPath;
      if (!folderPath) {
        const picked = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "このフォルダを一括監査する",
        });
        folderPath = picked?.[0]?.fsPath;
      }
      if (!folderPath) { return; }
      runAuditFolder(folderPath);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.clearDiagnostics", () => {
      diagnosticCollection.clear();
      statusBarItem.text = "$(shield) ai_audit";
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.openSettings", () => {
      vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "aiAudit"
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.showSupportedLanguages", () => {
      showSupportedLanguages();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.selectModel", () => {
      selectModel();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.setupWhyFeature", async () => {
      await setupWhyFeature();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.extractWhy", async (uri?: vscode.Uri) => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      if (!cfg.get<boolean>("enableWhyFeature", false)) {
        const action = await vscode.window.showInformationMessage(
          "ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？",
          "セットアップする",
          "キャンセル"
        );
        if (action === "セットアップする") {
          await setupWhyFeature();
        }
        return;
      }
      // エクスプローラー右クリック → uri あり、コマンドパレット → ワークスペースルート
      const folderPath = uri?.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folderPath) {
        vscode.window.showWarningMessage("ai_audit: ワークスペースを開いた状態で実行してください。");
        return;
      }
      runBackendCommand("extract_why", [folderPath], "extractWhy");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.extractWhyForce", async (uri?: vscode.Uri) => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      if (!cfg.get<boolean>("enableWhyFeature", false)) {
        const action = await vscode.window.showInformationMessage(
          "ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？",
          "セットアップする",
          "キャンセル"
        );
        if (action === "セットアップする") {
          await setupWhyFeature();
        }
        return;
      }
      const folderPath = uri?.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folderPath) {
        vscode.window.showWarningMessage("ai_audit: ワークスペースを開いた状態で実行してください。");
        return;
      }
      runBackendCommand("extract_why", [folderPath, "--force"], "extractWhy");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.searchWhy", async () => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      if (!cfg.get<boolean>("enableWhyFeature", false)) {
        const action = await vscode.window.showInformationMessage(
          "ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？",
          "セットアップする",
          "キャンセル"
        );
        if (action === "セットアップする") {
          await setupWhyFeature();
        }
        return;
      }
      const query = await vscode.window.showInputBox({
        title: "ai_audit: 設計思想を検索",
        prompt: "検索キーワードを入力してください（例: キャッシュ戦略、エラーハンドリング）",
        placeHolder: "検索キーワード",
      });
      if (!query) { return; }
      const root = await pickWhyRoot();
      if (!root) { return; }
      runBackendCommand("search_why", [query, "--dir", root], "searchWhy");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.reviewArchitecture", (uri?: vscode.Uri) => {
      // エクスプローラー右クリック → uri あり、コマンドパレット → ワークスペースルート
      const folderPath = uri?.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folderPath) {
        vscode.window.showWarningMessage("ai_audit: ワークスペースを開いた状態で実行してください。");
        return;
      }
      // 解析対象フォルダ内に _architecture.md を出力させる（--output フラグで指定）
      const outputMd = path.join(folderPath, "_architecture.md");
      runBackendCommand("review_architecture", [folderPath, "--output", outputMd], "reviewArchitecture");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.generateDesignDoc", async (uri?: vscode.Uri) => {
      // エクスプローラー右クリック → uri あり、コマンドパレット → フォルダ選択ダイアログ
      let folderPath: string | undefined = uri?.fsPath;
      if (!folderPath) {
        const picked = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "このフォルダの設計書を生成する",
        });
        folderPath = picked?.[0]?.fsPath;
      }
      if (!folderPath) { return; }
      runBackendCommand("generate_design_doc", [folderPath], "generateDesignDoc");
    })
  );

  // Code Action: 指摘をCopilot Chatへ追記
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "aiAudit.sendToCopilotChat",
      async (diagnostic: vscode.Diagnostic) => {
        const text = buildPromptFromDiagnostic(diagnostic);
        // Copilot Chat が利用可能なら chat パネルへ書き込む
        const copilotAvailable = vscode.extensions.getExtension("GitHub.copilot-chat") !== undefined;
        if (copilotAvailable) {
          await vscode.commands.executeCommand("workbench.panel.chat.view.copilot.focus");
          await vscode.commands.executeCommand(
            "workbench.action.chat.sendToNewChat",
            { inputValue: text }
          );
        } else {
          // Copilot 未インストールの場合はクリップボードへ
          const current = await vscode.env.clipboard.readText();
          const appended = current.endsWith("\n") || current === ""
            ? current + text
            : current + "\n" + text;
          await vscode.env.clipboard.writeText(appended + "\n");
          vscode.window.showInformationMessage(
            "ai_audit: GitHub Copilot Chat が見つからないためクリップボードにコピーしました。"
          );
        }
      }
    )
  );

  // Code Action: 指摘をクリップボードへ追記
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "aiAudit.copyToClipboard",
      async (diagnostic: vscode.Diagnostic) => {
        const text = buildPromptFromDiagnostic(diagnostic);
        const current = await vscode.env.clipboard.readText();
        const appended = current.endsWith("\n") || current === ""
          ? current + text
          : current + "\n" + text;
        await vscode.env.clipboard.writeText(appended + "\n");
        vscode.window.showInformationMessage(
          "ai_audit: クリップボードに追記しました。AIチャットに貼り付けてください。"
        );
      }
    )
  );

  // Code Action プロバイダー登録（波線ホバー時にボタンを表示）
  const supportedLanguageIds = SUPPORTED_LANGUAGES
    .filter((l) => l.status === "supported")
    .map((l) => ({ language: l.id }));
  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      supportedLanguageIds,
      new AiAuditCodeActionProvider(),
      { providedCodeActionKinds: AiAuditCodeActionProvider.providedKinds }
    )
  );

  // ---------------------------------------------------------------------------
  // 設計思想 CodeLens プロバイダー登録
  // ---------------------------------------------------------------------------
  whyLensProvider = new AiAuditWhyLensProvider();
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(
      supportedLanguageIds,
      whyLensProvider
    )
  );

  // ---------------------------------------------------------------------------
  // 設計思想 TreeView プロバイダー登録
  // ---------------------------------------------------------------------------
  whyTreeProvider = new AiAuditWhyTreeProvider();
  const treeView = vscode.window.createTreeView("aiAuditWhyView", {
    treeDataProvider: whyTreeProvider,
    showCollapseAll: true,
  });
  context.subscriptions.push(treeView);

  // ---------------------------------------------------------------------------
  // 設計思想一覧コマンド
  // ---------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.listWhy", async () => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      if (!cfg.get<boolean>("enableWhyFeature", false)) {
        const action = await vscode.window.showInformationMessage(
          "ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？",
          "セットアップする",
          "キャンセル"
        );
        if (action === "セットアップする") { await setupWhyFeature(); }
        return;
      }
      const root = await pickWhyRoot();
      if (!root) { return; }
      runBackendCommand("list_why", ["--dir", root], "listWhy");
    })
  );

  // ---------------------------------------------------------------------------
  // CodeLens ON/OFF 切り替えコマンド
  // ---------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.toggleWhyLens", async () => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      const current = cfg.get<boolean>("showWhyLens", false);
      await cfg.update("showWhyLens", !current, vscode.ConfigurationTarget.Global);
      vscode.window.showInformationMessage(
        `ai_audit: 設計思想 CodeLens を${!current ? "ON" : "OFF"} にしました。`
      );
      whyLensProvider?.refresh();
    })
  );

  // ---------------------------------------------------------------------------
  // 監査波線 ON/OFF 切り替えコマンド
  // ---------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.toggleAuditDiagnostics", async () => {
      const cfg = vscode.workspace.getConfiguration("aiAudit");
      const current = cfg.get<boolean>("showAuditDiagnostics", true);
      await cfg.update("showAuditDiagnostics", !current, vscode.ConfigurationTarget.Global);
      if (!current) {
        vscode.window.showInformationMessage("ai_audit: 監査波線表示を ON にしました。");
      } else {
        diagnosticCollection.clear();
        vscode.window.showInformationMessage("ai_audit: 監査波線表示を OFF にしました。");
      }
    })
  );

  // ---------------------------------------------------------------------------
  // TreeView 再読み込みコマンド
  // ---------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("aiAudit.refreshWhyView", () => {
      whyTreeProvider?.refresh();
    })
  );

  // ---------------------------------------------------------------------------
  // 設計思想詳細ポップアップ（CodeLens クリック / TreeView クリック）
  // ---------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "aiAudit.showWhyDetail",
      (whyText: string, funcName: string) => {
        showWebview(
          `💡 設計思想: ${funcName}`,
          `<style>
            body { font-family: var(--vscode-font-family); padding: 16px; line-height: 1.7; }
            h2 { color: var(--vscode-textLink-foreground); }
            pre { background: var(--vscode-textBlockQuote-background);
                  padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }
          </style>
          <h2>💡 ${escapeHtml(funcName)}</h2>
          <pre>${escapeHtml(whyText)}</pre>`
        );
      }
    )
  );

  // 設定変更時に CodeLens を更新
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("aiAudit.showWhyLens")) {
        whyLensProvider?.refresh();
      }
    })
  );
}

export function deactivate() {
  diagnosticCollection.clear();
}

// ---------------------------------------------------------------------------
// 起動時セットアップチェック
// ---------------------------------------------------------------------------
async function checkSetupOnStartup(_context: vscode.ExtensionContext): Promise<void> {
  const cfg = vscode.workspace.getConfiguration("aiAudit");
  const apiUrl = cfg.get<string>("apiBaseUrl", "").trim();
  const apiKey = cfg.get<string>("apiKey", "").trim();
  const model  = cfg.get<string>("modelName", "").trim();

  const missing: string[] = [];
  if (!apiUrl)  { missing.push("API URL (aiAudit.apiBaseUrl)"); }
  // apiKey は任意（Ollama等APIキー不要な環境では空でよい）
  if (!model)   { missing.push("モデル名 (aiAudit.modelName)"); }

  // 同梱バイナリの存在チェック
  const binaryPath = resolveBackendBinary();
  const binaryOk = binaryPath ? fs.existsSync(binaryPath) : false;

  if (missing.length > 0 || !binaryOk) {
    const messages: string[] = [];
    if (!binaryOk) {
      messages.push(
        `お使いのOS（${process.platform}）に対応したバイナリが見つかりません。\n` +
        `正しいOS用の VSIX をインストールしてください。`
      );
    }
    if (missing.length > 0) {
      messages.push(`以下の必須設定が未入力です:\n  ・${missing.join("\n  ・")}`);
    }

    const action = await vscode.window.showWarningMessage(
      `ai_audit: セットアップが必要です。\n${messages.join("\n\n")}`,
      "設定画面を開く",
      "後で"
    );
    if (action === "設定画面を開く") {
      vscode.commands.executeCommand("aiAudit.openSettings");
    }
  }
}

// ---------------------------------------------------------------------------
// モデル切り替え UI
// ---------------------------------------------------------------------------
async function selectModel(): Promise<void> {
  const cfg    = vscode.workspace.getConfiguration("aiAudit");
  const apiUrl = cfg.get<string>("apiBaseUrl", "").trim();

  if (!apiUrl) {
    const action = await vscode.window.showErrorMessage(
      "ai_audit: API URL が設定されていません。先に設定画面で API URL を入力してください。",
      "設定画面を開く"
    );
    if (action === "設定画面を開く") {
      vscode.commands.executeCommand("aiAudit.openSettings");
    }
    return;
  }

  // Ollama の /api/tags を呼ぶ
  statusBarItem.text = "$(sync~spin) ai_audit: モデル一覧を取得中...";
  let models: Array<{ name: string; size: string }>;
  try {
    models = await fetchOllamaModels(apiUrl);
  } catch (e) {
    statusBarItem.text = "$(shield) ai_audit";
    vscode.window.showErrorMessage(
      `ai_audit: モデル一覧の取得に失敗しました。\n` +
      `接続先: ${apiUrl}\n` +
      `エラー: ${e}\n\n` +
      `設定画面の "API URL" が正しいか確認してください。`
    );
    return;
  }
  statusBarItem.text = "$(shield) ai_audit";

  const currentModel = cfg.get<string>("modelName", "");
  const items: vscode.QuickPickItem[] = models.map((m) => ({
    label: m.name,
    description: m.size,
    detail: m.name === currentModel ? "← 現在使用中" : undefined,
  }));

  const selected = await vscode.window.showQuickPick(items, {
    title: "ai_audit: 使用するモデルを選択",
    placeHolder: "モデル名を選択してください",
    matchOnDescription: true,
  });

  if (!selected) { return; }

  await cfg.update("modelName", selected.label, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(
    `ai_audit: モデルを "${selected.label}" に変更しました。`
  );
}

function fetchOllamaModels(baseUrl: string): Promise<Array<{ name: string; size: string }>> {
  return new Promise((resolve, reject) => {
    // /v1 を除いて /api/tags を呼ぶ
    let ollamaBase = baseUrl.replace(/\/v1\/?$/, "");
    const url = new URL("/api/tags", ollamaBase);

    const req = http.get(url.toString(), (res) => {
      let data = "";
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => {
        try {
          const json = JSON.parse(data);
          const models = (json.models ?? []).map((m: {
            name: string;
            size?: number;
          }) => ({
            name: m.name,
            size: m.size ? `${(m.size / 1_073_741_824).toFixed(1)} GB` : "?",
          }));
          resolve(models);
        } catch (e) {
          reject(new Error(`レスポンスの解析に失敗: ${e}`));
        }
      });
    });
    req.on("error", (e) => reject(e));
    req.setTimeout(10000, () => {
      req.destroy();
      reject(new Error("タイムアウト（10秒）"));
    });
  });
}

// ---------------------------------------------------------------------------
// サポート言語一覧表示
// ---------------------------------------------------------------------------
function showSupportedLanguages(): void {
  const rows = SUPPORTED_LANGUAGES.map((l) => {
    const status = l.status === "supported" ? "✅ サポート中" : "🔜 対応予定";
    return `<tr><td>${l.label}</td><td>${status}</td><td>${l.since}</td></tr>`;
  }).join("");

  const panel = vscode.window.createWebviewPanel(
    "aiAuditLanguages",
    "ai_audit: サポート言語",
    vscode.ViewColumn.Beside,
    {
      enableScripts: false,
      retainContextWhenHidden: false,
    }
  );

  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
  <meta charset="UTF-8">
</head>
<body style="font-family:sans-serif;padding:20px">
  <h2>ai_audit サポート言語一覧</h2>
  <table border="1" cellpadding="8" cellspacing="0">
    <thead>
      <tr><th>言語</th><th>状態</th><th>対応バージョン</th></tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>
  <p style="color:gray;margin-top:16px">※ 対応予定言語へのリクエストはIssueでお知らせください。</p>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// OS別バイナリパス解決
// ---------------------------------------------------------------------------
function resolveBackendBinary(): string | null {
  const platform = process.platform; // "win32" | "darwin" | "linux"
  let subDir: string;
  let binName: string;

  if (platform === "win32") {
    subDir = "win";
    binName = "main.exe";
  } else if (platform === "darwin") {
    subDir = "mac";
    binName = "main";
  } else {
    subDir = "linux";
    binName = "main";
  }

  return path.join(extensionPath, "bin", subDir, binName);
}

// ---------------------------------------------------------------------------
// 監査実行
// ---------------------------------------------------------------------------
/** VSCode 設定をバックエンドの環境変数に変換する（.env 不要）。必須設定がなければ null。 */
function buildBackendEnv(): NodeJS.ProcessEnv | null {
  const cfg = vscode.workspace.getConfiguration("aiAudit");
  const apiUrl    = cfg.get<string>("apiBaseUrl", "").trim();
  const modelName = cfg.get<string>("modelName", "").trim();
  const missing: string[] = [];
  if (!apiUrl)    { missing.push("API URL"); }
  if (!modelName) { missing.push("モデル名"); }
  if (missing.length > 0) {
    vscode.window.showErrorMessage(
      `ai_audit: 設定が不足しています。コマンドパレットから "ai_audit: 接続設定を開く" を実行して設定してください。\n未入力: ${missing.join(", ")}`
    );
    return null;
  }
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONUTF8: "1",          // Windows CP932 環境での文字化け防止
    LLM_API_BASE_URL: apiUrl,
    LLM_MODEL_NAME:   modelName,
  };
  const str = (key: string) => cfg.get<string>(key, "").trim();
  const num = (key: string) => cfg.get<number | null>(key, null);
  const setIf = (name: string, value: string | number | null | undefined) => {
    if (value !== null && value !== undefined && value !== "") { env[name] = String(value); }
  };
  setIf("LLM_API_KEY", str("apiKey"));
  setIf("LLM_MAX_OUTPUT_TOKENS", num("maxOutputTokens"));
  setIf("LLM_CONTEXT_LENGTH", num("contextLength"));
  setIf("LLM_CONTEXT_BUDGET", num("contextBudget"));
  const tier = str("tier");
  setIf("AI_AUDIT_TIER", tier === "auto" ? "" : tier);
  setIf("LLM_REASONING", str("reasoning"));
  setIf("LLM_EXTRA_BODY", str("extraBody"));
  setIf("LLM_PARALLEL", num("parallel"));
  setIf("LLM_EMBEDDING_MODEL", str("embeddingModel"));
  setIf("LLM_SERVER_TYPE", str("serverType"));
  return env;
}

/** 同梱バイナリのパス（存在しなければエラー表示して null）。 */
function requireBackendBinary(): string | null {
  const binaryPath = resolveBackendBinary();
  if (!binaryPath || !fs.existsSync(binaryPath)) {
    vscode.window.showErrorMessage(
      `ai_audit: バイナリが見つかりません（${binaryPath}）。\n` +
      `お使いのOSに対応した VSIX を再インストールしてください。`
    );
    return null;
  }
  return binaryPath;
}

/** ソースファイルに対応する監査結果 JSON のパス（拡張子を _audit.json に置換）。 */
function auditJsonPathFor(filePath: string): string {
  return filePath.replace(/\.[^./\\]+$/, "") + "_audit.json";
}

function runAudit(filePath: string, force: boolean): void {
  if (runningAudits.has(filePath)) { return; }
  const env = buildBackendEnv();
  const binaryPath = env ? requireBackendBinary() : null;
  if (!env || !binaryPath) { return; }
  runningAudits.add(filePath);

  const args = ["audit", filePath];
  if (force) { args.push("--force"); }

  const shortName = path.basename(filePath);
  statusBarItem.text = `$(sync~spin) ai_audit: ${shortName} を監査中...`;

  const proc = cp.spawn(binaryPath, args, { cwd: path.dirname(binaryPath), env });
  const stderrChunks: Buffer[] = [];
  proc.stderr.on("data", (data: Buffer) => { stderrChunks.push(data); });
  proc.on("error", (err) => {
    runningAudits.delete(filePath);
    statusBarItem.text = "$(shield) ai_audit";
    vscode.window.showErrorMessage(`ai_audit: 起動エラー: ${err.message}`);
  });

  proc.on("close", (code) => {
    runningAudits.delete(filePath);
    statusBarItem.text = "$(shield) ai_audit";
    if (code !== 0) {
      vscode.window.showErrorMessage(`ai_audit エラー: ${decodeBuffer(stderrChunks).slice(-300)}`);
      return;
    }
    const auditJsonPath = auditJsonPathFor(filePath);
    if (!fs.existsSync(auditJsonPath)) {
      diagnosticCollection.set(vscode.Uri.file(filePath), []);
      return;
    }
    try {
      const auditResult = JSON.parse(fs.readFileSync(auditJsonPath, "utf-8"));
      applyDiagnostics(filePath, auditResult);
      const total = auditResult.total_issues ?? 0;
      statusBarItem.text = total > 0
        ? `$(warning) ai_audit: ${total} 件の指摘`
        : "$(pass) ai_audit: 問題なし";
    } catch (e) {
      vscode.window.showErrorMessage(`ai_audit: 結果の読み込みに失敗しました: ${e}`);
    }
  });
}

// ---------------------------------------------------------------------------
// フォルダ一括監査
// ---------------------------------------------------------------------------
function runAuditFolder(folderPath: string): void {
  const env = buildBackendEnv();
  const binaryPath = env ? requireBackendBinary() : null;
  if (!env || !binaryPath) { return; }

  const shortName = path.basename(folderPath);
  statusBarItem.text = `$(sync~spin) ai_audit: ${shortName}/ を一括監査中...`;
  const proc = cp.spawn(binaryPath, ["audit", folderPath], { cwd: path.dirname(binaryPath), env });
  const stderrChunks: Buffer[] = [];
  proc.stderr.on("data", (data: Buffer) => { stderrChunks.push(data); });
  proc.stdout.on("data", (data: Buffer) => { updateProgressFromOutput(data, `${shortName}/ を監査中`); });

  proc.on("close", (code) => {
    statusBarItem.text = "$(shield) ai_audit";
    if (code !== 0) {
      vscode.window.showErrorMessage(`ai_audit エラー: ${decodeBuffer(stderrChunks).slice(-300)}`);
      return;
    }
    // フォルダ以下の _audit.json をすべて探して Diagnostics に反映する
    let totalIssues = 0;
    let fileCount = 0;
    const SKIP = new Set(["node_modules", ".git", ".ai_audit", "_design", "__pycache__", ".venv", "venv"]);
    const applyAll = (dir: string) => {
      let entries: string[];
      try { entries = fs.readdirSync(dir); } catch { return; }
      for (const entry of entries) {
        const fullPath = path.join(dir, entry);
        try {
          const stat = fs.statSync(fullPath);
          if (stat.isDirectory()) {
            if (!SKIP.has(entry)) { applyAll(fullPath); }
          } else if (entry.endsWith("_audit.json") && entry !== "_summary_audit.json") {
            const auditResult = JSON.parse(fs.readFileSync(fullPath, "utf-8"));
            const srcFile = auditResult.source_file ?? fullPath.replace(/_audit\.json$/, ".py");
            applyDiagnostics(srcFile, auditResult);
            totalIssues += auditResult.total_issues ?? 0;
            fileCount++;
          }
        } catch { /* 読み込み失敗はスキップ */ }
      }
    };
    applyAll(folderPath);
    statusBarItem.text = totalIssues > 0
      ? `$(warning) ai_audit: ${totalIssues} 件の指摘`
      : "$(pass) ai_audit: 問題なし";
    vscode.window.showInformationMessage(
      `ai_audit: ${shortName}/ の一括監査が完了しました。${fileCount} ファイル / ${totalIssues} 件の指摘`
    );
  });
}

/** バックエンドの進捗出力（[STAGE] / [n/m]）をステータスバーに反映する。 */
function updateProgressFromOutput(data: Buffer, label: string): void {
  const text = data.toString("utf-8");
  const stage = [...text.matchAll(/\[STAGE\] ([^\n（]+)/g)].pop();
  const prog  = [...text.matchAll(/\[(\d+)\/(\d+)\]/g)].pop();
  if (stage) { lastStage = stage[1].trim(); }
  if (prog || stage) {
    const p = prog ? ` ${prog[1]}/${prog[2]}` : "";
    statusBarItem.text = `$(sync~spin) ai_audit: ${label}${lastStage ? `（${lastStage}${p}）` : p}`;
  }
}
let lastStage = "";

// ---------------------------------------------------------------------------
// Diagnostics 変換
// ---------------------------------------------------------------------------
function severityToDiagnosticSeverity(severity: string): vscode.DiagnosticSeverity {
  switch (severity?.toLowerCase()) {
    case "high":   return vscode.DiagnosticSeverity.Error;
    case "medium": return vscode.DiagnosticSeverity.Warning;
    default:       return vscode.DiagnosticSeverity.Information;
  }
}

interface AuditIssue {
  type: string;
  severity: string;
  line?: number;                     // v0.4: ファイル内の絶対行番号（1 始まり）
  line_number_offset?: number | null;
  line_approximate?: boolean;
  title?: string;
  description: string;
  suggestion: string;
  confidence?: string;
}

function applyDiagnostics(filePath: string, auditResult: {
  chunks: Array<{ chunk_id: string; name?: string; start_line?: number; issues: AuditIssue[] }>;
}): void {
  const cfg = vscode.workspace.getConfiguration("aiAudit");
  // 監査波線表示が OFF の場合は何もしない
  if (!cfg.get<boolean>("showAuditDiagnostics", true)) {
    diagnosticCollection.set(vscode.Uri.file(filePath), []);
    return;
  }
  const showInfo = cfg.get<boolean>("showInformationDiagnostics", false);

  const diagnostics: vscode.Diagnostic[] = [];
  let fileLines: string[] = [];
  try {
    fileLines = fs.readFileSync(filePath, "utf-8").split("\n");
  } catch { /* フォールバック */ }

  for (const chunk of auditResult.chunks ?? []) {
    // v0.3 形式（line なし）の場合は定義行を探して offset を足す
    let chunkStartLine = (chunk.start_line ?? 1) - 1;
    if (chunk.start_line === undefined) {
      const funcName = chunk.chunk_id.split(/[:.]/).pop() ?? "";
      const defPattern = new RegExp(`^\\s*(def|class)\\s+${escapeRegex(funcName)}\\s*[:(]`);
      const found = fileLines.findIndex((l) => defPattern.test(l));
      chunkStartLine = found >= 0 ? found : 0;
    }

    for (const issue of chunk.issues ?? []) {
      const diagSeverity = severityToDiagnosticSeverity(issue.severity);
      if (!showInfo && diagSeverity === vscode.DiagnosticSeverity.Information) { continue; }

      const targetLine = typeof issue.line === "number"
        ? Math.max(0, issue.line - 1)
        : chunkStartLine + (issue.line_number_offset ?? 0);
      const lineText = fileLines[targetLine] ?? "";
      const indent   = lineText.length - lineText.trimStart().length;
      const range    = new vscode.Range(targetLine, indent, targetLine, Math.max(lineText.length, indent + 1));

      const head = issue.title ? `${issue.title}: ` : "";
      const note = issue.line_approximate ? "（行は推定）" : "";
      const conf = issue.confidence === "low" ? "（確度: 低）" : "";
      const diag = new vscode.Diagnostic(
        range,
        `[ai_audit/${issue.type}] ${head}${issue.description}${note}${conf}`,
        diagSeverity
      );
      diag.source = "ai_audit";
      if (issue.suggestion) {
        diag.relatedInformation = [
          new vscode.DiagnosticRelatedInformation(
            new vscode.Location(vscode.Uri.file(filePath), range),
            `修正提案: ${issue.suggestion}`
          ),
        ];
      }
      diagnostics.push(diag);
    }
  }

  diagnosticCollection.set(vscode.Uri.file(filePath), diagnostics);
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Buffer 配列を結合し、UTF-8 → CP932（Shift-JIS）の順にデコードを試みる。
 * Windows の Python バイナリは CP932 で stderr を出力することがある。
 */
function decodeBuffer(chunks: Buffer[]): string {
  const buf = Buffer.concat(chunks);
  // まず UTF-8 として解釈（文字化け判定: replacement character が含まれないか）
  const utf8 = buf.toString("utf-8");
  if (!utf8.includes("\uFFFD")) {
    return utf8;
  }
  // UTF-8 で文字化けしている場合は CP932（Shift-JIS）でデコード
  try {
    return new TextDecoder("shift_jis").decode(buf);
  } catch {
    return utf8; // TextDecoder が失敗したら UTF-8 フォールバック
  }
}

// ---------------------------------------------------------------------------
// バックエンドコマンド汎用実行（extract-why / search-why / review-architecture）
// ---------------------------------------------------------------------------
type BackendCommandId = "extractWhy" | "searchWhy" | "listWhy" | "reviewArchitecture" | "generateDesignDoc";

const BACKEND_COMMAND_LABELS: Record<BackendCommandId, string> = {
  extractWhy:          "設計思想を抽出中",
  searchWhy:           "設計思想を検索中",
  listWhy:             "設計思想を読み込み中",
  reviewArchitecture:  "アーキテクチャを解析中",
  generateDesignDoc:   "設計書を生成中",
};

const BACKEND_COMMAND_TITLES: Record<BackendCommandId, string> = {
  extractWhy:          "ai_audit: 設計思想抽出",
  searchWhy:           "ai_audit: 設計思想検索",
  listWhy:             "ai_audit: 設計思想一覧",
  reviewArchitecture:  "ai_audit: アーキテクチャ解析",
  generateDesignDoc:   "ai_audit: 設計書生成",
};

function runBackendCommand(
  subCommand: string,
  args: string[],
  commandId: BackendCommandId
): void {
  // v0.4: 設計思想も含め、すべて同梱バイナリで実行する（ChromaDB 依存を廃止したため）
  const env = buildBackendEnv();
  const binaryPath = env ? requireBackendBinary() : null;
  if (!env || !binaryPath) { return; }

  const label = BACKEND_COMMAND_LABELS[commandId];
  statusBarItem.text = `$(sync~spin) ai_audit: ${label}...`;
  lastStage = "";

  const proc = cp.spawn(binaryPath, [subCommand, ...args], { cwd: path.dirname(binaryPath), env });
  const stdoutChunks: Buffer[] = [];
  const stderrChunks: Buffer[] = [];
  proc.stdout?.on("data", (data: Buffer) => {
    stdoutChunks.push(data);
    updateProgressFromOutput(data, label);
  });
  proc.stderr.on("data",  (data: Buffer) => { stderrChunks.push(data); });

  proc.on("error", (err) => {
    statusBarItem.text = "$(shield) ai_audit";
    vscode.window.showErrorMessage(`ai_audit: 起動エラー: ${err.message}`);
  });

  proc.on("close", (code) => {
    statusBarItem.text = "$(shield) ai_audit";
    const stdout = decodeBuffer(stdoutChunks);
    const stderr = decodeBuffer(stderrChunks);

    // generate_design_doc は一部失敗時に終了コード 2（生成済みの部分は出力される）
    if (code === 2 && commandId === "generateDesignDoc") {
      vscode.window.showWarningMessage(
        "ai_audit: 一部の生成に失敗しました。もう一度実行すると、失敗した部分だけを再生成します。"
      );
    } else if (code !== 0) {
      vscode.window.showErrorMessage(`ai_audit エラー: ${stderr.slice(-300)}`);
      return;
    }

    const title = BACKEND_COMMAND_TITLES[commandId];
    if (commandId === "extractWhy") {
      showWhyResultInWebview(title, args[0]);
      whyTreeProvider?.refresh();
      whyLensProvider?.refresh();
    } else if (commandId === "searchWhy" || commandId === "listWhy") {
      showTextResultInWebview(title, stdout || stderr);
    } else if (commandId === "reviewArchitecture") {
      // args = [folderPath, "--output", outputMdPath]
      openMarkdownPreview(args[2] ?? path.join(args[0], "_architecture.md"));
    } else if (commandId === "generateDesignDoc") {
      const outDir = path.join(args[0], "_design");
      openMarkdownPreview(path.join(outDir, "detail.md"));
      openMarkdownPreview(path.join(outDir, "overview.md"));
      vscode.window.showInformationMessage(
        `ai_audit: 設計書を生成しました（${outDir}）。目次は README.md です。`
      );
    }
  });
}

/** Markdown を VSCode 標準のプレビューで開く（Mermaid は対応拡張があれば描画される）。 */
function openMarkdownPreview(mdPath: string): void {
  if (!fs.existsSync(mdPath)) {
    vscode.window.showWarningMessage(`ai_audit: 結果ファイルが見つかりません（${mdPath}）`);
    return;
  }
  vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(mdPath));
}

// ---------------------------------------------------------------------------
// Webview 表示ヘルパー
// ---------------------------------------------------------------------------

function showWhyResultInWebview(title: string, rootDir: string): void {
  const jsonPath = path.join(rootDir, ".ai_audit", "why.json");
  if (!fs.existsSync(jsonPath)) {
    vscode.window.showWarningMessage(`ai_audit: 結果ファイルが見つかりません（${jsonPath}）`);
    return;
  }
  let data: Record<string, WhyEntry[]> = {};
  try {
    data = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  } catch {
    vscode.window.showErrorMessage(`ai_audit: 結果ファイルの読み込みに失敗しました（${jsonPath}）`);
    return;
  }
  const rows = Object.entries(data).flatMap(([file, entries]) => entries.map((item) => `
    <div class="card">
      <div class="chunk-id">${escapeHtml(file)}:${item.line} — ${escapeHtml(item.qualname)}</div>
      <div class="why">${escapeHtml(item.why ?? "").replace(/\n/g, "<br>")}</div>
    </div>
  `)).join("");

  showWebview(title, `
    <style>
      body { font-family: var(--vscode-font-family); padding: 16px; }
      .card { border: 1px solid var(--vscode-panel-border); border-radius: 4px;
              padding: 12px; margin-bottom: 12px; }
      .chunk-id { font-weight: bold; color: var(--vscode-textLink-foreground);
                  margin-bottom: 6px; font-size: 0.9em; }
      .why { line-height: 1.6; }
    </style>
    <h2>${escapeHtml(title)}</h2>
    ${rows || "<p>結果がありません。</p>"}
  `);
}

function showTextResultInWebview(title: string, text: string): void {
  showWebview(title, `
    <style>
      body { font-family: var(--vscode-font-family); padding: 16px; }
      pre { background: var(--vscode-textBlockQuote-background);
            padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }
    </style>
    <h2>${escapeHtml(title)}</h2>
    <pre>${escapeHtml(text)}</pre>
  `);
}

function showWebview(title: string, bodyHtml: string): void {
  const panel = vscode.window.createWebviewPanel(
    "aiAuditResult",
    title,
    vscode.ViewColumn.Beside,
    { enableScripts: false, retainContextWhenHidden: false }
  );
  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
  <meta charset="UTF-8">
</head>
<body>
${bodyHtml}
</body>
</html>`;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// AI連携用プロンプト生成
// ---------------------------------------------------------------------------
function buildPromptFromDiagnostic(diagnostic: vscode.Diagnostic): string {
  const editor = vscode.window.activeTextEditor;
  const fileName = editor ? path.basename(editor.document.uri.fsPath) : "不明なファイル";

  // 指摘のメッセージから [ai_audit/type] プレフィックスを除いた本文を取得
  const message = typeof diagnostic.message === "string"
    ? diagnostic.message.replace(/^\[ai_audit\/[^\]]+\]\s*/, "")
    : String(diagnostic.message);

  // relatedInformation から修正提案を取得
  const suggestion = diagnostic.relatedInformation?.[0]?.message
    ?.replace(/^修正提案:\s*/, "") ?? "";

  const line = diagnostic.range.start.line + 1; // 1始まりに変換

  let prompt = `以下のコードの問題を修正してください。\n`;
  prompt += `ファイル: ${fileName} (${line}行目付近)\n`;
  prompt += `問題: ${message}\n`;
  if (suggestion) {
    prompt += `修正提案: ${suggestion}\n`;
  }
  return prompt;
}

// ---------------------------------------------------------------------------
// 設計思想機能 セットアップウィザード
// ---------------------------------------------------------------------------
async function setupWhyFeature(): Promise<void> {
  // v0.4 から追加インストールは不要（設計思想はプロジェクト内の SQLite に保存する）
  const cfg = vscode.workspace.getConfiguration("aiAudit");
  const action = await vscode.window.showInformationMessage(
    "ai_audit: 設計思想機能を有効にしますか？（追加のインストールは不要です。" +
    "埋め込みモデルを設定すると検索精度が上がります）",
    "有効にする",
    "キャンセル"
  );
  if (action !== "有効にする") { return; }
  await cfg.update("enableWhyFeature", true, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(
    "ai_audit: 設計思想機能を有効にしました。フォルダを右クリック →「設計思想を抽出」で開始します。"
  );
}

// ---------------------------------------------------------------------------
// 設計思想データ（<root>/.ai_audit/why.json）の読み込み
// ---------------------------------------------------------------------------
interface WhyEntry { id: string; name: string; qualname: string; kind: string; line: number; why: string; }

/** ワークスペース内の why.json を持つディレクトリ（抽出済みのルート）を探す。 */
function findWhyRoots(): string[] {
  const roots: string[] = [];
  const SKIP = new Set(["node_modules", ".git", ".ai_audit", "_design", "__pycache__", ".venv", "venv", "dist", "build"]);
  const walk = (dir: string, depth: number) => {
    if (fs.existsSync(path.join(dir, ".ai_audit", "why.json"))) { roots.push(dir); }
    if (depth >= 3) { return; }
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (e.isDirectory() && !SKIP.has(e.name) && !e.name.startsWith(".")) {
        walk(path.join(dir, e.name), depth + 1);
      }
    }
  };
  for (const folder of vscode.workspace.workspaceFolders ?? []) { walk(folder.uri.fsPath, 0); }
  return roots;
}

async function pickWhyRoot(): Promise<string | undefined> {
  const roots = findWhyRoots();
  if (roots.length === 0) {
    vscode.window.showWarningMessage("ai_audit: 抽出済みの設計思想がありません。先にフォルダを右クリック →「設計思想を抽出」を実行してください。");
    return undefined;
  }
  if (roots.length === 1) { return roots[0]; }
  return vscode.window.showQuickPick(roots, { title: "ai_audit: 対象のプロジェクトを選択" });
}

/** why.json の内容（ルート → {相対パス: エントリ}）のキャッシュ。 */
const _whyRootCache = new Map<string, { mtime: number; data: Record<string, WhyEntry[]> }>();

function _loadWhyRoot(root: string): Record<string, WhyEntry[]> {
  const jsonPath = path.join(root, ".ai_audit", "why.json");
  try {
    const mtime = fs.statSync(jsonPath).mtimeMs;
    const cached = _whyRootCache.get(root);
    if (cached && cached.mtime === mtime) { return cached.data; }
    const data = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
    _whyRootCache.set(root, { mtime, data });
    return data;
  } catch {
    return {};
  }
}

/** ソースファイルの設計思想エントリ（最も近い祖先の why.json から読む）。 */
function _loadWhyEntries(sourceFilePath: string): WhyEntry[] {
  let dir = path.dirname(sourceFilePath);
  for (;;) {
    if (fs.existsSync(path.join(dir, ".ai_audit", "why.json"))) {
      const rel = path.relative(dir, sourceFilePath).split(path.sep).join("/");
      return _loadWhyRoot(dir)[rel] ?? [];
    }
    const parent = path.dirname(dir);
    if (parent === dir) { return []; }
    dir = parent;
  }
}

// ---------------------------------------------------------------------------
// 設計思想 CodeLens プロバイダー
// 関数・クラスの定義行の上に「💡 設計思想: ...」を薄く表示する
// ---------------------------------------------------------------------------

class AiAuditWhyLensProvider implements vscode.CodeLensProvider {
  private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

  refresh(): void {
    _whyRootCache.clear();
    this._onDidChangeCodeLenses.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    if (!cfg.get<boolean>("showWhyLens", false)) { return []; }
    if (!cfg.get<boolean>("enableWhyFeature", false)) { return []; }

    // v0.4: why.json に定義行が記録されているので、その行に表示する
    return _loadWhyEntries(document.uri.fsPath)
      .filter((e) => e.line >= 1 && e.line <= document.lineCount)
      .map((entry) => {
        const firstLine = entry.why.split("\n")[0].trim();
        const snippet   = firstLine.length > 60 ? firstLine.slice(0, 60) + "…" : firstLine;
        return new vscode.CodeLens(new vscode.Range(entry.line - 1, 0, entry.line - 1, 0), {
          title:     `💡 設計思想: ${snippet}`,
          command:   "aiAudit.showWhyDetail",
          arguments: [entry.why, entry.qualname],
        });
      });
  }
}

// 設計思想詳細表示コマンド（CodeLens クリック時）は activate() 外でも登録できるよう遅延登録
// → activate() 内で登録済みなので不要だが、クラス外に定義して activate に入れる

// ---------------------------------------------------------------------------
// 設計思想 TreeView プロバイダー（サイドパネル一覧）
// ---------------------------------------------------------------------------

/** TreeView のノード: ファイルノード or 関数ノード */
class WhyTreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly kind: "file" | "entry",
    collapsibleState: vscode.TreeItemCollapsibleState,
    public readonly filePath?: string,
    public readonly whyText?: string,
    public readonly funcName?: string,
  ) {
    super(label, collapsibleState);
    if (kind === "file") {
      this.iconPath = new vscode.ThemeIcon("file-code");
      this.contextValue = "whyFile";
    } else {
      this.iconPath = new vscode.ThemeIcon("lightbulb");
      this.contextValue = "whyEntry";
      this.tooltip = whyText;
      // クリックで詳細表示
      this.command = {
        command: "aiAudit.showWhyDetail",
        title:   "設計思想を表示",
        arguments: [whyText ?? "", funcName ?? ""],
      };
    }
  }
}

class AiAuditWhyTreeProvider implements vscode.TreeDataProvider<WhyTreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<WhyTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  /** ワークスペース内の _why.json ファイルを再スキャンして TreeView を更新 */
  refresh(): void {
    _whyRootCache.clear();
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: WhyTreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: WhyTreeItem): Promise<WhyTreeItem[]> {
    if (!element) {
      // ルートレベル: ワークスペース内の _why.json を探してファイルノードを返す
      return this._getFileNodes();
    }

    if (element.kind === "file" && element.filePath) {
      // ファイルノードの子: 各関数エントリ
      return this._getEntryNodes(element.filePath);
    }

    return [];
  }

  private _getFileNodes(): WhyTreeItem[] {
    const items: WhyTreeItem[] = [];
    const base = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
    for (const root of findWhyRoots()) {
      for (const rel of Object.keys(_loadWhyRoot(root)).sort()) {
        const srcPath = path.join(root, rel);
        items.push(new WhyTreeItem(
          path.relative(base, srcPath) || rel,
          "file",
          vscode.TreeItemCollapsibleState.Collapsed,
          srcPath,
        ));
      }
    }
    return items;
  }

  private _getEntryNodes(sourceFilePath: string): WhyTreeItem[] {
    return _loadWhyEntries(sourceFilePath).map((e) => {
      const firstLine = e.why.split("\n")[0].trim();
      const label     = firstLine.length > 50 ? firstLine.slice(0, 50) + "…" : firstLine;
      return new WhyTreeItem(
        `[${e.qualname}] ${label}`,
        "entry",
        vscode.TreeItemCollapsibleState.None,
        sourceFilePath,
        e.why,
        e.qualname,
      );
    });
  }
}

// ---------------------------------------------------------------------------
// Code Action プロバイダー（波線ホバー時のボタン）
// ---------------------------------------------------------------------------
class AiAuditCodeActionProvider implements vscode.CodeActionProvider {
  static readonly providedKinds = [vscode.CodeActionKind.QuickFix];

  provideCodeActions(
    _document: vscode.TextDocument,
    _range: vscode.Range,
    context: vscode.CodeActionContext
  ): vscode.CodeAction[] {
    // ai_audit の診断のみ対象
    const aiDiagnostics = context.diagnostics.filter(
      (d) => d.source === "ai_audit"
    );
    if (aiDiagnostics.length === 0) { return []; }

    const actions: vscode.CodeAction[] = [];

    for (const diag of aiDiagnostics) {
      // Copilot Chat へ送るボタン
      const copilotAction = new vscode.CodeAction(
        "$(copilot) Copilot Chat に修正依頼",
        vscode.CodeActionKind.QuickFix
      );
      copilotAction.command = {
        command: "aiAudit.sendToCopilotChat",
        title: "Copilot Chat に修正依頼",
        arguments: [diag],
      };
      copilotAction.diagnostics = [diag];
      actions.push(copilotAction);

      // クリップボードへコピーするボタン
      const clipboardAction = new vscode.CodeAction(
        "$(clippy) クリップボードにコピー（AI修正依頼用）",
        vscode.CodeActionKind.QuickFix
      );
      clipboardAction.command = {
        command: "aiAudit.copyToClipboard",
        title: "クリップボードにコピー",
        arguments: [diag],
      };
      clipboardAction.diagnostics = [diag];
      actions.push(clipboardAction);
    }

    return actions;
  }
}
