"""
LLMクライアント: GPT-oss-120b等のOpenAI互換APIへのリクエスト送信

すべての設定は .env から読み込む。config.json は使用しない。

環境変数（.env）:
  LLM_API_BASE_URL      : APIのベースURL（例: http://192.168.1.40:11434/v1）  ※必須
  LLM_API_KEY           : APIキー（Ollama の場合は "ollama" 等、任意の文字列でOK）  ※必須
  LLM_MODEL_NAME        : 使用するモデル名（例: gpt-oss:120b）  ※必須
  LLM_MAX_OUTPUT_TOKENS : 最大出力トークン数（未設定=モデルのデフォルト最大値を使用）
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_MAX_RETRIES = 3
_RETRY_DELAY = 2  # 秒


def _get_settings() -> dict:
    """config.json → .env → デフォルト の優先順で設定を返す。"""
    # config_manager は循環参照を避けるため遅延 import
    from .config_manager import load_config
    return load_config()


def call_llm(system_prompt: str, user_content: str, json_mode: bool = True) -> str:
    """
    LLM APIを呼び出し、レスポンステキストを返す。

    Args:
        system_prompt: システムプロンプト（ウェア）
        user_content:  ユーザーメッセージ（解析対象コード等）
        json_mode:     JSONフォーマットでのレスポンスを要求するか

    Returns:
        LLMのレスポンス文字列。JSONモード時はJSON文字列。

    Raises:
        RuntimeError: 最大リトライ回数を超えてもAPIが成功しない場合
    """
    cfg = _get_settings()
    base_url          = cfg["api_base_url"]
    api_key           = cfg["api_key"]
    model             = cfg["model_name"]
    max_output_tokens = cfg["max_output_tokens"]  # None = モデルのデフォルト最大値を使用

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.2,
    }
    # max_output_tokens が None の場合は max_tokens をペイロードに含めない
    # → モデル側のデフォルト最大トークン数がそのまま適用される
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=3600)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * attempt)

    raise RuntimeError(f"LLM API呼び出しに失敗しました（{_MAX_RETRIES}回試行）: {last_error}")


def parse_json_response(raw: str) -> dict:
    """
    LLMのレスポンスをJSONとしてパースする。
    JSONパースに失敗した場合は空のissuesリストを返す。

    Args:
        raw: LLMから返された生テキスト

    Returns:
        パースされた辞書
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # JSON部分の抽出を試みる
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        return {"issues": [], "_parse_error": raw[:200]}
