"""
ユースケースD: ASTスケルトン解析による設計書リバースビルド

既存ソースコード（Python / JS / TS）から設計書を2段階で自動生成する。

1. 詳細設計書（_design_detail.md）: スケルトンコード → LLM → 内部設計書
2. 概要設計書（_design_overview.md）: 詳細設計書 → LLM → 外部設計書

再開機能:
  - --force なし: _design_detail.md が存在すれば詳細生成をスキップし概要生成のみ実行
  - --force あり: 両ファイルを上書き（モデル変更・全体やり直し時）

JS/TS 固有観点の制限付記:
  - JS/TSファイルが含まれる場合、出力の冒頭に自動で付記を挿入する
"""
import os
import sys
from datetime import datetime

from .ast_parser import generate_skeleton, get_lang, scan_source_files
from .llm_client import call_llm
from .token_counter import DEFAULT_CHAR_LIMIT, truncate_to_limit
from .wear_manager import get_wear

# JS/TS固有観点の制限付記
_JSTS_NOTICE = """\
> ⚠️ **JS/TS固有の観点について**
> このドキュメントはPython向けウェアで生成されています。
> 型の安全性、async/awaitの誤用、React Hooksルール等のJS/TS固有の観点については
> 現在のウェアでは専門的な解析を行っていません。参考としてご活用ください。

"""

_DETAIL_FILENAME = "_design_detail.md"
_OVERVIEW_FILENAME = "_design_overview.md"


def generate_design_doc(
    directory: str,
    output_dir: str | None = None,
    force: bool = False,
) -> tuple[str, str]:
    """
    指定ディレクトリのソースコードから設計書を生成する。

    Args:
        directory:  対象ソースディレクトリ
        output_dir: 出力先ディレクトリ（None の場合は directory 直下に出力）
        force:      True の場合は既存ファイルを無視して全体を再生成

    Returns:
        (detail_path, overview_path) 生成された設計書ファイルのパス
    """
    abs_dir = os.path.abspath(directory)
    out_dir = os.path.abspath(output_dir) if output_dir else abs_dir
    os.makedirs(out_dir, exist_ok=True)

    detail_path = os.path.join(out_dir, _DETAIL_FILENAME)
    overview_path = os.path.join(out_dir, _OVERVIEW_FILENAME)

    # 詳細設計書の生成（再開判定）
    if not force and os.path.exists(detail_path):
        print(f"[INFO] 詳細設計書が既に存在します: {detail_path}")
        print("[INFO] --force なしのため詳細生成をスキップし、概要生成フェーズから再開します。")
    else:
        _generate_detail(abs_dir, detail_path)

    # 概要設計書の生成
    _generate_overview(detail_path, overview_path)

    return detail_path, overview_path


def _generate_detail(abs_dir: str, detail_path: str) -> None:
    """スケルトンコードから詳細設計書を生成して _design_detail.md に書き込む。"""
    wear_prompt = get_wear("detail_designer")

    # スケルトンコードを収集
    skeletons: list[str] = []
    has_jsts = False

    for file_path in scan_source_files(abs_dir):
        skeleton = generate_skeleton(file_path)
        if not skeleton:
            continue
        lang = get_lang(file_path)
        if lang in ("javascript", "typescript"):
            has_jsts = True
        rel_path = os.path.relpath(file_path, abs_dir)
        skeletons.append(f"=== File: {rel_path} ===\n{skeleton}")

    if not skeletons:
        print("[INFO] 解析可能なソースファイルが見つかりませんでした。", file=sys.stderr)
        # 空ファイルを書いてクラッシュを防ぐ
        with open(detail_path, "w", encoding="utf-8") as f:
            f.write("# 詳細設計書\n\n対象のソースファイルが見つかりませんでした。\n")
        return

    # トークン制限内でバッチ分割
    batches = _split_batches(skeletons)
    print(f"[INFO] スケルトン生成完了: {len(skeletons)} ファイル, {len(batches)} バッチで処理")

    # 各バッチをLLMに送信
    sections: list[str] = []
    files_per_batch = max(1, len(skeletons) // len(batches))
    for i, batch in enumerate(batches, 1):
        start_file = (i - 1) * files_per_batch + 1
        end_file = min(i * files_per_batch, len(skeletons))
        if i == len(batches):
            end_file = len(skeletons)
        print(f"  [DESIGN] バッチ {i}/{len(batches)} を送信中 (ファイル {start_file}〜{end_file})...")

        user_content = f"以下のスケルトンコードから内部設計書を生成してください:\n\n{batch}"
        user_content = truncate_to_limit(user_content, DEFAULT_CHAR_LIMIT)

        try:
            result = call_llm(wear_prompt, user_content, json_mode=False)
            if len(batches) > 1:
                sections.append(f"## ファイル {start_file}〜{end_file} の詳細設計\n\n{result}")
            else:
                sections.append(result)
        except RuntimeError as e:
            print(f"  [ERROR] バッチ {i} の推論失敗: {e}", file=sys.stderr)
            sections.append(f"*バッチ {i} の生成でエラーが発生しました: {e}*")

    # レポート組み立て
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# 詳細設計書（内部設計）\n\n"
        f"**対象ディレクトリ:** `{abs_dir}`  \n"
        f"**生成日時:** {timestamp}  \n"
        f"**対象ファイル数:** {len(skeletons)}\n\n"
        f"---\n\n"
    )

    notice = _JSTS_NOTICE if has_jsts else ""
    body = "\n\n---\n\n".join(sections)
    report = header + notice + body

    with open(detail_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[INFO] 詳細設計書を保存しました: {detail_path}")


def _generate_overview(detail_path: str, overview_path: str) -> None:
    """詳細設計書から概要設計書を生成して _design_overview.md に書き込む。"""
    wear_prompt = get_wear("overview_designer")

    if not os.path.exists(detail_path):
        print(f"[ERROR] 詳細設計書が見つかりません: {detail_path}", file=sys.stderr)
        return

    with open(detail_path, "r", encoding="utf-8") as f:
        detail_content = f.read()

    print(f"[INFO] 概要設計書を生成中 (詳細設計書: {len(detail_content)} 文字)...")

    # 詳細設計書が大きい場合はトークン制限に合わせて切り詰め
    user_content = f"以下の詳細設計書から外部（概要）設計書を生成してください:\n\n{detail_content}"
    user_content = truncate_to_limit(user_content, DEFAULT_CHAR_LIMIT)

    # JS/TS付記が詳細設計書に含まれていれば概要設計書にも引き継ぐ
    has_jsts_notice = _JSTS_NOTICE.strip()[:30] in detail_content

    try:
        result = call_llm(wear_prompt, user_content, json_mode=False)
    except RuntimeError as e:
        print(f"[ERROR] 概要設計書の生成失敗: {e}", file=sys.stderr)
        result = f"*概要設計書の生成でエラーが発生しました: {e}*"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# 概要設計書（外部設計）\n\n"
        f"**生成日時:** {timestamp}  \n"
        f"**参照:** `{os.path.basename(detail_path)}`\n\n"
        f"---\n\n"
    )
    notice = _JSTS_NOTICE if has_jsts_notice else ""
    report = header + notice + result

    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[INFO] 概要設計書を保存しました: {overview_path}")


def _split_batches(skeletons: list[str]) -> list[str]:
    """スケルトンリストをトークン制限内でバッチに分割する。"""
    batches: list[str] = []
    current_batch: list[str] = []
    current_length = 0

    for skeleton_block in skeletons:
        block_len = len(skeleton_block)
        if current_length + block_len > DEFAULT_CHAR_LIMIT and current_batch:
            batches.append("\n\n".join(current_batch))
            current_batch = [skeleton_block]
            current_length = block_len
        else:
            current_batch.append(skeleton_block)
            current_length += block_len

    if current_batch:
        batches.append("\n\n".join(current_batch))

    return batches
