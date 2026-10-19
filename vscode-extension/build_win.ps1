# =============================================================================
# ai_audit Windows用 VSIXビルドスクリプト
#
# 前提: Python 3.10 以上、Node.js / npm
# 実行: cd vscode-extension; .\build_win.ps1
# 初回のみ: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# バックエンドのビルド手順は build_backend.py（全 OS 共通）に集約している。
# =============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "[1/3] Building backend..."
python build_backend.py win
if ($LASTEXITCODE -ne 0) { Write-Error "backend build failed"; exit 1 }

Write-Host "[2/3] Compiling TypeScript..."
npm run compile
if ($LASTEXITCODE -ne 0) { Write-Error "TypeScript compile failed"; exit 1 }

Write-Host "[3/3] Packaging VSIX..."
$PkgVersion = node -p "require('./package.json').version"
$OutFile = "ai-audit-win-$PkgVersion.vsix"
npx vsce package --no-dependencies --out $OutFile
if ($LASTEXITCODE -ne 0) { Write-Error "VSIX packaging failed"; exit 1 }
Write-Host "Done! Distribute: $OutFile"
