"use strict";
/**
 * ai_audit VSCode 拡張機能 (Phase 1)
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const cp = __importStar(require("child_process"));
const fs = __importStar(require("fs"));
const http = __importStar(require("http"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
// ---------------------------------------------------------------------------
// サポート言語定義（将来の拡張に備えて一元管理）
// ---------------------------------------------------------------------------
const SUPPORTED_LANGUAGES = [
    { id: "python", label: "Python", status: "supported", since: "v0.1.0" },
    { id: "javascript", label: "JavaScript", status: "supported", since: "v0.3.0" },
    { id: "typescript", label: "TypeScript", status: "supported", since: "v0.3.0" },
    { id: "go", label: "Go", status: "planned", since: "-" },
    { id: "csharp", label: "C#", status: "planned", since: "-" },
];
// ---------------------------------------------------------------------------
// グローバル状態
// ---------------------------------------------------------------------------
let diagnosticCollection;
const runningAudits = new Set();
let statusBarItem;
let extensionPath;
// 設計思想 CodeLens + TreeView 用
let whyLensProvider;
let whyTreeProvider;
// ---------------------------------------------------------------------------
// 有効化エントリポイント
// ---------------------------------------------------------------------------
function activate(context) {
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
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument((doc) => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        if (!cfg.get("enableOnSave", true)) {
            return;
        }
        const supported = SUPPORTED_LANGUAGES.find((l) => l.id === doc.languageId && l.status === "supported");
        if (supported) {
            runAudit(doc.uri.fsPath, false);
        }
    }));
    // コマンド登録
    // エクスプローラー右クリックから呼ばれると uri 引数が渡される
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.auditCurrentFile", (uri) => {
        const filePath = uri?.fsPath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
        if (!filePath) {
            return;
        }
        const langId = uri
            ? (filePath.endsWith(".py") ? "python" : "")
            : (vscode.window.activeTextEditor?.document.languageId ?? "");
        const supported = SUPPORTED_LANGUAGES.find((l) => l.id === langId && l.status === "supported");
        if (!supported) {
            vscode.window.showWarningMessage(`ai_audit: このファイル形式はまだサポートされていません。` +
                `サポート言語: コマンドパレットから "ai_audit: サポート言語一覧を表示" で確認できます。`);
            return;
        }
        runAudit(filePath, false);
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.auditCurrentFileForce", (uri) => {
        const filePath = uri?.fsPath ?? vscode.window.activeTextEditor?.document.uri.fsPath;
        if (!filePath) {
            return;
        }
        runAudit(filePath, true);
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.auditFolder", async (uri) => {
        // エクスプローラー右クリック → uri あり、コマンドパレット → フォルダ選択ダイアログ
        let folderPath = uri?.fsPath;
        if (!folderPath) {
            const picked = await vscode.window.showOpenDialog({
                canSelectFiles: false,
                canSelectFolders: true,
                canSelectMany: false,
                openLabel: "このフォルダを一括監査する",
            });
            folderPath = picked?.[0]?.fsPath;
        }
        if (!folderPath) {
            return;
        }
        runAuditFolder(folderPath);
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.clearDiagnostics", () => {
        diagnosticCollection.clear();
        statusBarItem.text = "$(shield) ai_audit";
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.openSettings", () => {
        vscode.commands.executeCommand("workbench.action.openSettings", "aiAudit");
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.showSupportedLanguages", () => {
        showSupportedLanguages();
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.selectModel", () => {
        selectModel();
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.setupWhyFeature", async () => {
        await setupWhyFeature();
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.extractWhy", async (uri) => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        if (!cfg.get("enableWhyFeature", false)) {
            const action = await vscode.window.showInformationMessage("ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？", "セットアップする", "キャンセル");
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
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.searchWhy", async () => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        if (!cfg.get("enableWhyFeature", false)) {
            const action = await vscode.window.showInformationMessage("ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？", "セットアップする", "キャンセル");
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
        if (!query) {
            return;
        }
        runBackendCommand("search_why", [query], "searchWhy");
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.reviewArchitecture", (uri) => {
        // エクスプローラー右クリック → uri あり、コマンドパレット → ワークスペースルート
        const folderPath = uri?.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!folderPath) {
            vscode.window.showWarningMessage("ai_audit: ワークスペースを開いた状態で実行してください。");
            return;
        }
        // 解析対象フォルダ内に _architecture.md を出力させる（--output フラグで指定）
        const outputMd = path.join(folderPath, "_architecture.md");
        runBackendCommand("review_architecture", [folderPath, "--output", outputMd], "reviewArchitecture");
    }));
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.generateDesignDoc", async (uri) => {
        // エクスプローラー右クリック → uri あり、コマンドパレット → フォルダ選択ダイアログ
        let folderPath = uri?.fsPath;
        if (!folderPath) {
            const picked = await vscode.window.showOpenDialog({
                canSelectFiles: false,
                canSelectFolders: true,
                canSelectMany: false,
                openLabel: "このフォルダの設計書を生成する",
            });
            folderPath = picked?.[0]?.fsPath;
        }
        if (!folderPath) {
            return;
        }
        runBackendCommand("generate_design_doc", [folderPath], "generateDesignDoc");
    }));
    // Code Action: 指摘をCopilot Chatへ追記
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.sendToCopilotChat", async (diagnostic) => {
        const text = buildPromptFromDiagnostic(diagnostic);
        // Copilot Chat が利用可能なら chat パネルへ書き込む
        const copilotAvailable = vscode.extensions.getExtension("GitHub.copilot-chat") !== undefined;
        if (copilotAvailable) {
            await vscode.commands.executeCommand("workbench.panel.chat.view.copilot.focus");
            await vscode.commands.executeCommand("workbench.action.chat.sendToNewChat", { inputValue: text });
        }
        else {
            // Copilot 未インストールの場合はクリップボードへ
            const current = await vscode.env.clipboard.readText();
            const appended = current.endsWith("\n") || current === ""
                ? current + text
                : current + "\n" + text;
            await vscode.env.clipboard.writeText(appended + "\n");
            vscode.window.showInformationMessage("ai_audit: GitHub Copilot Chat が見つからないためクリップボードにコピーしました。");
        }
    }));
    // Code Action: 指摘をクリップボードへ追記
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.copyToClipboard", async (diagnostic) => {
        const text = buildPromptFromDiagnostic(diagnostic);
        const current = await vscode.env.clipboard.readText();
        const appended = current.endsWith("\n") || current === ""
            ? current + text
            : current + "\n" + text;
        await vscode.env.clipboard.writeText(appended + "\n");
        vscode.window.showInformationMessage("ai_audit: クリップボードに追記しました。AIチャットに貼り付けてください。");
    }));
    // Code Action プロバイダー登録（波線ホバー時にボタンを表示）
    const supportedLanguageIds = SUPPORTED_LANGUAGES
        .filter((l) => l.status === "supported")
        .map((l) => ({ language: l.id }));
    context.subscriptions.push(vscode.languages.registerCodeActionsProvider(supportedLanguageIds, new AiAuditCodeActionProvider(), { providedCodeActionKinds: AiAuditCodeActionProvider.providedKinds }));
    // ---------------------------------------------------------------------------
    // 設計思想 CodeLens プロバイダー登録
    // ---------------------------------------------------------------------------
    whyLensProvider = new AiAuditWhyLensProvider();
    context.subscriptions.push(vscode.languages.registerCodeLensProvider(supportedLanguageIds, whyLensProvider));
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
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.listWhy", async () => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        if (!cfg.get("enableWhyFeature", false)) {
            const action = await vscode.window.showInformationMessage("ai_audit: 設計思想機能はまだ有効になっていません。セットアップを実行しますか？", "セットアップする", "キャンセル");
            if (action === "セットアップする") {
                await setupWhyFeature();
            }
            return;
        }
        runBackendCommand("list_why", [], "listWhy");
    }));
    // ---------------------------------------------------------------------------
    // CodeLens ON/OFF 切り替えコマンド
    // ---------------------------------------------------------------------------
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.toggleWhyLens", async () => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        const current = cfg.get("showWhyLens", false);
        await cfg.update("showWhyLens", !current, vscode.ConfigurationTarget.Global);
        vscode.window.showInformationMessage(`ai_audit: 設計思想 CodeLens を${!current ? "ON" : "OFF"} にしました。`);
        whyLensProvider?.refresh();
    }));
    // ---------------------------------------------------------------------------
    // 監査波線 ON/OFF 切り替えコマンド
    // ---------------------------------------------------------------------------
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.toggleAuditDiagnostics", async () => {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        const current = cfg.get("showAuditDiagnostics", true);
        await cfg.update("showAuditDiagnostics", !current, vscode.ConfigurationTarget.Global);
        if (!current) {
            vscode.window.showInformationMessage("ai_audit: 監査波線表示を ON にしました。");
        }
        else {
            diagnosticCollection.clear();
            vscode.window.showInformationMessage("ai_audit: 監査波線表示を OFF にしました。");
        }
    }));
    // ---------------------------------------------------------------------------
    // TreeView 再読み込みコマンド
    // ---------------------------------------------------------------------------
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.refreshWhyView", () => {
        whyTreeProvider?.refresh();
    }));
    // ---------------------------------------------------------------------------
    // 設計思想詳細ポップアップ（CodeLens クリック / TreeView クリック）
    // ---------------------------------------------------------------------------
    context.subscriptions.push(vscode.commands.registerCommand("aiAudit.showWhyDetail", (whyText, funcName) => {
        showWebview(`💡 設計思想: ${funcName}`, `<style>
            body { font-family: var(--vscode-font-family); padding: 16px; line-height: 1.7; }
            h2 { color: var(--vscode-textLink-foreground); }
            pre { background: var(--vscode-textBlockQuote-background);
                  padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }
          </style>
          <h2>💡 ${escapeHtml(funcName)}</h2>
          <pre>${escapeHtml(whyText)}</pre>`);
    }));
    // 設定変更時に CodeLens を更新
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("aiAudit.showWhyLens")) {
            whyLensProvider?.refresh();
        }
    }));
}
function deactivate() {
    diagnosticCollection.clear();
}
// ---------------------------------------------------------------------------
// 起動時セットアップチェック
// ---------------------------------------------------------------------------
async function checkSetupOnStartup(_context) {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const apiUrl = cfg.get("apiBaseUrl", "").trim();
    const apiKey = cfg.get("apiKey", "").trim();
    const model = cfg.get("modelName", "").trim();
    const missing = [];
    if (!apiUrl) {
        missing.push("API URL (aiAudit.apiBaseUrl)");
    }
    // apiKey は任意（Ollama等APIキー不要な環境では空でよい）
    if (!model) {
        missing.push("モデル名 (aiAudit.modelName)");
    }
    // 同梱バイナリの存在チェック
    const binaryPath = resolveBackendBinary();
    const binaryOk = binaryPath ? fs.existsSync(binaryPath) : false;
    if (missing.length > 0 || !binaryOk) {
        const messages = [];
        if (!binaryOk) {
            messages.push(`お使いのOS（${process.platform}）に対応したバイナリが見つかりません。\n` +
                `正しいOS用の VSIX をインストールしてください。`);
        }
        if (missing.length > 0) {
            messages.push(`以下の必須設定が未入力です:\n  ・${missing.join("\n  ・")}`);
        }
        const action = await vscode.window.showWarningMessage(`ai_audit: セットアップが必要です。\n${messages.join("\n\n")}`, "設定画面を開く", "後で");
        if (action === "設定画面を開く") {
            vscode.commands.executeCommand("aiAudit.openSettings");
        }
    }
}
// ---------------------------------------------------------------------------
// モデル切り替え UI
// ---------------------------------------------------------------------------
async function selectModel() {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const apiUrl = cfg.get("apiBaseUrl", "").trim();
    if (!apiUrl) {
        const action = await vscode.window.showErrorMessage("ai_audit: API URL が設定されていません。先に設定画面で API URL を入力してください。", "設定画面を開く");
        if (action === "設定画面を開く") {
            vscode.commands.executeCommand("aiAudit.openSettings");
        }
        return;
    }
    // Ollama の /api/tags を呼ぶ
    statusBarItem.text = "$(sync~spin) ai_audit: モデル一覧を取得中...";
    let models;
    try {
        models = await fetchOllamaModels(apiUrl);
    }
    catch (e) {
        statusBarItem.text = "$(shield) ai_audit";
        vscode.window.showErrorMessage(`ai_audit: モデル一覧の取得に失敗しました。\n` +
            `接続先: ${apiUrl}\n` +
            `エラー: ${e}\n\n` +
            `設定画面の "API URL" が正しいか確認してください。`);
        return;
    }
    statusBarItem.text = "$(shield) ai_audit";
    const currentModel = cfg.get("modelName", "");
    const items = models.map((m) => ({
        label: m.name,
        description: m.size,
        detail: m.name === currentModel ? "← 現在使用中" : undefined,
    }));
    const selected = await vscode.window.showQuickPick(items, {
        title: "ai_audit: 使用するモデルを選択",
        placeHolder: "モデル名を選択してください",
        matchOnDescription: true,
    });
    if (!selected) {
        return;
    }
    await cfg.update("modelName", selected.label, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(`ai_audit: モデルを "${selected.label}" に変更しました。`);
}
function fetchOllamaModels(baseUrl) {
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
                    const models = (json.models ?? []).map((m) => ({
                        name: m.name,
                        size: m.size ? `${(m.size / 1073741824).toFixed(1)} GB` : "?",
                    }));
                    resolve(models);
                }
                catch (e) {
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
function showSupportedLanguages() {
    const rows = SUPPORTED_LANGUAGES.map((l) => {
        const status = l.status === "supported" ? "✅ サポート中" : "🔜 対応予定";
        return `<tr><td>${l.label}</td><td>${status}</td><td>${l.since}</td></tr>`;
    }).join("");
    const panel = vscode.window.createWebviewPanel("aiAuditLanguages", "ai_audit: サポート言語", vscode.ViewColumn.Beside, {
        enableScripts: false,
        retainContextWhenHidden: false,
    });
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
function resolveBackendBinary() {
    const platform = process.platform; // "win32" | "darwin" | "linux"
    let subDir;
    let binName;
    if (platform === "win32") {
        subDir = "win";
        binName = "main.exe";
    }
    else if (platform === "darwin") {
        subDir = "mac";
        binName = "main";
    }
    else {
        subDir = "linux";
        binName = "main";
    }
    return path.join(extensionPath, "bin", subDir, binName);
}
// ---------------------------------------------------------------------------
// 監査実行
// ---------------------------------------------------------------------------
function runAudit(filePath, force) {
    if (runningAudits.has(filePath)) {
        return;
    }
    runningAudits.add(filePath);
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const apiUrl = cfg.get("apiBaseUrl", "").trim();
    const apiKey = cfg.get("apiKey", "").trim();
    const modelName = cfg.get("modelName", "").trim();
    const maxTokens = cfg.get("maxOutputTokens", null);
    // 必須設定チェック（apiKey は任意）
    const missing = [];
    if (!apiUrl) {
        missing.push("API URL");
    }
    if (!modelName) {
        missing.push("モデル名");
    }
    if (missing.length > 0) {
        vscode.window.showErrorMessage(`ai_audit: 設定が不足しています。コマンドパレットから "ai_audit: 接続設定を開く" を実行して設定してください。\n未入力: ${missing.join(", ")}`);
        runningAudits.delete(filePath);
        return;
    }
    // 拡張機能に同梱されたバイナリのパスを解決
    const binaryPath = resolveBackendBinary();
    if (!binaryPath || !fs.existsSync(binaryPath)) {
        vscode.window.showErrorMessage(`ai_audit: バイナリが見つかりません（${binaryPath}）。\n` +
            `お使いのOSに対応した VSIX を再インストールしてください。`);
        runningAudits.delete(filePath);
        return;
    }
    const args = ["audit", filePath];
    if (force) {
        args.push("--force");
    }
    // VSCode 設定を環境変数として渡す（.env が不要になる）
    const env = {
        ...process.env,
        PYTHONUTF8: "1", // Windows CP932 環境での文字化け防止
        LLM_API_BASE_URL: apiUrl,
        LLM_MODEL_NAME: modelName,
    };
    // apiKey は任意（空の場合は環境変数を設定しない）
    if (apiKey) {
        env["LLM_API_KEY"] = apiKey;
    }
    if (maxTokens !== null && maxTokens !== undefined) {
        env["LLM_MAX_OUTPUT_TOKENS"] = String(maxTokens);
    }
    const shortName = path.basename(filePath);
    statusBarItem.text = `$(sync~spin) ai_audit: ${shortName} を監査中...`;
    const proc = cp.spawn(binaryPath, args, {
        cwd: path.dirname(binaryPath),
        env,
    });
    const stderrChunks = [];
    proc.stderr.on("data", (data) => { stderrChunks.push(data); });
    proc.on("close", (code) => {
        runningAudits.delete(filePath);
        statusBarItem.text = "$(shield) ai_audit";
        if (code !== 0) {
            const stderr = decodeBuffer(stderrChunks);
            vscode.window.showErrorMessage(`ai_audit エラー: ${stderr.slice(0, 300)}`);
            return;
        }
        const auditJsonPath = filePath.replace(/\.py$/, "_audit.json");
        if (!fs.existsSync(auditJsonPath)) {
            diagnosticCollection.set(vscode.Uri.file(filePath), []);
            return;
        }
        try {
            const raw = fs.readFileSync(auditJsonPath, "utf-8");
            const auditResult = JSON.parse(raw);
            applyDiagnostics(filePath, auditResult);
            const total = auditResult.total_issues ?? 0;
            statusBarItem.text = total > 0
                ? `$(warning) ai_audit: ${total} 件の指摘`
                : "$(pass) ai_audit: 問題なし";
        }
        catch (e) {
            vscode.window.showErrorMessage(`ai_audit: 結果の読み込みに失敗しました: ${e}`);
        }
    });
}
// ---------------------------------------------------------------------------
// フォルダ一括監査
// ---------------------------------------------------------------------------
function runAuditFolder(folderPath) {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const apiUrl = cfg.get("apiBaseUrl", "").trim();
    const apiKey = cfg.get("apiKey", "").trim();
    const modelName = cfg.get("modelName", "").trim();
    const maxTokens = cfg.get("maxOutputTokens", null);
    const missing = [];
    if (!apiUrl) {
        missing.push("API URL");
    }
    if (!modelName) {
        missing.push("モデル名");
    }
    if (missing.length > 0) {
        vscode.window.showErrorMessage(`ai_audit: 設定が不足しています。\n未入力: ${missing.join(", ")}`);
        return;
    }
    const binaryPath = resolveBackendBinary();
    if (!binaryPath || !fs.existsSync(binaryPath)) {
        vscode.window.showErrorMessage(`ai_audit: バイナリが見つかりません（${binaryPath}）。\n` +
            `お使いのOSに対応した VSIX を再インストールしてください。`);
        return;
    }
    const env = {
        ...process.env,
        PYTHONUTF8: "1", // Windows CP932 環境での文字化け防止
        LLM_API_BASE_URL: apiUrl,
        LLM_MODEL_NAME: modelName,
    };
    if (apiKey) {
        env["LLM_API_KEY"] = apiKey;
    }
    if (maxTokens !== null && maxTokens !== undefined) {
        env["LLM_MAX_OUTPUT_TOKENS"] = String(maxTokens);
    }
    const shortName = path.basename(folderPath);
    statusBarItem.text = `$(sync~spin) ai_audit: ${shortName}/ を一括監査中...`;
    const proc = cp.spawn(binaryPath, ["audit", folderPath], {
        cwd: path.dirname(binaryPath),
        env,
    });
    const stderrChunks = [];
    proc.stderr.on("data", (data) => { stderrChunks.push(data); });
    proc.on("close", (code) => {
        statusBarItem.text = "$(shield) ai_audit";
        if (code !== 0) {
            const stderr = decodeBuffer(stderrChunks);
            vscode.window.showErrorMessage(`ai_audit エラー: ${stderr.slice(0, 300)}`);
            return;
        }
        // フォルダ以下の _audit.json をすべて探して Diagnostics に反映する
        let totalIssues = 0;
        let fileCount = 0;
        const applyAll = (dir) => {
            let entries;
            try {
                entries = fs.readdirSync(dir);
            }
            catch {
                return;
            }
            for (const entry of entries) {
                const fullPath = path.join(dir, entry);
                try {
                    const stat = fs.statSync(fullPath);
                    if (stat.isDirectory()) {
                        applyAll(fullPath);
                    }
                    else if (entry.endsWith("_audit.json")) {
                        const pyFile = fullPath.replace(/_audit\.json$/, ".py");
                        try {
                            const raw = fs.readFileSync(fullPath, "utf-8");
                            const auditResult = JSON.parse(raw);
                            applyDiagnostics(pyFile, auditResult);
                            totalIssues += auditResult.total_issues ?? 0;
                            fileCount++;
                        }
                        catch { /* 読み込み失敗はスキップ */ }
                    }
                }
                catch { /* stat 失敗はスキップ */ }
            }
        };
        applyAll(folderPath);
        statusBarItem.text = totalIssues > 0
            ? `$(warning) ai_audit: ${totalIssues} 件の指摘`
            : "$(pass) ai_audit: 問題なし";
        vscode.window.showInformationMessage(`ai_audit: ${shortName}/ の一括監査が完了しました。${fileCount} ファイル / ${totalIssues} 件の指摘`);
    });
}
// ---------------------------------------------------------------------------
// Diagnostics 変換
// ---------------------------------------------------------------------------
function severityToDiagnosticSeverity(severity) {
    switch (severity?.toLowerCase()) {
        case "high": return vscode.DiagnosticSeverity.Error;
        case "medium": return vscode.DiagnosticSeverity.Warning;
        default: return vscode.DiagnosticSeverity.Information;
    }
}
function applyDiagnostics(filePath, auditResult) {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    // 監査波線表示が OFF の場合は何もしない
    if (!cfg.get("showAuditDiagnostics", true)) {
        diagnosticCollection.set(vscode.Uri.file(filePath), []);
        return;
    }
    const showInfo = cfg.get("showInformationDiagnostics", false);
    const diagnostics = [];
    let fileLines = [];
    try {
        fileLines = fs.readFileSync(filePath, "utf-8").split("\n");
    }
    catch { /* フォールバック */ }
    for (const chunk of auditResult.chunks ?? []) {
        const funcName = chunk.chunk_id.split(":").pop() ?? "";
        let chunkStartLine = 0;
        const defPattern = new RegExp(`^\\s*(def|class)\\s+${escapeRegex(funcName)}\\s*[:(]`);
        for (let i = 0; i < fileLines.length; i++) {
            if (defPattern.test(fileLines[i])) {
                chunkStartLine = i;
                break;
            }
        }
        for (const issue of chunk.issues ?? []) {
            const diagSeverity = severityToDiagnosticSeverity(issue.severity);
            if (!showInfo && diagSeverity === vscode.DiagnosticSeverity.Information) {
                continue;
            }
            const targetLine = chunkStartLine + (issue.line_number_offset ?? 0);
            const lineText = fileLines[targetLine] ?? "";
            const range = new vscode.Range(targetLine, 0, targetLine, lineText.length || 1);
            const diag = new vscode.Diagnostic(range, `[ai_audit/${issue.type}] ${issue.description}`, diagSeverity);
            diag.source = "ai_audit";
            if (issue.suggestion) {
                diag.relatedInformation = [
                    new vscode.DiagnosticRelatedInformation(new vscode.Location(vscode.Uri.file(filePath), range), `修正提案: ${issue.suggestion}`),
                ];
            }
            diagnostics.push(diag);
        }
    }
    diagnosticCollection.set(vscode.Uri.file(filePath), diagnostics);
}
function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
/**
 * Buffer 配列を結合し、UTF-8 → CP932（Shift-JIS）の順にデコードを試みる。
 * Windows の Python バイナリは CP932 で stderr を出力することがある。
 */
function decodeBuffer(chunks) {
    const buf = Buffer.concat(chunks);
    // まず UTF-8 として解釈（文字化け判定: replacement character が含まれないか）
    const utf8 = buf.toString("utf-8");
    if (!utf8.includes("\uFFFD")) {
        return utf8;
    }
    // UTF-8 で文字化けしている場合は CP932（Shift-JIS）でデコード
    try {
        return new TextDecoder("shift_jis").decode(buf);
    }
    catch {
        return utf8; // TextDecoder が失敗したら UTF-8 フォールバック
    }
}
const BACKEND_COMMAND_LABELS = {
    extractWhy: "設計思想を抽出中",
    searchWhy: "設計思想を検索中",
    listWhy: "設計思想を読み込み中",
    reviewArchitecture: "アーキテクチャを解析中",
    generateDesignDoc: "設計書を生成中",
};
const BACKEND_COMMAND_TITLES = {
    extractWhy: "ai_audit: 設計思想抽出",
    searchWhy: "ai_audit: 設計思想検索",
    listWhy: "ai_audit: 設計思想一覧",
    reviewArchitecture: "ai_audit: アーキテクチャ解析",
    generateDesignDoc: "ai_audit: 設計書生成",
};
function runBackendCommand(subCommand, args, commandId) {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const apiUrl = cfg.get("apiBaseUrl", "").trim();
    const apiKey = cfg.get("apiKey", "").trim();
    const modelName = cfg.get("modelName", "").trim();
    const maxTokens = cfg.get("maxOutputTokens", null);
    const missing = [];
    if (!apiUrl) {
        missing.push("API URL");
    }
    if (!modelName) {
        missing.push("モデル名");
    }
    if (missing.length > 0) {
        vscode.window.showErrorMessage(`ai_audit: 設定が不足しています。\n未入力: ${missing.join(", ")}`);
        return;
    }
    const env = {
        ...process.env,
        PYTHONUTF8: "1", // Windows CP932 環境での文字化け防止
        LLM_API_BASE_URL: apiUrl,
        LLM_MODEL_NAME: modelName,
    };
    if (apiKey) {
        env["LLM_API_KEY"] = apiKey;
    }
    if (maxTokens) {
        env["LLM_MAX_OUTPUT_TOKENS"] = String(maxTokens);
    }
    const label = BACKEND_COMMAND_LABELS[commandId];
    statusBarItem.text = `$(sync~spin) ai_audit: ${label}...`;
    // extractWhy / searchWhy は chromadb が必要なため、
    // バイナリ（PyInstaller）ではなく利用者環境の Python + 同梱 main.py で実行する
    let spawnCmd;
    let spawnArgs;
    let spawnCwd;
    // extractWhy/searchWhy/listWhy のみ Python 直接実行（chromadb が必要なため）
    // reviewArchitecture はバイナリで実行
    const needsPython = commandId === "extractWhy" || commandId === "searchWhy" || commandId === "listWhy";
    if (needsPython) {
        const pythonPath = cfg.get("pythonPath", "python").trim();
        const mainPyPath = path.join(extensionPath, "python", "main.py");
        spawnCmd = pythonPath;
        spawnArgs = [mainPyPath, subCommand, ...args];
        spawnCwd = path.join(extensionPath, "python");
    }
    else {
        const binaryPath = resolveBackendBinary();
        if (!binaryPath || !fs.existsSync(binaryPath)) {
            vscode.window.showErrorMessage(`ai_audit: バイナリが見つかりません（${binaryPath}）。\n` +
                `お使いのOSに対応した VSIX を再インストールしてください。`);
            return;
        }
        spawnCmd = binaryPath;
        spawnArgs = [subCommand, ...args];
        spawnCwd = path.dirname(binaryPath);
    }
    const proc = cp.spawn(spawnCmd, spawnArgs, {
        cwd: spawnCwd,
        env,
        shell: needsPython, // Python はシェル経由で起動（PATH解決のため）
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    proc.stdout?.on("data", (data) => { stdoutChunks.push(data); });
    proc.stderr.on("data", (data) => { stderrChunks.push(data); });
    proc.on("error", (err) => {
        statusBarItem.text = "$(shield) ai_audit";
        if (needsPython) {
            const pythonPath = cfg.get("pythonPath", "python").trim();
            vscode.window.showErrorMessage(`ai_audit: Python の起動に失敗しました。\n` +
                `パス: "${pythonPath}"\n` +
                `エラー: ${err.message}\n\n` +
                `設定画面の "Python パス" を確認してください。`);
        }
        else {
            vscode.window.showErrorMessage(`ai_audit: 起動エラー: ${err.message}`);
        }
    });
    proc.on("close", (code) => {
        statusBarItem.text = "$(shield) ai_audit";
        const stdout = decodeBuffer(stdoutChunks);
        const stderr = decodeBuffer(stderrChunks);
        if (code !== 0) {
            vscode.window.showErrorMessage(`ai_audit エラー: ${stderr.slice(0, 300)}`);
            return;
        }
        // 出力ファイルを読み込んで Webview に表示
        const title = BACKEND_COMMAND_TITLES[commandId];
        if (commandId === "extractWhy") {
            const jsonPath = args[0].replace(/\.py$/, "_why.json");
            showJsonResultInWebview(title, jsonPath);
            // TreeView も更新
            whyTreeProvider?.refresh();
            whyLensProvider?.refresh();
        }
        else if (commandId === "searchWhy") {
            // search-why は stdout に結果を出力する
            showTextResultInWebview(title, stdout || stderr);
        }
        else if (commandId === "listWhy") {
            // list_why は stdout に結果を出力する
            showTextResultInWebview(title, stdout || stderr);
        }
        else if (commandId === "reviewArchitecture") {
            // review-architecture は --output で指定したパスにファイルを書く
            // args = [folderPath, "--output", outputMdPath]
            const mdPath = args[2] ?? path.join(args[0], "_architecture.md");
            showMarkdownResultInWebview(title, mdPath);
        }
        else if (commandId === "generateDesignDoc") {
            // generate_design_doc は args[0] のフォルダ直下に両ファイルを書く
            const folderPath = args[0];
            const detailPath = path.join(folderPath, "_design_detail.md");
            const overviewPath = path.join(folderPath, "_design_overview.md");
            showMarkdownResultInWebview("ai_audit: 詳細設計書", detailPath);
            showMarkdownResultInWebview("ai_audit: 概要設計書", overviewPath);
        }
    });
}
// ---------------------------------------------------------------------------
// Webview 表示ヘルパー
// ---------------------------------------------------------------------------
function showJsonResultInWebview(title, jsonPath) {
    if (!fs.existsSync(jsonPath)) {
        vscode.window.showWarningMessage(`ai_audit: 結果ファイルが見つかりません（${jsonPath}）`);
        return;
    }
    let data = [];
    try {
        data = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
    }
    catch {
        vscode.window.showErrorMessage(`ai_audit: 結果ファイルの読み込みに失敗しました（${jsonPath}）`);
        return;
    }
    const rows = data.map((item) => `
    <div class="card">
      <div class="chunk-id">${escapeHtml(item.chunk_id)}</div>
      <div class="why">${escapeHtml(item.why ?? "").replace(/\n/g, "<br>")}</div>
    </div>
  `).join("");
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
function showTextResultInWebview(title, text) {
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
function showMarkdownResultInWebview(title, mdPath) {
    if (!fs.existsSync(mdPath)) {
        vscode.window.showWarningMessage(`ai_audit: 結果ファイルが見つかりません（${mdPath}）`);
        return;
    }
    const md = fs.readFileSync(mdPath, "utf-8");
    // Markdown をシンプルな HTML に変換（見出し・コードブロック・箇条書きのみ）
    const html = md
        .replace(/^### (.+)$/gm, "<h3>$1</h3>")
        .replace(/^## (.+)$/gm, "<h2>$1</h2>")
        .replace(/^# (.+)$/gm, "<h1>$1</h1>")
        .replace(/```[\s\S]*?```/g, (m) => `<pre><code>${escapeHtml(m.slice(3, -3).replace(/^\w*\n/, ""))}</code></pre>`)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/^\* (.+)$/gm, "<li>$1</li>")
        .replace(/^- (.+)$/gm, "<li>$1</li>")
        .replace(/\n\n/g, "</p><p>")
        .replace(/^(?!<[hlipc])(.+)$/gm, "<p>$1</p>");
    showWebview(title, `
    <style>
      body { font-family: var(--vscode-font-family); padding: 16px; line-height: 1.6; }
      h1,h2,h3 { color: var(--vscode-textLink-foreground); border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 4px; }
      code { background: var(--vscode-textBlockQuote-background); padding: 2px 4px; border-radius: 3px; }
      pre  { background: var(--vscode-textBlockQuote-background); padding: 12px; border-radius: 4px; overflow-x: auto; }
      li   { margin-bottom: 4px; }
    </style>
    <h2>${escapeHtml(title)}</h2>
    ${html}
  `);
}
function showWebview(title, bodyHtml) {
    const panel = vscode.window.createWebviewPanel("aiAuditResult", title, vscode.ViewColumn.Beside, { enableScripts: false, retainContextWhenHidden: false });
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
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
// ---------------------------------------------------------------------------
// AI連携用プロンプト生成
// ---------------------------------------------------------------------------
function buildPromptFromDiagnostic(diagnostic) {
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
async function setupWhyFeature() {
    const cfg = vscode.workspace.getConfiguration("aiAudit");
    const currentPythonPath = cfg.get("pythonPath", "python");
    // Step 1: Python パスを確認・入力
    const pythonPath = await vscode.window.showInputBox({
        title: "ai_audit: 設計思想機能のセットアップ",
        prompt: "使用する Python のパスを確認してください。通常は変更不要です。",
        value: currentPythonPath,
        placeHolder: "python",
        validateInput: (value) => {
            if (!value.trim()) {
                return "Python のパスを入力してください。";
            }
            return null;
        },
    });
    if (!pythonPath) {
        return;
    } // キャンセル
    const pyCmd = pythonPath.trim();
    // Python が動作するかチェック
    statusBarItem.text = "$(sync~spin) ai_audit: Python を確認中...";
    const pythonOk = await runPythonCheck(pyCmd, ["--version"]);
    statusBarItem.text = "$(shield) ai_audit";
    if (!pythonOk) {
        const action = await vscode.window.showErrorMessage(`ai_audit: Python が見つかりませんでした。\nパス: "${pyCmd}"\n\n` +
            `Python がインストールされているか確認し、正しいパスを入力してください。`, "設定を変更する", "キャンセル");
        if (action === "設定を変更する") {
            vscode.commands.executeCommand("aiAudit.openSettings");
        }
        return;
    }
    // pythonPath 設定を保存
    await cfg.update("pythonPath", pyCmd, vscode.ConfigurationTarget.Global);
    // chromadb がすでにインストール済みか確認
    statusBarItem.text = "$(sync~spin) ai_audit: chromadb を確認中...";
    const chromaOk = await runPythonCheck(pyCmd, ["-c", "import chromadb"]);
    statusBarItem.text = "$(shield) ai_audit";
    if (chromaOk) {
        // すでにインストール済み → 即有効化
        await cfg.update("enableWhyFeature", true, vscode.ConfigurationTarget.Global);
        vscode.window.showInformationMessage("ai_audit: chromadb は既にインストール済みです。設計思想機能を有効にしました。");
        return;
    }
    // chromadb 未インストール → インストール確認
    const action = await vscode.window.showInformationMessage(`chromadb がインストールされていません。\n` +
        `インストールしますか？\n` +
        `（実行コマンド: ${pyCmd} -m pip install chromadb）`, "インストールする", "キャンセル");
    if (action !== "インストールする") {
        return;
    }
    // VSCode ターミナルでインストール実行
    const terminal = vscode.window.createTerminal("ai_audit: セットアップ");
    terminal.show(true);
    terminal.sendText(`${pyCmd} -m pip install chromadb`, true);
    // インストール完了後に「有効にする」ボタンで確定
    const done = await vscode.window.showInformationMessage(`インストールが完了したら「有効にする」を押してください。`, "有効にする", "キャンセル");
    if (done === "有効にする") {
        await cfg.update("enableWhyFeature", true, vscode.ConfigurationTarget.Global);
        vscode.window.showInformationMessage("ai_audit: 設計思想機能を有効にしました。");
    }
}
/** Python コマンドを shell 経由で実行し、終了コード 0 なら true を返す */
function runPythonCheck(pythonPath, args) {
    return new Promise((resolve) => {
        const proc = cp.spawn(pythonPath, args, { shell: true });
        proc.on("close", (code) => resolve(code === 0));
        proc.on("error", () => resolve(false));
        setTimeout(() => { try {
            proc.kill();
        }
        catch { /* ignore */ } resolve(false); }, 8000);
    });
}
// ---------------------------------------------------------------------------
// 設計思想 CodeLens プロバイダー
// 関数・クラスの定義行の上に「💡 設計思想: ...」を薄く表示する
// ---------------------------------------------------------------------------
/**
 * _why.json ファイルから設計思想エントリを読み込み、キャッシュする。
 * キャッシュキー: ファイルパス (without _why.json suffix)
 */
const _whyCache = new Map();
function _loadWhyCache(sourceFilePath) {
    const whyJsonPath = sourceFilePath.replace(/\.(py|js|jsx|ts|tsx|dart)$/, "_why.json");
    if (!fs.existsSync(whyJsonPath)) {
        return [];
    }
    const cached = _whyCache.get(sourceFilePath);
    if (cached) {
        return cached;
    }
    try {
        const data = JSON.parse(fs.readFileSync(whyJsonPath, "utf-8"));
        const entries = data.map((item) => ({
            name: item.chunk_id.split(":").pop() ?? "",
            why: item.why ?? "",
        }));
        _whyCache.set(sourceFilePath, entries);
        return entries;
    }
    catch {
        return [];
    }
}
class AiAuditWhyLensProvider {
    constructor() {
        this._onDidChangeCodeLenses = new vscode.EventEmitter();
        this.onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;
    }
    refresh() {
        _whyCache.clear();
        this._onDidChangeCodeLenses.fire();
    }
    provideCodeLenses(document) {
        const cfg = vscode.workspace.getConfiguration("aiAudit");
        if (!cfg.get("showWhyLens", false)) {
            return [];
        }
        if (!cfg.get("enableWhyFeature", false)) {
            return [];
        }
        const entries = _loadWhyCache(document.uri.fsPath);
        if (entries.length === 0) {
            return [];
        }
        const lenses = [];
        const fileLines = document.getText().split("\n");
        // 関数・クラス定義行を探す（Python: def/class, JS/TS: function/class/const ... =, Dart: class）
        const DEF_PATTERN = /^\s*(def|async\s+def|class|function\s+|export\s+(default\s+)?(function|class)|const\s+\w+\s*=\s*(async\s+)?\(|[A-Za-z_]\w*\s+[A-Za-z_]\w*\s*\()/;
        for (let lineIdx = 0; lineIdx < fileLines.length; lineIdx++) {
            const line = fileLines[lineIdx];
            const defMatch = DEF_PATTERN.exec(line);
            if (!defMatch) {
                continue;
            }
            // 行から関数/クラス名を抽出
            let nameMatch = null;
            // Python: def func_name / class ClassName
            nameMatch = line.match(/(?:def|class)\s+([A-Za-z_]\w*)/);
            if (!nameMatch) {
                // JS/TS: function funcName / class ClassName
                nameMatch = line.match(/(?:function|class)\s+([A-Za-z_]\w*)/);
            }
            if (!nameMatch) {
                // JS/TS: const funcName =
                nameMatch = line.match(/const\s+([A-Za-z_]\w*)\s*=/);
            }
            if (!nameMatch) {
                continue;
            }
            const funcName = nameMatch[1];
            const entry = entries.find((e) => e.name === funcName);
            if (!entry) {
                continue;
            }
            // 1行目を抽出（最大60文字）
            const firstLine = entry.why.split("\n")[0].trim();
            const snippet = firstLine.length > 60 ? firstLine.slice(0, 60) + "…" : firstLine;
            const range = new vscode.Range(lineIdx, 0, lineIdx, 0);
            const lens = new vscode.CodeLens(range, {
                title: `💡 設計思想: ${snippet}`,
                command: "aiAudit.showWhyDetail",
                arguments: [entry.why, funcName],
            });
            lenses.push(lens);
        }
        return lenses;
    }
}
// 設計思想詳細表示コマンド（CodeLens クリック時）は activate() 外でも登録できるよう遅延登録
// → activate() 内で登録済みなので不要だが、クラス外に定義して activate に入れる
// ---------------------------------------------------------------------------
// 設計思想 TreeView プロバイダー（サイドパネル一覧）
// ---------------------------------------------------------------------------
/** TreeView のノード: ファイルノード or 関数ノード */
class WhyTreeItem extends vscode.TreeItem {
    constructor(label, kind, collapsibleState, filePath, whyText, funcName) {
        super(label, collapsibleState);
        this.kind = kind;
        this.filePath = filePath;
        this.whyText = whyText;
        this.funcName = funcName;
        if (kind === "file") {
            this.iconPath = new vscode.ThemeIcon("file-code");
            this.contextValue = "whyFile";
        }
        else {
            this.iconPath = new vscode.ThemeIcon("lightbulb");
            this.contextValue = "whyEntry";
            this.tooltip = whyText;
            // クリックで詳細表示
            this.command = {
                command: "aiAudit.showWhyDetail",
                title: "設計思想を表示",
                arguments: [whyText ?? "", funcName ?? ""],
            };
        }
    }
}
class AiAuditWhyTreeProvider {
    constructor() {
        this._onDidChangeTreeData = new vscode.EventEmitter();
        this.onDidChangeTreeData = this._onDidChangeTreeData.event;
    }
    /** ワークスペース内の _why.json ファイルを再スキャンして TreeView を更新 */
    refresh() {
        _whyCache.clear();
        this._onDidChangeTreeData.fire();
    }
    getTreeItem(element) {
        return element;
    }
    async getChildren(element) {
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
    _getFileNodes() {
        const folders = vscode.workspace.workspaceFolders;
        if (!folders || folders.length === 0) {
            return [];
        }
        const items = [];
        for (const folder of folders) {
            this._scanWhyJsonFiles(folder.uri.fsPath, items);
        }
        return items;
    }
    _scanWhyJsonFiles(dir, items) {
        const SKIP_DIRS = new Set(["node_modules", ".git", "__pycache__", "build_tmp", "dist", ".venv", "venv"]);
        let entries;
        try {
            entries = fs.readdirSync(dir);
        }
        catch {
            return;
        }
        for (const entry of entries) {
            const fullPath = path.join(dir, entry);
            try {
                const stat = fs.statSync(fullPath);
                if (stat.isDirectory()) {
                    if (!SKIP_DIRS.has(entry)) {
                        this._scanWhyJsonFiles(fullPath, items);
                    }
                }
                else if (entry.endsWith("_why.json")) {
                    // 対応するソースファイルパスを推定
                    const srcPath = fullPath.replace(/_why\.json$/, "");
                    const label = path.relative(vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "", fullPath);
                    items.push(new WhyTreeItem(label, "file", vscode.TreeItemCollapsibleState.Collapsed, srcPath));
                }
            }
            catch { /* skip */ }
        }
    }
    _getEntryNodes(sourceFilePath) {
        const entries = _loadWhyCache(sourceFilePath);
        return entries.map((e) => {
            const firstLine = e.why.split("\n")[0].trim();
            const label = firstLine.length > 50 ? firstLine.slice(0, 50) + "…" : firstLine;
            return new WhyTreeItem(`[${e.name}] ${label}`, "entry", vscode.TreeItemCollapsibleState.None, sourceFilePath, e.why, e.name);
        });
    }
}
// ---------------------------------------------------------------------------
// Code Action プロバイダー（波線ホバー時のボタン）
// ---------------------------------------------------------------------------
class AiAuditCodeActionProvider {
    provideCodeActions(_document, _range, context) {
        // ai_audit の診断のみ対象
        const aiDiagnostics = context.diagnostics.filter((d) => d.source === "ai_audit");
        if (aiDiagnostics.length === 0) {
            return [];
        }
        const actions = [];
        for (const diag of aiDiagnostics) {
            // Copilot Chat へ送るボタン
            const copilotAction = new vscode.CodeAction("$(copilot) Copilot Chat に修正依頼", vscode.CodeActionKind.QuickFix);
            copilotAction.command = {
                command: "aiAudit.sendToCopilotChat",
                title: "Copilot Chat に修正依頼",
                arguments: [diag],
            };
            copilotAction.diagnostics = [diag];
            actions.push(copilotAction);
            // クリップボードへコピーするボタン
            const clipboardAction = new vscode.CodeAction("$(clippy) クリップボードにコピー（AI修正依頼用）", vscode.CodeActionKind.QuickFix);
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
AiAuditCodeActionProvider.providedKinds = [vscode.CodeActionKind.QuickFix];
