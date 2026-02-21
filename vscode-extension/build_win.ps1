# =============================================================================
# ai_audit Windows用 VSIXビルドスクリプト
#
# 前提:
#   - Python 3.10以上がインストール済み
#   - Node.js / npm がインストール済み
#   - このスクリプトは vscode-extension/ フォルダで実行する
#
# 実行方法:
#   cd vscode-extension
#   .\build_win.ps1
#
# 初回のみ実行ポリシーの変更が必要な場合:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BinOut     = Join-Path $ScriptDir "bin\win"
$VenvDir    = Join-Path $ScriptDir "build_tmp\venv_win"

# build_tmp が残っていたらクリーン
if (Test-Path (Join-Path $ScriptDir "build_tmp\win")) {
    Write-Host "[0/6] Cleaning previous build_tmp\win..."
    Remove-Item -Recurse -Force (Join-Path $ScriptDir "build_tmp\win")
}

Write-Host "[1/6] Creating isolated venv for build..."
python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed"; exit 1 }

$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

Write-Host "[2/6] Installing dependencies into venv..."
& $VenvPip install --quiet requests python-dotenv pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

Write-Host "[3/6] Building with PyInstaller (isolated)..."
Set-Location $ProjectRoot
& $VenvPy -m PyInstaller `
    --onefile `
    --name main `
    --distpath $BinOut `
    --workpath (Join-Path $ScriptDir "build_tmp\win") `
    --specpath (Join-Path $ScriptDir "build_tmp") `
    --noconfirm `
    main.py
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller build failed"; exit 1 }

Write-Host "[4/6] Checking output..."
$ExePath = Join-Path $BinOut "main.exe"
if (-not (Test-Path $ExePath)) { Write-Error "main.exe not found"; exit 1 }
$SizeMB = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
Write-Host "  OK: $ExePath [$SizeMB MB]"

Write-Host "[5/6] Compiling TypeScript..."
Set-Location $ScriptDir
npm run compile
if ($LASTEXITCODE -ne 0) { Write-Error "TypeScript compile failed"; exit 1 }

Write-Host "[6/6] Packaging VSIX..."
$PkgVersion = node -p "require('./package.json').version"
$OutFile = "ai-audit-win-$PkgVersion.vsix"
npx vsce package --no-dependencies --out $OutFile
if ($LASTEXITCODE -ne 0) { Write-Error "VSIX packaging failed"; exit 1 }

Write-Host ""
Write-Host "Done! Distribute: $OutFile"
