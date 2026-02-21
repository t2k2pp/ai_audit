"""
設定マネージャー: すべての設定を .env で一元管理する

【設計方針】
  設定は .env ファイル一箇所に集約する。config.json は使用しない。

  .env に置く理由:
    - Git管理外（.gitignore 済み）なので秘密情報を安全に保持できる
    - 初回セットアップ時に必ず設定させることで、環境に合わない値のまま
      使い続けるリスクを防ぐ
    - 設定が一箇所に集まるため見落としが減る

【必須設定項目】（未設定の場合は起動時にエラーを出して停止する）
  LLM_API_BASE_URL  : LLM API の URL（例: http://192.168.1.40:11434/v1）
  LLM_MODEL_NAME    : 使用するモデル名（例: gpt-oss:120b）

【任意設定項目（省略可）】
  LLM_API_KEY       : API キー（Ollama など不要な環境では未設定でよい）

【任意設定項目】
  LLM_MAX_OUTPUT_TOKENS : 最大出力トークン数（未設定=モデルのデフォルト最大値を使用）

【config コマンドの動作】
  `python main.py config model`         → .env の LLM_MODEL_NAME を書き換える
  `python main.py config output-tokens` → .env の LLM_MAX_OUTPUT_TOKENS を書き換える
"""
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# 必須環境変数の定義
# LLM_API_KEY は任意（Ollama など API キー不要な環境では未設定でよい）
_REQUIRED_VARS = [
    ("LLM_API_BASE_URL", "LLM API の URL（例: http://192.168.1.40:11434/v1）"),
    ("LLM_MODEL_NAME",   "使用するモデル名（例: gpt-oss:120b）"),
]


def _get_env_path() -> Path:
    """プロジェクトの .env ファイルパスを返す。"""
    # main.py と同じ階層の .env を探す
    candidates = [
        Path(os.getcwd()) / ".env",
        Path(__file__).parent.parent / ".env",
    ]
    for p in candidates:
        if p.exists():
            return p
    # 存在しない場合は カレントディレクトリ/.env を返す（エラーメッセージ用）
    return candidates[0]


def validate_env() -> None:
    """
    必須環境変数が設定されているか検証する。
    未設定の項目があればエラーメッセージを出力して sys.exit(1) する。
    """
    import sys
    missing = [
        (var, desc) for var, desc in _REQUIRED_VARS if not os.getenv(var, "").strip()
    ]
    if not missing:
        return

    env_path = _get_env_path()
    print("[ERROR] 以下の必須設定が .env に設定されていません:\n", file=sys.stderr)
    for var, desc in missing:
        print(f"  {var}\n    → {desc}\n", file=sys.stderr)
    print(
        f"  .env ファイルの場所: {env_path}\n"
        f"  .env.example を参考に設定してください。\n"
        f"  例: cp .env.example .env  （その後 .env を編集）",
        file=sys.stderr,
    )
    sys.exit(1)


def load_config() -> dict:
    """
    .env から全設定を読み込んで返す。
    必須項目の未設定チェックは呼び出し元（main.py）で validate_env() を使うこと。

    Returns:
        api_base_url:      LLM API の URL
        api_key:           API キー
        model_name:        使用するモデル名
        max_output_tokens: 最大出力トークン数（None=モデルのデフォルト最大値）
    """
    _raw_max = os.getenv("LLM_MAX_OUTPUT_TOKENS", "").strip()
    max_output_tokens: int | None = int(_raw_max) if _raw_max else None

    return {
        "api_base_url":      os.getenv("LLM_API_BASE_URL", ""),
        "api_key":           os.getenv("LLM_API_KEY", ""),
        "model_name":        os.getenv("LLM_MODEL_NAME", ""),
        "max_output_tokens": max_output_tokens,
    }


def update_env_var(key: str, value: str | None) -> None:
    """
    .env ファイルの指定キーの値を書き換える。
    キーが存在しない場合はファイル末尾に追記する。
    value が None の場合はキーをコメントアウトする。

    Args:
        key:   環境変数名（例: LLM_MODEL_NAME）
        value: 設定する値。None の場合はコメントアウト（未設定扱いにする）
    """
    env_path = _get_env_path()

    if not env_path.exists():
        raise FileNotFoundError(
            f".env ファイルが見つかりません: {env_path}\n"
            "cp .env.example .env を実行して .env を作成してください。"
        )

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    # 既存行を検索（コメント行も含めて key= の行を探す）
    pattern_active  = re.compile(rf"^{re.escape(key)}\s*=.*$")
    pattern_comment = re.compile(rf"^#\s*{re.escape(key)}\s*=.*$")

    new_line = f"# {key}=\n" if value is None else f"{key}={value}\n"

    replaced = False
    for i, line in enumerate(lines):
        if pattern_active.match(line.rstrip()) or pattern_comment.match(line.rstrip()):
            lines[i] = new_line
            replaced = True
            break

    if not replaced:
        # 末尾に追記
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(new_line)

    env_path.write_text("".join(lines), encoding="utf-8")


def fetch_ollama_models(base_url: str | None = None) -> list[dict]:
    """
    Ollama の /api/tags エンドポイントからモデル一覧を取得する。

    Args:
        base_url: Ollama の OpenAI 互換ベースURL（例: http://192.168.1.40:11434/v1）
                  None の場合は .env から読む。

    Returns:
        モデル情報の辞書リスト。各辞書:
          - name: str           モデル名（例: gpt-oss:120b）
          - parameter_size: str （例: 116.8B）
          - quantization: str   （例: MXFP4）
          - size_gb: float      ファイルサイズ (GB)
    """
    if base_url is None:
        base_url = os.getenv("LLM_API_BASE_URL", "")

    if not base_url:
        raise RuntimeError(
            ".env の LLM_API_BASE_URL が設定されていないため、"
            "モデル一覧を取得できません。"
        )

    # /v1 を除いて Ollama ネイティブ API エンドポイントを作る
    ollama_base = base_url.rstrip("/")
    if ollama_base.endswith("/v1"):
        ollama_base = ollama_base[:-3]

    url = f"{ollama_base}/api/tags"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        models_raw = resp.json().get("models", [])
    except requests.RequestException as e:
        raise RuntimeError(
            f"Ollama モデル一覧の取得に失敗しました: {e}\n"
            f"URL: {url}\n"
            f"接続先（LLM_API_BASE_URL）が正しいか .env を確認してください。"
        )

    models = []
    for m in models_raw:
        details = m.get("details", {})
        models.append({
            "name":           m["name"],
            "parameter_size": details.get("parameter_size", "?"),
            "quantization":   details.get("quantization_level", "?"),
            "size_gb":        round(m.get("size", 0) / 1_073_741_824, 1),
        })
    return models


def print_model_list(models: list[dict]) -> None:
    """モデル一覧をインデックス付きで表示する。"""
    current = os.getenv("LLM_MODEL_NAME", "")

    print(f"\n{'No':>3}  {'モデル名':<35} {'パラメータ':>10}  {'量子化':<10} {'サイズ':>7}")
    print("-" * 75)
    for i, m in enumerate(models):
        marker = " << 使用中" if m["name"] == current else ""
        print(
            f"{i:>3}  {m['name']:<35} {m['parameter_size']:>10}  "
            f"{m['quantization']:<10} {m['size_gb']:>5.1f} GB{marker}"
        )
    print()
