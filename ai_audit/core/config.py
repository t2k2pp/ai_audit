"""
設定: すべての設定を .env（または環境変数）で一元管理する

VSCode 拡張は設定値を環境変数として渡すため、.env がなくても動作する。

【必須】
  LLM_API_BASE_URL      LLM API の URL（例: http://192.168.1.40:11434/v1）
  LLM_MODEL_NAME        使用するモデル名
【任意】
  LLM_API_KEY           API キー（不要なサーバでは未設定でよい）
  LLM_MAX_OUTPUT_TOKENS 最大出力トークン数（未設定=ティアに応じた既定値）
  LLM_CONTEXT_LENGTH    最大コンテキスト長（未設定=サーバから自動取得）
  LLM_CONTEXT_BUDGET    1 リクエストの実効予算（未設定=自動算出）
  AI_AUDIT_TIER         compact / standard / large（未設定=自動判定）
  LLM_REASONING         off / low / medium / high（思考モード）
  LLM_EXTRA_BODY        リクエストに追加する JSON（サーバ固有パラメータ）
  LLM_PARALLEL          同時リクエスト数（既定 1）
  LLM_EMBEDDING_MODEL   埋め込みモデル名（設計思想検索用）
  LLM_SERVER_TYPE       auto / llamacpp / vllm / ollama / openai（既定 auto）
  LLM_TIMEOUT           1 リクエストのタイムアウト秒（既定 3600）
  AI_AUDIT_DATA_DIR     グローバルデータの保存先（既定 ~/.ai_audit）
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_REQUIRED_VARS = [
    ("LLM_API_BASE_URL", "LLM API の URL（例: http://192.168.1.40:11434/v1）"),
    ("LLM_MODEL_NAME",   "使用するモデル名（例: qwen3.8-flash-next）"),
]

_dotenv_loaded = False


def _env_candidates() -> list[Path]:
    return [Path(os.getcwd()) / ".env", _PROJECT_ROOT / ".env"]


def get_env_path() -> Path:
    """.env ファイルのパスを返す（存在しなければカレントディレクトリの .env）。"""
    for p in _env_candidates():
        if p.exists():
            return p
    return _env_candidates()[0]


def load_env() -> None:
    """.env を一度だけ読み込む（既存の環境変数は上書きしない）。"""
    global _dotenv_loaded
    if _dotenv_loaded or os.getenv("AI_AUDIT_NO_DOTENV"):
        return
    _dotenv_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = get_env_path()
    if path.exists():
        load_dotenv(path, override=False)


def _int_or_none(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} は整数で指定してください（現在値: {raw!r}）")


@dataclass
class Settings:
    api_base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    max_output_tokens: int | None = None
    context_length: int | None = None
    context_budget: int | None = None
    tier: str | None = None
    reasoning: str = "off"
    extra_body: dict = field(default_factory=dict)
    parallel: int = 1
    embedding_model: str = ""
    server_type: str = "auto"
    timeout: int = 3600
    data_dir: Path = field(default_factory=lambda: Path("~/.ai_audit").expanduser())


def load_settings() -> Settings:
    """環境変数（.env を含む）から設定を読み込む。"""
    load_env()
    extra_raw = os.getenv("LLM_EXTRA_BODY", "").strip()
    try:
        extra_body = json.loads(extra_raw) if extra_raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM_EXTRA_BODY が JSON として不正です: {e}")
    if not isinstance(extra_body, dict):
        raise ValueError("LLM_EXTRA_BODY は JSON オブジェクトで指定してください")

    tier = os.getenv("AI_AUDIT_TIER", "").strip().lower() or None
    if tier not in (None, "compact", "standard", "large"):
        raise ValueError(f"AI_AUDIT_TIER は compact / standard / large のいずれかです（現在値: {tier!r}）")
    reasoning = os.getenv("LLM_REASONING", "off").strip().lower() or "off"
    if reasoning not in ("off", "low", "medium", "high"):
        raise ValueError(f"LLM_REASONING は off / low / medium / high のいずれかです（現在値: {reasoning!r}）")

    data_dir = Path(os.getenv("AI_AUDIT_DATA_DIR", "~/.ai_audit")).expanduser()
    return Settings(
        api_base_url=os.getenv("LLM_API_BASE_URL", "").strip(),
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        model_name=os.getenv("LLM_MODEL_NAME", "").strip(),
        max_output_tokens=_int_or_none("LLM_MAX_OUTPUT_TOKENS"),
        context_length=_int_or_none("LLM_CONTEXT_LENGTH"),
        context_budget=_int_or_none("LLM_CONTEXT_BUDGET"),
        tier=tier,
        reasoning=reasoning,
        extra_body=extra_body,
        parallel=max(1, _int_or_none("LLM_PARALLEL") or 1),
        embedding_model=os.getenv("LLM_EMBEDDING_MODEL", "").strip(),
        server_type=(os.getenv("LLM_SERVER_TYPE", "auto").strip().lower() or "auto"),
        timeout=_int_or_none("LLM_TIMEOUT") or 3600,
        data_dir=data_dir,
    )


def validate_env() -> None:
    """必須設定が未設定ならメッセージを出して終了する。"""
    load_env()
    missing = [(v, d) for v, d in _REQUIRED_VARS if not os.getenv(v, "").strip()]
    if not missing:
        return
    print("[ERROR] 以下の必須設定が .env に設定されていません:\n", file=sys.stderr)
    for var, desc in missing:
        print(f"  {var}\n    → {desc}\n", file=sys.stderr)
    print(
        f"  .env ファイルの場所: {get_env_path()}\n"
        f"  .env.example を参考に設定してください。\n"
        f"  例: cp .env.example .env  （その後 .env を編集）",
        file=sys.stderr,
    )
    sys.exit(1)


def update_env_var(key: str, value: str | None) -> None:
    """
    .env の指定キーを書き換える（なければ末尾に追記）。
    value が None の場合はキーをコメントアウトする。
    """
    env_path = get_env_path()
    if not env_path.exists():
        raise FileNotFoundError(
            f".env ファイルが見つかりません: {env_path}\n"
            "cp .env.example .env を実行して .env を作成してください。"
        )
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(rf"^#?\s*{re.escape(key)}\s*=.*$")
    new_line = f"# {key}=\n" if value is None else f"{key}={value}\n"
    for i, line in enumerate(lines):
        if pattern.match(line.rstrip()):
            lines[i] = new_line
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(new_line)
    env_path.write_text("".join(lines), encoding="utf-8")
    # 同一プロセス内の後続処理にも反映する
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def fetch_ollama_models(base_url: str) -> list[dict]:
    """Ollama の /api/tags からモデル一覧を取得する。"""
    import requests

    if not base_url:
        raise RuntimeError("LLM_API_BASE_URL が設定されていないため、モデル一覧を取得できません。")
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    url = f"{root}/api/tags"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        models_raw = resp.json().get("models", [])
    except requests.RequestException as e:
        raise RuntimeError(
            f"Ollama モデル一覧の取得に失敗しました: {e}\nURL: {url}\n"
            "接続先（LLM_API_BASE_URL）が正しいか .env を確認してください。"
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
