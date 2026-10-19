#!/usr/bin/env python3
"""
VSCode 拡張に同梱するバックエンドを作るビルド補助スクリプト（全 OS 共通）。

1. リポジトリルートの ai_audit/ と main.py を vscode-extension/python/ にコピーする
   （Python 直接実行モードで使用。ソースの正はルート側のみ）
2. 隔離 venv に requirements-core.txt と PyInstaller を入れる
3. PyInstaller で単一バイナリを bin/<os>/ に出力する

使い方: python build_backend.py <win|mac|linux>
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


def copy_sources() -> Path:
    dest = SCRIPT_DIR / "python"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    shutil.copytree(ROOT / "ai_audit", dest / "ai_audit",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("main.py", "requirements.txt", "requirements-core.txt"):
        shutil.copy2(ROOT / name, dest / name)
    return dest


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("win", "mac", "linux"):
        print(__doc__)
        return 2
    target = sys.argv[1]
    py_dir = copy_sources()
    print(f"[backend] sources copied to {py_dir}")

    venv_dir = SCRIPT_DIR / "build_tmp" / f"venv_{target}"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    venv_py = venv_dir / bin_dir / ("python.exe" if os.name == "nt" else "python")
    subprocess.check_call([str(venv_py), "-m", "pip", "install", "--quiet",
                           "-r", str(ROOT / "requirements-core.txt"), "pyinstaller"])

    work = SCRIPT_DIR / "build_tmp" / target
    if work.exists():
        shutil.rmtree(work)
    collect = []
    for pkg in ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript",
                "tree_sitter_language_pack"):
        collect += ["--collect-all", pkg]
    subprocess.check_call([
        str(venv_py), "-m", "PyInstaller", "--onefile", "--name", "main",
        "--distpath", str(SCRIPT_DIR / "bin" / target),
        "--workpath", str(work),
        "--specpath", str(SCRIPT_DIR / "build_tmp"),
        "--noconfirm", *collect, str(py_dir / "main.py"),
    ], cwd=py_dir)

    exe = SCRIPT_DIR / "bin" / target / ("main.exe" if target == "win" else "main")
    if not exe.exists():
        print(f"ERROR: {exe} not found", file=sys.stderr)
        return 1
    print(f"[backend] OK: {exe} ({exe.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
