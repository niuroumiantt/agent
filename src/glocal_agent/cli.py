from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from .config import CONFIG_PATH, DEFAULT_MODEL, Settings, load_settings


def configure():
    print("本机 agent 配置。仅存到 ~/.config/agent/config.json，不修改其他应用配置。")
    choice = input(
        "模型服务：1=Spark Ollama API，2=兼容 API，3=Codex CLI，4=Claude Code CLI [1]: "
    ).strip() or "1"
    providers = {"1": "ollama", "2": "gateway", "3": "codex_cli", "4": "claude_code_cli"}
    if choice not in providers:
        raise ValueError("选项无效，配置未写入。")
    provider = providers[choice]
    url = ""
    api_key = ""
    if provider in {"ollama", "gateway"}:
        url = input("模型 API 地址（SSH 隧道可用 http://127.0.0.1:11435）: ").strip()
        if not url:
            raise ValueError("尚未提供模型地址，配置未写入。")
        api_key = getpass.getpass("agent 专用 API key（Ollama 无鉴权时直接回车）: ")
    default_model = "brain" if provider == "gateway" else DEFAULT_MODEL
    if provider in {"codex_cli", "claude_code_cli"}:
        var = "CODEX_CLI_MODEL" if provider == "codex_cli" else "CLAUDE_CODE_CLI_MODEL"
        default_model = os.environ.get(var, "")
        print("CLI 使用本机既有登录，将所选文字交给相应云端模型；不授予 CLI 文件工具权限。")
    model = input(f"模型名 [{default_model}]: ").strip() or default_model
    root = input(f"授权文件目录 [{Path.home() / 'Downloads'}]: ").strip()
    settings = Settings(
        root=Path(root or str(Path.home() / "Downloads")).expanduser().resolve(),
        data_dir=(Path.home() / ".local/share/agent").resolve(),
        provider=provider, base_url=url.rstrip("/"), model=model, api_key=api_key,
    ).validate()
    values = {
        "root": str(settings.root), "provider": provider, "base_url": settings.base_url,
        "model": model, "api_key": api_key,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = CONFIG_PATH.with_suffix(".new")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
        target.replace(CONFIG_PATH)
    finally:
        target.unlink(missing_ok=True)
    print("配置已保存。可以启动工作台；连接和模型权限会在实际分析时验证。")


def main():
    parser = argparse.ArgumentParser(description="Glocal Agent 本机文件试点")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("configure", help="配置 Spark API、模型和授权文件目录")
    serve = commands.add_parser("serve", help="启动只监听本机的工作台")
    serve.add_argument("--root", help="授权输入目录，默认 ~/Downloads")
    serve.add_argument("--data-dir", help="运行数据目录，默认 ~/.local/share/agent")
    serve.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    try:
        if args.command == "configure":
            configure()
        else:
            import uvicorn

            from .app import create_app

            os.umask(0o077)
            settings = load_settings(args.root, args.data_dir)
            print(f"本机工作台：http://127.0.0.1:{args.port}")
            print(f"授权目录：{settings.root}")
            uvicorn.run(
                create_app(settings), host="127.0.0.1", port=args.port,
                access_log=False, log_level="warning",
            )
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
