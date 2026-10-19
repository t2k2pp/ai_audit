#!/bin/bash
# =============================================================================
# ai_audit macOS用 VSIXビルドスクリプト
#
# 前提: Python 3.10 以上、Node.js / npm
# 実行: cd vscode-extension && ./build_mac.sh
# バックエンドのビルド手順は build_backend.py（全 OS 共通）に集約している。
# =============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/3] Building backend..."
python3 build_backend.py mac

echo "[2/3] Compiling TypeScript..."
npm run compile

echo "[3/3] Packaging VSIX..."
PKG_VERSION=$(node -p "require('./package.json').version")
npx vsce package --no-dependencies --out "ai-audit-mac-${PKG_VERSION}.vsix"
echo "Done! Distribute: ai-audit-mac-${PKG_VERSION}.vsix"
