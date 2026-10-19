"""
テスト用ダミー LLM サーバ（OpenAI 互換 API ＋ llama.cpp 風の補助エンドポイント）。

- POST /v1/chat/completions : responder(request_json) の戻り値を content として返す
- POST /v1/embeddings       : 文字列ハッシュから決定的なベクトルを返す
- GET  /props               : llama.cpp 互換（n_ctx を返す）
- POST /tokenize            : 4 文字 = 1 トークンとして tokens を返す
"""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLM:
    def __init__(self, n_ctx: int = 32768):
        self.n_ctx = n_ctx
        self.requests: list[dict] = []
        self.responder = lambda req: "{}"
        self.fail_next = 0          # 次の N 回は 500 を返す
        self.reasoning = None       # reasoning_content に入れる文字列
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def start(self) -> "FakeLLM":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def chat_requests(self) -> list[dict]:
        return [r for r in self.requests if r.get("_path") == "/v1/chat/completions"]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静かにする
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/props":
                    self._send(200, {"default_generation_settings": {"n_ctx": fake.n_ctx},
                                     "n_ctx": fake.n_ctx})
                elif self.path == "/v1/models":
                    self._send(200, {"data": [{"id": "fake-model"}]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length) or b"{}")
                req["_path"] = self.path
                fake.requests.append(req)
                if self.path == "/tokenize":
                    text = req.get("content", "")
                    self._send(200, {"tokens": list(range(max(1, len(text) // 4)))})
                elif self.path == "/v1/embeddings":
                    inputs = req.get("input", [])
                    if isinstance(inputs, str):
                        inputs = [inputs]
                    data = []
                    for i, text in enumerate(inputs):
                        digest = hashlib.sha256(text.encode("utf-8")).digest()
                        data.append({"index": i, "embedding": [b / 255 for b in digest[:16]]})
                    self._send(200, {"data": data})
                elif self.path == "/v1/chat/completions":
                    if fake.fail_next > 0:
                        fake.fail_next -= 1
                        self._send(500, {"error": "temporary"})
                        return
                    content = fake.responder(req)
                    message = {"role": "assistant", "content": content}
                    if fake.reasoning:
                        message["reasoning_content"] = fake.reasoning
                    prompt_chars = sum(len(m.get("content", "")) for m in req.get("messages", []))
                    self._send(200, {
                        "choices": [{"message": message, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": prompt_chars // 4,
                                  "completion_tokens": len(content) // 4},
                    })
                else:
                    self._send(404, {"error": "not found"})

        return Handler
