"""
サーバ自動検出: 推論サーバの種別・最大コンテキスト長・トークナイザの有無を調べる

対応（2026-10 時点の各サーバの公開 API に基づく。仕様変更時はここを直す）:
  llama.cpp : GET  <root>/props          → n_ctx / default_generation_settings.n_ctx
              POST <root>/tokenize       {"content": text} → {"tokens": [...]}
  vLLM      : GET  <base>/models         → data[].max_model_len
              POST <root>/tokenize       {"model": m, "prompt": text} → {"count": n}
  Ollama    : POST <root>/api/show       {"model": m} → model_info["<arch>.context_length"]
              （トークナイザ API なし。実行時の num_ctx は parameters から読む）

検出結果は <data_dir>/server_profile.json にキャッシュする（URL・モデル名が同じ間は再利用）。
"""
import json
import re
import time
from dataclasses import asdict, dataclass, field

import requests

from .config import Settings

# 自動検出できなかった場合の安全側の既定値（4096 環境でも壊れない値）
FALLBACK_CONTEXT = 4096
_CACHE_TTL = 24 * 3600
_PROBE_TIMEOUT = 5


@dataclass
class ServerProfile:
    server_type: str = "unknown"          # llamacpp / vllm / ollama / openai / unknown
    context_length: int = FALLBACK_CONTEXT
    context_source: str = "fallback"      # env / server / fallback
    tokenize: str | None = None           # llamacpp / vllm / None（推定）
    warnings: list[str] = field(default_factory=list)


def _root_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def _get_json(method: str, url: str, **kwargs):
    try:
        r = requests.request(method, url, timeout=_PROBE_TIMEOUT, **kwargs)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def _probe_llamacpp(settings: Settings) -> ServerProfile | None:
    data = _get_json("GET", f"{_root_url(settings.api_base_url)}/props")
    if not isinstance(data, dict):
        return None
    n_ctx = (data.get("default_generation_settings") or {}).get("n_ctx") or data.get("n_ctx")
    if not n_ctx:
        return None
    return ServerProfile("llamacpp", int(n_ctx), "server", "llamacpp")


def _probe_vllm(settings: Settings) -> ServerProfile | None:
    data = _get_json("GET", f"{settings.api_base_url.rstrip('/')}/models")
    if not isinstance(data, dict):
        return None
    models = [m for m in data.get("data", []) if m.get("max_model_len")]
    match = [m for m in models if m.get("id") == settings.model_name] or models[:1]
    if not match:
        return None
    return ServerProfile("vllm", int(match[0]["max_model_len"]), "server", "vllm")


def _probe_ollama(settings: Settings) -> ServerProfile | None:
    data = _get_json("POST", f"{_root_url(settings.api_base_url)}/api/show",
                     json={"model": settings.model_name})
    if not isinstance(data, dict) or "model_info" not in data:
        return None
    max_ctx = None
    for key, value in (data.get("model_info") or {}).items():
        if key.endswith(".context_length") and isinstance(value, int):
            max_ctx = value
            break
    m = re.search(r"num_ctx\s+(\d+)", data.get("parameters") or "")
    runtime = int(m.group(1)) if m else None
    profile = ServerProfile("ollama", runtime or max_ctx or FALLBACK_CONTEXT,
                            "server" if (runtime or max_ctx) else "fallback", None)
    if max_ctx and not runtime:
        profile.context_length = max_ctx
        profile.warnings.append(
            f"Ollama のモデル最大コンテキストは {max_ctx} ですが、実行時のコンテキスト長（num_ctx）は"
            "サーバ設定（OLLAMA_CONTEXT_LENGTH 等）に依存し、これより小さい場合があります。"
            "実際の値を LLM_CONTEXT_LENGTH に設定することを推奨します。"
        )
    return profile


_PROBES = {
    "llamacpp": [_probe_llamacpp],
    "vllm": [_probe_vllm],
    "ollama": [_probe_ollama],
    "openai": [],
    "auto": [_probe_llamacpp, _probe_ollama, _probe_vllm],
}


def _cache_key(settings: Settings) -> str:
    return f"{settings.api_base_url}|{settings.model_name}|{settings.server_type}"


def _load_cache(settings: Settings) -> ServerProfile | None:
    path = settings.data_dir / "server_profile.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get(_cache_key(settings))
        if entry and time.time() - entry["at"] < _CACHE_TTL:
            return ServerProfile(**entry["profile"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _save_cache(settings: Settings, profile: ServerProfile) -> None:
    path = settings.data_dir / "server_profile.json"
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[_cache_key(settings)] = {"at": time.time(), "profile": asdict(profile)}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def probe_server(settings: Settings, use_cache: bool = True) -> ServerProfile:
    """サーバを検出してプロファイルを返す。LLM_CONTEXT_LENGTH があればそれを優先する。"""
    profile = _load_cache(settings) if use_cache else None
    if profile is None:
        profile = ServerProfile(server_type=settings.server_type if settings.server_type != "auto" else "unknown")
        if settings.api_base_url:
            for probe in _PROBES.get(settings.server_type, _PROBES["auto"]):
                found = probe(settings)
                if found:
                    profile = found
                    break
        if profile.context_source == "fallback":
            profile.warnings.append(
                f"サーバから最大コンテキスト長を取得できなかったため {FALLBACK_CONTEXT} として扱います。"
                "LLM_CONTEXT_LENGTH を設定してください。"
            )
        _save_cache(settings, profile)

    if settings.context_length:
        profile.context_length = settings.context_length
        profile.context_source = "env"
        profile.warnings = [w for w in profile.warnings if "LLM_CONTEXT_LENGTH" not in w]
    return profile
