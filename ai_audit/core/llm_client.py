"""
LLM クライアント: OpenAI 互換 Chat Completions API の呼び出し

- 構造化出力: json_schema → json_object → 抽出・修復 の順にフォールバック
- 思考モード: 応答の reasoning_content / <think> を本文から除去する
  LLM_REASONING が off 以外なら reasoning_effort を送る（解釈はサーバ依存）。
  思考の ON/OFF をサーバ固有パラメータで制御する場合は LLM_EXTRA_BODY を使う
  （例: {"chat_template_kwargs": {"enable_thinking": false}}）。
- リトライ: 接続エラー・タイムアウト・429・5xx のみ。コンテキスト超過は ContextOverflowError
"""
import json
import time
from dataclasses import dataclass

import requests

from .config import Settings
from .jsonutil import extract_json, strip_thinking, validate


class LLMError(RuntimeError):
    """LLM 呼び出しの失敗。"""


class ContextOverflowError(LLMError):
    """入力がサーバのコンテキスト長を超えた。"""


class LLMOutputError(LLMError):
    """応答が期待した形式（JSON / スキーマ）にならなかった。"""


_OVERFLOW_HINTS = ("context length", "context_length", "maximum context", "too long",
                   "exceeds", "exceed the", "n_ctx", "context window", "prompt is too")


@dataclass
class LLMResponse:
    text: str
    data: object = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed: float = 0.0
    truncated: bool = False


class LLMClient:
    retry_delay = 2.0
    max_retries = 3

    def __init__(self, settings: Settings, counter=None, on_usage=None):
        self.settings = settings
        self.counter = counter
        self.on_usage = on_usage
        self._structured_mode = "json_schema"   # json_schema / json_object / none

    # ------------------------------------------------------------------
    def _url(self) -> str:
        return f"{self.settings.api_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.settings.api_key:
            h["Authorization"] = f"Bearer {self.settings.api_key}"
        return h

    def _payload(self, system: str, user: str, max_tokens, temperature, response_format) -> dict:
        payload: dict = {
            "model": self.settings.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        limit = max_tokens or self.settings.max_output_tokens
        if limit:
            payload["max_tokens"] = limit
        if self.settings.reasoning != "off":
            payload["reasoning_effort"] = self.settings.reasoning
        if response_format:
            payload["response_format"] = response_format
        payload.update(self.settings.extra_body)
        return payload

    def _post(self, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                r = requests.post(self._url(), headers=self._headers(), json=payload,
                                  timeout=self.settings.timeout)
            except requests.RequestException as e:
                last_error = e
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        last_error = e
                elif r.status_code == 429 or r.status_code >= 500:
                    last_error = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    body = r.text[:500]
                    if any(h in body.lower() for h in _OVERFLOW_HINTS):
                        raise ContextOverflowError(f"コンテキスト長を超えました: {body}")
                    raise LLMError(f"HTTP {r.status_code}: {body}")
            if attempt <= self.max_retries:
                time.sleep(self.retry_delay * (2 ** (attempt - 1)))
        raise LLMError(f"LLM API 呼び出しに失敗しました（{self.max_retries + 1} 回試行）: {last_error}")

    def _call(self, system, user, max_tokens, temperature, response_format) -> LLMResponse:
        started = time.time()
        data = self._post(self._payload(system, user, max_tokens, temperature, response_format))
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"応答の形式が不正です: {str(data)[:300]}")
        usage = data.get("usage") or {}
        resp = LLMResponse(
            text=strip_thinking(content),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            elapsed=time.time() - started,
            truncated=choice.get("finish_reason") == "length",
        )
        if self.counter and resp.prompt_tokens:
            self.counter.observe(system + user, resp.prompt_tokens)
        if self.on_usage:
            self.on_usage(resp)
        return resp

    # ------------------------------------------------------------------
    def chat(self, system: str, user: str, *, max_tokens: int | None = None,
             temperature: float = 0.2) -> LLMResponse:
        """テキスト応答を返す。"""
        return self._call(system, user, max_tokens, temperature, None)

    def chat_json(self, system: str, user: str, *, schema: dict | None = None,
                  max_tokens: int | None = None, temperature: float = 0.1) -> LLMResponse:
        """
        JSON 応答を返す（resp.data にパース結果）。schema 指定時は簡易検証も行う。
        1 回目が不正なら、誤りを伝えて 1 回だけ再生成する。それでも不正なら LLMOutputError。
        """
        attempt_user = user
        last_problem = ""
        for attempt in range(2):
            resp = self._call_structured(system, attempt_user, schema, max_tokens, temperature)
            try:
                resp.data = extract_json(resp.text)
                problems = validate(resp.data, schema) if schema else []
            except ValueError as e:
                problems = [str(e)]
            if not problems:
                return resp
            last_problem = "; ".join(problems[:5])
            attempt_user = (
                f"{user}\n\n---\n前回の出力は次の理由で不正でした: {last_problem}\n"
                "指定の JSON 形式のみを出力してください（説明文やコードフェンスは不要）。"
            )
        raise LLMOutputError(f"JSON 応答が不正です: {last_problem}")

    def _call_structured(self, system, user, schema, max_tokens, temperature) -> LLMResponse:
        while True:
            if self._structured_mode == "json_schema" and schema:
                fmt = {"type": "json_schema",
                       "json_schema": {"name": "result", "schema": schema, "strict": False}}
            elif self._structured_mode in ("json_schema", "json_object"):
                fmt = {"type": "json_object"}
            else:
                fmt = None
            try:
                return self._call(system, user, max_tokens, temperature, fmt)
            except ContextOverflowError:
                raise
            except LLMError as e:
                # response_format 非対応のサーバ（400 系）なら一段下げて再試行
                if fmt is None or "HTTP 4" not in str(e):
                    raise
                self._structured_mode = "json_object" if fmt["type"] == "json_schema" else "none"


def schema_hint(schema: dict) -> str:
    """プロンプトに埋め込むための JSON Schema 文字列。"""
    return json.dumps(schema, ensure_ascii=False, indent=1)
