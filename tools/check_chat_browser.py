"""Exercise the real chat UI against synthetic local HTTPS/auth/model fixtures."""

from __future__ import annotations

import argparse
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from glocal_agent.app import create_app
from glocal_agent.config import Settings


def analyzer(settings, instruction, sources):
    time.sleep(0.15)
    source = sources[0]
    return {
        "summary": "清单记录了 32 件产品。请继续核对供应商的单价、交期和付款条件。",
        "documents": [],
        "facts": []
        if source["source_id"] == "S0"
        else [
            {
                "source_id": source["source_id"],
                "locator": source["blocks"][0]["locator"],
                "quote": "Quantity: 32",
                "claim": "材料中的数量是 32 件。",
                "verified": True,
            }
        ],
        "recommendations": ["向供应商确认单价与交期，并补充付款条款。"],
        "warnings": [],
        "model": settings.model,
    }


def planner(settings, content, history, files, result):
    if "总结" in content:
        return {"action": "analyze", "reply": "我会阅读这份清单并保留原文引用。"}
    assert history and result and result["facts"]
    return {
        "action": "reply",
        "reply": "根据刚才的清单，我们可以继续核对三件事：\n\n"
        "1. 数量 32 件是否与采购计划一致。\n2. 供应商的单价、币种和付款条件。\n"
        "3. 交期是否符合项目需要。\n\n确认后，我们可以继续比较报价。",
    }


def run(output):
    with tempfile.TemporaryDirectory(prefix="agent-chat-browser-") as temporary:
        root = Path(temporary)
        uploads = root / "uploads"
        uploads.mkdir()
        key = root / "key.pem"
        cert = root / "cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        key.chmod(0o600)
        reserved = socket.socket()
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        reserved.close()
        proxy_key = "synthetic-browser-proxy-" + "x" * 48
        settings = Settings(
            uploads,
            root / "state",
            provider="gateway",
            model="brain",
            base_url="https://unused.example",
            mode="server",
            public_url=f"https://localhost:{port}",
            proxy_key=proxy_key,
        )
        app = create_app(settings, analyzer, planner)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_keyfile=str(key),
                ssl_certfile=str(cert),
                log_level="error",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise AssertionError("local browser fixture did not start")
                time.sleep(0.02)
            with sync_playwright() as p:
                system_browser = Path("/usr/bin/chromium")
                browser = p.chromium.launch(
                    executable_path=str(system_browser) if system_browser.exists() else None,
                    args=["--no-sandbox"],
                )
                # Only the synthetic localhost certificate uses this test exception.
                context = browser.new_context(
                    ignore_https_errors=True,
                    accept_downloads=True,
                    extra_http_headers={
                        "X-Agent-Proxy-Key": proxy_key,
                        "X-Agent-Subject": str(uuid.uuid4()),
                    },
                    viewport={"width": 1440, "height": 940},
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(settings.public_url, wait_until="networkidle")
                assert not page.locator("#open-sidebar").is_visible()
                assert not page.locator("#close-sidebar").is_visible()
                page.locator("#upload-files").set_input_files(
                    {
                        "name": "采购清单.txt",
                        "mimeType": "text/plain",
                        "buffer": b"Quantity: 32\nSupplier: Example\n",
                    }
                )
                checkbox = page.get_by_role("checkbox", name="选择 采购清单.txt", exact=True)
                checkbox.wait_for()
                assert checkbox.is_checked()
                page.locator("#message-input").fill("总结这份采购清单，并引用原文。")
                page.locator("#message-input").press("Enter")
                page.get_by_role("button", name="↓ Word", exact=True).wait_for(timeout=15000)
                assert page.locator(".message.user .message-files").inner_text() == "采购清单.txt"
                page.locator("#message-input").fill("接下来应该核对什么？")
                page.locator("#message-input").press("Enter")
                page.get_by_text("根据刚才的清单，我们可以继续核对三件事：", exact=False).wait_for()
                if output:
                    output.mkdir(parents=True, exist_ok=True)
                    page.locator("#thread").evaluate("e => e.scrollTop = 0")
                    page.screenshot(path=str(output / "agent-chat-desktop.png"), full_page=True)
                page.reload(wait_until="networkidle")
                page.get_by_text("根据刚才的清单，我们可以继续核对三件事：", exact=False).wait_for()
                page.locator("#message-input").fill("查看原文")
                page.locator("#message-input").press("Enter")
                page.get_by_text("原文预览如下，保留文件中的来源位置。", exact=True).wait_for()
                page.locator(".report-details").last.locator("summary").click()
                assert "Quantity: 32" in page.locator(".preview-block").first.inner_text()
                page.locator("#message-input").fill("下载报告")
                page.locator("#message-input").press("Enter")
                page.get_by_text(
                    "这是该任务已生成的报告，选择需要的格式即可下载。", exact=True
                ).wait_for()
                with page.expect_download() as downloaded:
                    page.get_by_role("button", name="↓ Word", exact=True).last.click()
                assert Path(downloaded.value.path()).read_bytes().startswith(b"PK")
                assert page.locator(".detail-panel").count() == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#message-input").bounding_box()["y"] < 940
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#open-sidebar").click()
                assert page.locator("#sidebar").evaluate("e => e.classList.contains('open')")
                page.locator("#close-sidebar").click()
                assert not page.locator("#sidebar").evaluate("e => e.classList.contains('open')")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#message-input").is_visible()
                if output:
                    page.screenshot(path=str(output / "agent-chat-mobile.png"), full_page=True)
                # File names and material must remain text, including markup-like input.
                page.locator("#upload-files").set_input_files(
                    {
                        "name": "报价 <img>.txt",
                        "mimeType": "text/plain",
                        "buffer": b"<script>alert('fixture')</script>",
                    }
                )
                page.get_by_role("checkbox", name="选择 报价 <img>.txt", exact=True).wait_for()
                assert page.locator(".file-list img").count() == 0
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "browser fixture did not shut down"
    print(
        "Chat browser checks passed: upload, selected-file context, follow-up, reload, "
        "inline source, DOCX download, desktop/mobile and safe file-name rendering."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    run(parser.parse_args().output)
