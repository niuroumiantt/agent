#!/usr/bin/env python3
"""CodeBuddy Code 本地 HTTP API 探测器（WorkBuddy 接入「线路二」）。

背景：WorkBuddy 桌面端本身不开放 API。要把能力接进自己的应用，需要在本机安装
独立的 CodeBuddy Code CLI，并以 serve 模式启动一个本地 HTTP 服务：

    npm i -g @tencent-ai/codebuddy-code     # 公开渠道，与桌面端内置的那份无关
    codebuddy --serve --port 8787           # 首次启动后用浏览器打开 Web UI 登录
    # 交互文档：http://127.0.0.1:8787/api/docs
    # OpenAPI 规范：http://127.0.0.1:8787/api/openapi.json

凭据落在 ~/.codebuddy/，后续调用不需要任何 API Key。--serve 默认开启密码认证，
密码写在 ~/.codebuddy/settings.json；只绑 127.0.0.1 时才考虑 --auth none。
绝不要把这个端口暴露到局域网或公网——它能执行命令、读写文件。

本模块只依赖标准库，用来验证这条链路是否通：
    health()        服务是否在跑
    start_run()     发一条 prompt，拿回 runId
    stream_events() 读 SSE 增量
    ask()           跑完并聚合全文

配置（环境变量）：
    CODEBUDDY_GATEWAY_URL   默认 http://127.0.0.1:8787
    CODEBUDDY_GATEWAY_TOKEN 可选；--auth none 启动时留空

命令行：
    python3 codebuddy_serve.py --health
    python3 codebuddy_serve.py --prompt "用一句话介绍你自己" --timeout 120
    python3 codebuddy_serve.py --prompt "..." --raw     # 打印原始 SSE 帧

注意：本 Beta 接口的 SSE 事件字段名以 /api/docs 为准，extract_text() 只是兜底
抽取；接生产前请按真实形状收紧。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Iterator

DEFAULT_URL = "http://127.0.0.1:8787"
# 除 /api/v1/health 等豁免路径外，所有请求都必须带这个自定义请求头，
# 缺了会被服务端以 403 拒绝。
REQUEST_HEADER = "X-CodeBuddy-Request"
_TEXT_KEYS = ("text", "delta", "content", "chunk", "message")

# 目标是本机回环地址，绝不走 HTTP 代理——环境里配了 http_proxy 时
# urllib 默认会把 127.0.0.1 也交给代理，然后拿到一个看不懂的 502。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class GatewayError(RuntimeError):
    """链路或协议错误。消息里不带任何凭据。"""


def base_url() -> str:
    return (os.environ.get("CODEBUDDY_GATEWAY_URL") or DEFAULT_URL).rstrip("/")


def token() -> str:
    return os.environ.get("CODEBUDDY_GATEWAY_TOKEN", "").strip()


def _headers(accept: str = "application/json") -> dict[str, str]:
    headers = {REQUEST_HEADER: "1", "Accept": accept, "User-Agent": "codebuddy-serve-probe/1"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    return headers


def _open(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
    accept: str = "application/json",
):
    body = None
    headers = _headers(accept)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url() + path, data=body, headers=headers, method=method)
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GatewayError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise GatewayError(
            f"连不上 {base_url()}{path}: {exc.reason}。"
            "先确认 `codebuddy --serve` 正在运行，且 CODEBUDDY_GATEWAY_URL 指对了。"
        ) from None


def health() -> dict[str, Any]:
    """GET /api/v1/health（豁免路径，不校验鉴权）。"""
    with _open("GET", "/api/v1/health", timeout=10) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    return envelope.get("data", envelope)


def start_run(
    prompt: str,
    *,
    run_id: str | None = None,
    conversation_id: str | None = None,
    sender: str = "probe",
) -> str:
    """POST /api/v1/runs，返回 runId。请求体形状见 gateway-protocol.ts。"""
    import uuid

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    payload = {
        "id": run_id,
        "type": "message",
        "source": {
            "platform": "generic",
            "sender": {"id": sender, "name": sender},
            "conversation": {"id": conversation_id or run_id, "type": "direct"},
        },
        "payload": {"text": prompt},
    }
    with _open("POST", "/api/v1/runs", payload=payload, timeout=30) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    data = envelope.get("data", envelope)
    resolved = data.get("runId") or data.get("id") or run_id
    return str(resolved)


def stream_events(run_id: str, *, timeout: float = 300.0) -> Iterator[dict[str, str]]:
    """GET /api/v1/runs/{run_id}/stream，按 SSE 帧 yield {"event","data"}。"""
    path = "/api/v1/runs/" + urllib.request.quote(str(run_id), safe="") + "/stream"
    with _open("GET", path, timeout=timeout, accept="text/event-stream") as response:
        event_name = "message"
        data_lines: list[str] = []
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    yield {"event": event_name, "data": "\n".join(data_lines)}
                event_name, data_lines = "message", []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_name = value
        if data_lines:
            yield {"event": event_name, "data": "\n".join(data_lines)}


def extract_text(payload: str | dict[str, Any]) -> str:
    """从一帧数据里兜底抽取文本。字段名以 /api/docs 为准，这里只做兼容。"""
    if isinstance(payload, str):
        try:
            node = json.loads(payload)
        except json.JSONDecodeError:
            return payload
    else:
        node = payload
    return _collect(node)


def _collect(node: Any) -> str:
    parts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _TEXT_KEYS and isinstance(value, str):
                parts.append(value)
            else:
                parts.append(_collect(value))
    elif isinstance(node, list):
        for item in node:
            parts.append(_collect(item))
    return "".join(parts)


def ask(
    prompt: str,
    *,
    timeout: float = 300.0,
    on_event: Any = None,
) -> str:
    """发一条 prompt，读完整条流，返回聚合后的纯文本。"""
    run_id = start_run(prompt)
    chunks: list[str] = []
    for frame in stream_events(run_id, timeout=timeout):
        if on_event is not None:
            on_event(frame)
        chunks.append(extract_text(frame["data"]))
    return "".join(chunks).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测本机 CodeBuddy Code serve HTTP API")
    parser.add_argument("--health", action="store_true", help="只检查服务是否在跑")
    parser.add_argument("--prompt", help="要发给 Agent 的一句话")
    parser.add_argument("--timeout", type=float, default=300.0, help="单条流的读取超时（秒）")
    parser.add_argument("--raw", action="store_true", help="打印原始 SSE 帧")
    args = parser.parse_args(argv)

    try:
        if args.health:
            print(json.dumps(health(), ensure_ascii=False))
            return 0
        if not args.prompt:
            parser.error("要么 --health，要么 --prompt")
        if args.raw:
            text = ask(args.prompt, timeout=args.timeout, on_event=lambda f: print(f, flush=True))
        else:
            text = ask(args.prompt, timeout=args.timeout)
        print(text or "(没有抽取到文本，加 --raw 看原始帧)")
        return 0
    except GatewayError as exc:
        print(f"[gateway] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
