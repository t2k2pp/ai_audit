"""
ユースケースB: 設計思想のリバースエンジニアリングと蓄積

extract_why: ディレクトリ内の全関数から「なぜそのように書かれたか（Why）」を
             AIに推測させ、ChromaDBに蓄積する。

search_why:  自然言語クエリでベクトル検索し、関連する設計思想を返す。
"""
import os
import sys
from datetime import datetime, timezone

from .ast_parser import parse_chunks, scan_source_files
from .llm_client import call_llm
from .token_counter import truncate_to_limit
from .wear_manager import get_wear

# ChromaDBのコレクション名
_COLLECTION_NAME = "architecture_decisions"


def _get_chroma_client():
    """ChromaDBクライアントと対象コレクションを返す。"""
    try:
        import chromadb
    except ImportError:
        print("[ERROR] chromadb がインストールされていません。`pip install chromadb` を実行してください。", file=sys.stderr)
        sys.exit(1)

    data_dir = os.path.expanduser(os.getenv("AI_AUDIT_DATA_DIR", "~/.ai_audit"))
    chroma_path = os.path.join(data_dir, "chroma_data")
    os.makedirs(chroma_path, exist_ok=True)

    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def extract_why(directory: str) -> None:
    """
    指定ディレクトリ内の全Pythonファイルから設計思想を抽出し、
    ChromaDBに保存する（バッチ実行）。

    Args:
        directory: スキャン対象ディレクトリ
    """
    collection = _get_chroma_client()
    wear_prompt = get_wear("why_extractor")
    processed = 0
    skipped = 0

    for file_path in scan_source_files(directory):
        chunks = parse_chunks(file_path)
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            code = chunk["code"]
            truncated_code = truncate_to_limit(code)

            # 既存エントリの確認（冪等性）
            existing = collection.get(ids=[chunk_id])
            if existing["ids"]:
                skipped += 1
                continue

            user_content = f"以下のPythonコードの設計思想を分析してください:\n\n```python\n{truncated_code}\n```"

            try:
                why_text = call_llm(wear_prompt, user_content, json_mode=False)
                print(f"  [EXTRACT] {chunk['name']}: {why_text[:60]}...")

                collection.add(
                    ids=[chunk_id],
                    documents=[why_text],
                    metadatas=[{
                        "file_path": os.path.relpath(file_path, directory),
                        "function_name": chunk["name"],
                        "chunk_type": chunk["type"],
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                    }],
                )
                processed += 1

            except RuntimeError as e:
                print(f"  [ERROR] {chunk['name']}: {e}", file=sys.stderr)

    print(f"\n[INFO] 完了: {processed} 件を抽出・保存, {skipped} 件はスキップ（既存）")


def search_why(query: str, top_k: int = 5) -> list[dict]:
    """
    自然言語クエリで設計思想を検索する（コサイン類似度ベース）。

    Args:
        query:  検索クエリ（自然言語）
        top_k:  返す結果の最大件数（デフォルト: 5）

    Returns:
        検索結果の辞書リスト。各辞書:
          - rank: 順位
          - file_path: ファイルパス
          - function_name: 関数名
          - why_text: 設計思想テキスト
          - distance: コサイン距離（小さいほど類似）
    """
    collection = _get_chroma_client()

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    output = []
    for i, (doc_id, document, metadata, distance) in enumerate(zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )):
        output.append({
            "rank": i + 1,
            "file_path": metadata.get("file_path", ""),
            "function_name": metadata.get("function_name", ""),
            "chunk_type": metadata.get("chunk_type", ""),
            "why_text": document,
            "distance": round(distance, 4),
        })

    return output


def list_why() -> list[dict]:
    """
    ChromaDB に蓄積されている全エントリを返す。

    Returns:
        エントリの辞書リスト。各辞書:
          - index: 順位（1始まり）
          - file_path: ファイルパス
          - function_name: 関数名
          - chunk_type: "function" または "class"
          - why_text: 設計思想テキスト
          - extracted_at: 抽出日時（ISO 8601）
    """
    collection = _get_chroma_client()
    total = collection.count()
    if total == 0:
        return []

    results = collection.get(
        include=["documents", "metadatas"],
        limit=total,
    )

    if not results["ids"]:
        return []

    output = []
    for i, (doc_id, document, metadata) in enumerate(zip(
        results["ids"],
        results["documents"],
        results["metadatas"],
    )):
        output.append({
            "index": i + 1,
            "file_path": metadata.get("file_path", ""),
            "function_name": metadata.get("function_name", ""),
            "chunk_type": metadata.get("chunk_type", ""),
            "why_text": document,
            "extracted_at": metadata.get("extracted_at", ""),
        })

    # ファイルパス → 関数名 の順にソート
    output.sort(key=lambda x: (x["file_path"], x["function_name"]))
    for i, item in enumerate(output):
        item["index"] = i + 1

    return output


def print_list_results(results: list[dict]) -> None:
    """list_why の結果をターミナルに見やすく表示する。"""
    if not results:
        print("蓄積された設計思想がありません。まず extract_why を実行してください。")
        return

    print(f"\n[INFO] 蓄積済み設計思想: {len(results)} 件")
    for item in results:
        print(f"\n--- #{item['index']} [{item['chunk_type']}] {item['function_name']} ---")
        print(f"ファイル   : {item['file_path']}")
        print(f"抽出日時   : {item['extracted_at']}")
        print(f"設計思想   :\n{item['why_text']}")


def print_search_results(results: list[dict]) -> None:
    """検索結果をターミナルに見やすく表示する。"""
    if not results:
        print("関連する設計思想が見つかりませんでした。")
        return

    for item in results:
        print(f"\n--- #{item['rank']} [{item['chunk_type']}] {item['function_name']} ---")
        print(f"ファイル : {item['file_path']}")
        print(f"類似度   : {1 - item['distance']:.2%} (distance={item['distance']})")
        print(f"設計思想 :\n{item['why_text']}")
