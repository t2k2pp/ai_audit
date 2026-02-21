"""
トークン数管理: 文字数ベースでトークン数を推定し、4096トークン制限内を保証する

120Bモデルの4096トークン制限に対して、
日本語・英語混在テキストを安全に収めるため文字数ベースで管理する。

ハードリミット: 2000文字（≒4096トークンの安全マージン）
"""

# デフォルトの文字数ハードリミット（日本語・英語混在で約4096トークン相当の安全値）
DEFAULT_CHAR_LIMIT = 2000


def estimate_tokens(text: str) -> int:
    """
    テキストのトークン数を文字数ベースで推定する。

    推定ルール:
      - 日本語・中国語・韓国語等の全角文字: 1文字 ≈ 2トークン
      - ASCII文字（英語・記号）: 4文字 ≈ 1トークン（平均的な英単語長を考慮）

    Args:
        text: 推定対象のテキスト

    Returns:
        推定トークン数（整数）
    """
    ascii_count = sum(1 for c in text if ord(c) < 128)
    non_ascii_count = len(text) - ascii_count

    estimated = (ascii_count // 4) + (non_ascii_count * 2)
    return max(estimated, len(text) // 4)


def is_within_limit(text: str, limit: int = DEFAULT_CHAR_LIMIT) -> bool:
    """
    テキストが文字数制限内であるか確認する。

    Args:
        text: 確認対象のテキスト
        limit: 最大文字数（デフォルト: 2000）

    Returns:
        制限内であれば True
    """
    return len(text) <= limit


def truncate_to_limit(text: str, limit: int = DEFAULT_CHAR_LIMIT) -> str:
    """
    テキストを文字数制限内に収まるよう末尾を切り詰める。

    Args:
        text: 切り詰め対象のテキスト
        limit: 最大文字数（デフォルト: 2000）

    Returns:
        制限内に収まるテキスト（切り詰めた場合は末尾に注記を追加）
    """
    if len(text) <= limit:
        return text
    truncated = text[:limit - 50]
    return truncated + "\n... (トークン制限のため以降省略)"
