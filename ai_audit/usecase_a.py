"""
ユースケースA: バックグラウンド多重マイクロ監査

ファイルを関数・クラス単位にチャンク化し、
セキュリティ・クリーンコードの2種のウェアでLLMに監査させる。
結果はSQLiteに保存し、<ファイル名>_audit.json として出力する。

単一ファイル: audit_file()
フォルダ一括: audit_directory()
"""
import json
import os
import sys

from .ast_parser import parse_chunks, scan_python_files
from .cache_manager import (
    compute_hash,
    get_audit_results,
    get_chunk_hash,
    init_db,
    save_audit_result,
    update_chunk_hash,
)
from .llm_client import call_llm, parse_json_response
from .token_counter import truncate_to_limit
from .wear_manager import get_wear

# ユースケースAで使用するウェアのリスト
AUDIT_WEARS = ["security", "readability"]


def audit_file(file_path: str, force: bool = False) -> dict:
    """
    指定ファイルを多重マイクロ監査する。

    処理フロー:
      1. ASTパーサーでチャンク抽出
      2. 各チャンクのSHA-256を計算し、キャッシュと比較
      3. 変更ありチャンクに対して複数ウェアでLLM監査
      4. 結果をSQLiteに保存
      5. 全結果を audit.json として返す

    Args:
        file_path: 監査対象のPythonファイルパス
        force:     True の場合はキャッシュを無視して再監査

    Returns:
        監査結果の辞書。キーはchunk_id、値はissuesリスト。
    """
    init_db()
    abs_path = os.path.abspath(file_path)

    if not os.path.isfile(abs_path):
        print(f"[ERROR] ファイルが見つかりません: {abs_path}", file=sys.stderr)
        return {}

    print(f"[INFO] 監査開始: {abs_path}")
    chunks = parse_chunks(abs_path)

    if not chunks:
        print("[INFO] 解析可能な関数・クラスが見つかりませんでした。", file=sys.stderr)
        return {}

    print(f"[INFO] {len(chunks)} チャンクを抽出しました。")

    all_results: dict[str, list] = {}

    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        code = chunk["code"]
        current_hash = compute_hash(code)

        cached_hash = get_chunk_hash(chunk_id) if not force else None
        if cached_hash == current_hash:
            print(f"  [SKIP] {chunk['name']} (キャッシュヒット)")
            all_results[chunk_id] = get_audit_results(chunk_id)
            continue

        print(f"  [AUDIT] {chunk['type']}: {chunk['name']}")
        truncated_code = truncate_to_limit(code)
        chunk_issues: list[dict] = []

        for wear_type in AUDIT_WEARS:
            system_prompt = get_wear(wear_type)
            user_content = f"以下のPythonコードを監査してください:\n\n```python\n{truncated_code}\n```"

            try:
                raw_response = call_llm(system_prompt, user_content, json_mode=True)
                parsed = parse_json_response(raw_response)
                issues = parsed.get("issues", [])
                save_audit_result(chunk_id, wear_type, issues)
                chunk_issues.extend(issues)
                print(f"    [{wear_type}] {len(issues)} 件の指摘")
            except RuntimeError as e:
                print(f"    [ERROR] {wear_type} 監査失敗: {e}", file=sys.stderr)

        update_chunk_hash(chunk_id, current_hash)
        all_results[chunk_id] = chunk_issues

    return all_results


def audit_directory(
    directory: str,
    force: bool = False,
    output_dir: str | None = None,
) -> dict[str, dict]:
    """
    指定ディレクトリ配下の全Pythonファイルを一括で多重マイクロ監査する。

    各ファイルに対して audit_file() を呼び出し、
    ファイルごとの監査結果JSONを出力する。

    処理完了後、ディレクトリ全体のサマリーJSONも出力する:
      <output_dir>/_summary_audit.json

    Args:
        directory:  監査対象ディレクトリ（再帰スキャン）
        force:      True の場合はキャッシュを無視して再監査
        output_dir: 結果JSONの出力先ディレクトリ。
                    None の場合は各ファイルと同じディレクトリに出力。

    Returns:
        {ファイルパス: audit_file()の戻り値} の辞書
    """
    abs_dir = os.path.abspath(directory)
    if not os.path.isdir(abs_dir):
        print(f"[ERROR] ディレクトリが見つかりません: {abs_dir}", file=sys.stderr)
        return {}

    py_files = list(scan_python_files(abs_dir))
    if not py_files:
        print("[INFO] 対象のPythonファイルが見つかりませんでした。", file=sys.stderr)
        return {}

    if output_dir:
        os.makedirs(os.path.abspath(output_dir), exist_ok=True)

    print(f"[INFO] フォルダ一括監査開始: {abs_dir}")
    print(f"[INFO] 対象ファイル数: {len(py_files)}")
    print("=" * 60)

    all_file_results: dict[str, dict] = {}
    total_issues = 0
    skipped_files = 0

    for i, file_path in enumerate(py_files, 1):
        rel_path = os.path.relpath(file_path, abs_dir)
        print(f"\n[{i}/{len(py_files)}] {rel_path}")

        results = audit_file(file_path, force=force)

        if not results:
            skipped_files += 1
            continue

        all_file_results[file_path] = results
        file_issue_count = sum(len(v) for v in results.values())
        total_issues += file_issue_count

        # ファイルごとのJSONを出力
        if output_dir:
            # output_dir 指定時: ディレクトリ構造を保持してそこへ出力
            rel_no_ext = os.path.splitext(rel_path)[0]
            out_path = os.path.join(os.path.abspath(output_dir), f"{rel_no_ext}_audit.json")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _write_audit_json(file_path, results, out_path)
        else:
            save_audit_json(file_path, results)

    # サマリーJSON
    summary = _build_summary(abs_dir, all_file_results, len(py_files), skipped_files)
    summary_path = os.path.join(
        os.path.abspath(output_dir) if output_dir else abs_dir,
        "_summary_audit.json",
    )
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"[INFO] 一括監査完了")
    print(f"       対象: {len(py_files)} ファイル / スキップ: {skipped_files} ファイル")
    print(f"       合計指摘件数: {total_issues} 件")
    print(f"[INFO] サマリーを保存しました: {summary_path}")

    return all_file_results


def _build_summary(
    directory: str,
    all_results: dict[str, dict],
    total_files: int,
    skipped_files: int,
) -> dict:
    """フォルダ監査のサマリー辞書を構築する。"""
    files_summary = []
    for file_path, results in all_results.items():
        issue_count = sum(len(v) for v in results.values())
        high_count = sum(
            1 for issues in results.values()
            for issue in issues
            if issue.get("severity") == "high"
        )
        files_summary.append({
            "file": os.path.relpath(file_path, directory),
            "total_issues": issue_count,
            "high_severity": high_count,
        })

    # 指摘件数の多い順にソート
    files_summary.sort(key=lambda x: x["total_issues"], reverse=True)

    return {
        "directory": directory,
        "total_files": total_files,
        "audited_files": len(all_results),
        "skipped_files": skipped_files,
        "total_issues": sum(f["total_issues"] for f in files_summary),
        "high_severity_total": sum(f["high_severity"] for f in files_summary),
        "files": files_summary,
    }


def save_audit_json(file_path: str, results: dict) -> str:
    """
    監査結果をJSONファイルに保存する（元ファイルと同じディレクトリへ出力）。

    Args:
        file_path: 元の監査対象ファイルパス
        results:   audit_file() の戻り値

    Returns:
        保存したJSONファイルのパス
    """
    base = os.path.splitext(os.path.abspath(file_path))[0]
    output_path = f"{base}_audit.json"
    _write_audit_json(file_path, results, output_path)
    return output_path


def _write_audit_json(file_path: str, results: dict, output_path: str) -> None:
    """監査結果を指定パスのJSONファイルに書き出す。"""
    output = {
        "source_file": os.path.abspath(file_path),
        "chunks": [
            {
                "chunk_id": chunk_id,
                "issues": issues,
            }
            for chunk_id, issues in results.items()
        ],
        "total_issues": sum(len(v) for v in results.values()),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 監査結果を保存しました: {output_path}")
