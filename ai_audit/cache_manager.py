"""
キャッシュマネージャー: SQLiteによるチャンクハッシュ管理と監査結果の永続化

DBの保存先: AI_AUDIT_DATA_DIR 環境変数（デフォルト: ~/.ai_audit/cache.db）
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_db_path() -> str:
    data_dir_raw = os.getenv("AI_AUDIT_DATA_DIR", "~/.ai_audit")
    data_dir = Path(data_dir_raw).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "cache.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    SQLiteスキーマを初期化する（冪等）。
    アプリケーション起動時に一度呼び出す。
    """
    conn = _connect()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunk_cache (
                chunk_id TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                last_audited_at DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL,
                wear_type TEXT NOT NULL,
                severity TEXT,
                description TEXT,
                suggestion TEXT,
                status TEXT DEFAULT 'open',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
    conn.close()


def compute_hash(code: str) -> str:
    """コード文字列のSHA-256ハッシュを返す。"""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def get_chunk_hash(chunk_id: str) -> str | None:
    """
    キャッシュからチャンクのハッシュを取得する。
    存在しない場合は None を返す。
    """
    conn = _connect()
    row = conn.execute(
        "SELECT hash FROM chunk_cache WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()
    conn.close()
    return row["hash"] if row else None


def update_chunk_hash(chunk_id: str, hash_value: str) -> None:
    """チャンクのハッシュをUPSERTする。"""
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO chunk_cache (chunk_id, hash, last_audited_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                hash = excluded.hash,
                last_audited_at = excluded.last_audited_at
            """,
            (chunk_id, hash_value, now),
        )
    conn.close()


def save_audit_result(chunk_id: str, wear_type: str, issues: list[dict]) -> None:
    """
    監査結果をDBに保存する（既存の同じwear_typeの結果は削除してから挿入）。

    Args:
        chunk_id:  チャンクの識別子
        wear_type: 使用したウェア名（例: "security", "readability"）
        issues:    LLMから返されたissuesリスト
    """
    conn = _connect()
    with conn:
        # 既存の同じ(chunk_id, wear_type)の結果を削除
        conn.execute(
            "DELETE FROM audit_results WHERE chunk_id = ? AND wear_type = ?",
            (chunk_id, wear_type),
        )
        for issue in issues:
            conn.execute(
                """
                INSERT INTO audit_results
                    (chunk_id, wear_type, severity, description, suggestion, status)
                VALUES (?, ?, ?, ?, ?, 'open')
                """,
                (
                    chunk_id,
                    wear_type,
                    issue.get("severity"),
                    issue.get("description"),
                    issue.get("suggestion"),
                ),
            )
    conn.close()


def get_audit_results(chunk_id: str) -> list[dict]:
    """
    チャンクの全監査結果を返す。

    Args:
        chunk_id: チャンクの識別子

    Returns:
        監査結果の辞書リスト
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM audit_results WHERE chunk_id = ? ORDER BY wear_type, severity",
        (chunk_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
