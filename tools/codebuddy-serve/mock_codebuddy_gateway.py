#!/usr/bin/env python3
"""CodeBuddy Code serve HTTP API 的本地 mock，用于在没有真 CLI 的机器上验证客户端。

只实现探测器用到的几个端点，行为按公开文档复刻：
  - 所有非豁免路径都要求 X-CodeBuddy-Request: 1，缺了 403
  - 配了 token 时就要求 Authorization: Bearer <token>
  - POST /api/v1/runs 返回 {"data":{"runId":...,"status":"accepted"}}
  - GET /api/v1/runs/{id}/stream 返回 SSE，最后以一帧 done 结束

事件字段名是猜的（Beta 接口以 /api/docs 为准），所以探测器必须容忍任意形状。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXEMPT = {"/api/v1/health"}


class _State:
    def __init__(self, token: str = "") -> None:
        self.token = token
        self.runs: dict[str, str] = {}


def make_server(port: int = 0, token: str = "") -> tuple[ThreadingHTTPServer, _State]:
    state = _State(token)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # 保持测试输出干净
            pass

        def _send(self, status: int, payload: dict, *, content_type: str = "application/json") -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if self.path.split("?")[0] in EXEMPT:
                return True
            if self.headers.get("X-CodeBuddy-Request") != "1":
                self._send(403, {"error": {"code": "FORBIDDEN", "message": "Missing required header"}})
                return False
            if state.token:
                if self.headers.get("Authorization") != f"Bearer {state.token}":
                    self._send(
                        401, {"error": {"code": "AUTH_REQUIRED", "message": "Authentication required"}}
                    )
                    return False
            return True

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if not self._authorized():
                return
            if path == "/api/v1/health":
                self._send(200, {"data": {"status": "ok", "uptime": 1.5, "platforms": ["generic"]}})
                return
            if path.startswith("/api/v1/runs/") and path.endswith("/stream"):
                run_id = path[len("/api/v1/runs/") : -len("/stream")]
                prompt = state.runs.get(run_id, "")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                # HTTP/1.1 keep-alive 下没有 Content-Length，客户端会一直等；
                # 明确告诉它写完了就断开，超时由探测器的 timeout 参数控制。
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                frames = [
                    {"type": "start", "runId": run_id},
                    {"type": "delta", "delta": {"text": f"收到：{prompt[:20]}"}},
                    {"type": "delta", "delta": {"text": "。这是 mock 的回复。"}},
                    {"type": "done", "stopReason": "end_turn", "usage": {"total_tokens": 7}},
                ]
                for frame in frames:
                    body = f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8")
                    self.wfile.write(body)
                    self.wfile.flush()
                return
            self._send(404, {"error": {"code": "NOT_FOUND", "message": path}})

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/v1/runs":
                run_id = str(body.get("id") or "run-anon")
                state.runs[run_id] = str(body.get("payload", {}).get("text", ""))
                self._send(200, {"data": {"runId": run_id, "status": "accepted"}})
                return
            self._send(404, {"error": {"code": "NOT_FOUND", "message": path}})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, state


def serve_in_thread(port: int = 0, token: str = "") -> tuple[ThreadingHTTPServer, str]:
    server, _ = make_server(port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
