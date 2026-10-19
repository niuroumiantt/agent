#!/usr/bin/env python3
"""在没有真 CLI 的机器上，用 mock 验证 codebuddy_serve.py 的客户端逻辑。

    python3 test_codebuddy_serve.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import codebuddy_serve as probe  # noqa: E402
from mock_codebuddy_gateway import serve_in_thread  # noqa: E402


def _raw_get(url: str, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    server, url = serve_in_thread()
    os.environ["CODEBUDDY_GATEWAY_URL"] = url
    os.environ.pop("CODEBUDDY_GATEWAY_TOKEN", None)

    try:
        # 1. 健康检查（豁免路径）
        status = probe.health()
        check("health() 返回 ok", status.get("status") == "ok", json.dumps(status, ensure_ascii=False))

        # 2. 完整一轮：建 run -> 读流 -> 聚合文本
        text = probe.ask("帮我总结这封邮件", timeout=30)
        check("ask() 聚合到文本", text.startswith("收到：帮我总结这封邮件"), text)

        # 3. SSE 帧可逐条读取
        run_id = probe.start_run("第二句话")
        frames = list(probe.stream_events(run_id, timeout=30))
        check("stream_events() 逐帧返回", len(frames) == 4, f"{len(frames)} 帧")
        check("末帧是 done", json.loads(frames[-1]["data"]).get("stopReason") == "end_turn")

        # 4. 缺 X-CodeBuddy-Request 头必须被拒（服务端 403）
        code, _ = _raw_get(f"{url}/api/v1/sessions", {"Accept": "application/json"})
        check("缺安全头被 403 拒绝", code == 403, f"HTTP {code}")

        # 5. 404 包形状
        try:
            probe.health()
            probe._open("GET", "/api/v1/nope", timeout=10)
            check("未知路径抛 GatewayError", False)
        except probe.GatewayError as exc:
            check("未知路径抛 GatewayError", "404" in str(exc))
    finally:
        server.shutdown()
        server.server_close()

    # 6. 带 token 的网关：不给 token 401，给了才通
    server, url = serve_in_thread(token="s3cret")
    os.environ["CODEBUDDY_GATEWAY_URL"] = url
    try:
        code, _ = _raw_get(f"{url}/api/v1/sessions", {"X-CodeBuddy-Request": "1"})
        check("缺 token 被 401 拒绝", code == 401, f"HTTP {code}")
        os.environ["CODEBUDDY_GATEWAY_TOKEN"] = "s3cret"
        check("带 token 可以 health", probe.health().get("status") == "ok")
    finally:
        os.environ.pop("CODEBUDDY_GATEWAY_TOKEN", None)
        server.shutdown()
        server.server_close()

    print()
    if failures:
        print(f"{len(failures)} 项失败: {failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
