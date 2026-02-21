#!/bin/bash
# =============================================================================
# ai_audit macOS用 VSIXビルドスクリプト
#
# 前提:
#   - Python 3.10以上がインストール済み（brew install python 等）
#   - Node.js / npm がインストール済み
#   - このスクリプトは vscode-extension/ フォルダで実行する
#
# 実行方法:
#   cd vscode-extension
#   chmod +x build_mac.sh
#   ./build_mac.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BIN_OUT="$SCRIPT_DIR/bin/mac"
VENV_DIR="$SCRIPT_DIR/build_tmp/venv_mac"

echo "[1/6] Creating isolated venv for build..."
python3 -m venv "$VENV_DIR"

VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

echo "[2/6] Installing dependencies into venv..."
"$VENV_PIP" install --quiet requests python-dotenv pyinstaller

echo "[3/6] Building with PyInstaller (isolated)..."
cd "$PROJECT_ROOT"
"$VENV_PY" -m PyInstaller \
    --onefile \
    --name main \
    --distpath "$BIN_OUT" \
    --workpath "$SCRIPT_DIR/build_tmp/mac" \
    --specpath "$SCRIPT_DIR/build_tmp" \
    --noconfirm \
    main.py

echo "[4/6] Checking output..."
if [ ! -f "$BIN_OUT/main" ]; then
    echo "ERROR: main binary not found"
    exit 1
fi
echo "  OK: $BIN_OUT/main [$(du -sh "$BIN_OUT/main" | cut -f1)]"

echo "[5/6] Compiling TypeScript..."
cd "$SCRIPT_DIR"
npm run compile

echo "[6/6] Packaging VSIX..."
PKG_VERSION=$(node -p "require('./package.json').version")
npx vsce package --no-dependencies --out "ai-audit-mac-${PKG_VERSION}.vsix"

echo ""
echo "Done! Distribute: ai-audit-mac-${PKG_VERSION}.vsix"
