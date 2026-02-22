"""
ユースケースC: ASTスケルトン解析によるマクロ・アーキテクチャレビュー

4096トークン制限内でAIに複数ファイルの全体像を把握させるため、
実装ブロックを除去したスケルトンコードを生成し、アーキテクチャを評価させる。

対応言語: Python / JavaScript / TypeScript
※ JS/TSファイルが含まれる場合、JS/TS固有の観点は含まれない旨を出力に付記する。
"""
import os
import sys
from datetime import datetime

from .ast_parser import generate_skeleton, get_lang, scan_python_files, scan_source_files
from .llm_client import call_llm
from .token_counter import DEFAULT_CHAR_LIMIT, is_within_limit, truncate_to_limit
from .wear_manager import get_wear

# JS/TS固有観点の制限付記
_JSTS_NOTICE = """\
> ⚠️ **JS/TS固有の観点について**
> このレビューはPython向けウェアで生成されています。
> 型の安全性、async/awaitの誤用、React Hooksルール等のJS/TS固有の観点については
> 現在のウェアでは専門的な解析を行っていません。参考としてご活用ください。

"""


def review_architecture(directory: str, output_file: str | None = None) -> str:
    """
    指定ディレクトリ内の全Pythonファイルのスケルトンを生成し、
    アーキテクチャレビューをLLMに依頼する。

    4096トークン制限を超える場合はファイルを分割して複数回推論し、
    結果を結合してMarkdownレポートとして返す。

    Args:
        directory:   対象ディレクトリ
        output_file: 結果を保存するファイルパス（None の場合は保存しない）

    Returns:
        アーキテクチャレビューのMarkdown文字列
    """
    abs_dir = os.path.abspath(directory)
    wear_prompt = get_wear("architecture_reviewer")

    # スケルトンコードを収集（Python / JS / TS）
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
        return "# アーキテクチャレビュー\n\n対象のソースファイルが見つかりませんでした。"

    # トークン制限内でファイルをバッチにまとめる
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

    print(f"[INFO] スケルトン生成完了: {len(skeletons)} ファイル, {len(batches)} バッチで処理")

    # 各バッチをLLMに送信
    reviews: list[str] = []
    for i, batch in enumerate(batches, 1):
        print(f"  [REVIEW] バッチ {i}/{len(batches)} を送信中...")
        user_content = f"以下のスケルトンコードのアーキテクチャをレビューしてください:\n\n{batch}"
        user_content = truncate_to_limit(user_content, DEFAULT_CHAR_LIMIT)

        try:
            result = call_llm(wear_prompt, user_content, json_mode=False)
            reviews.append(result)
        except RuntimeError as e:
            print(f"  [ERROR] バッチ {i} の推論失敗: {e}", file=sys.stderr)
            reviews.append(f"*バッチ {i} のレビューでエラーが発生しました: {e}*")

    # 結果を結合してMarkdownレポートを作成
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# アーキテクチャレビューレポート\n\n"
        f"**対象ディレクトリ:** `{abs_dir}`  \n"
        f"**生成日時:** {timestamp}  \n"
        f"**対象ファイル数:** {len(skeletons)}\n\n"
        f"---\n\n"
    )
    notice = _JSTS_NOTICE if has_jsts else ""

    if len(reviews) == 1:
        report = header + notice + reviews[0]
    else:
        # 複数バッチに分割された場合は、対象ファイル範囲を見出しに付ける
        files_per_batch = max(1, len(skeletons) // len(reviews))
        sections = []
        for i, r in enumerate(reviews):
            start_file = i * files_per_batch + 1
            end_file = min((i + 1) * files_per_batch, len(skeletons))
            if i == len(reviews) - 1:
                end_file = len(skeletons)
            sections.append(f"## ファイル {start_file}〜{end_file} のレビュー\n\n{r}")
        report = header + notice + "\n\n---\n\n".join(sections)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[INFO] レポートを保存しました: {output_file}")

    return report
