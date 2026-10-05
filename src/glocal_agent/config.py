from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_PATH = Path.home() / ".config/agent/config.json"
DEFAULT_MODEL = "qwen3.8:27b"


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    provider: str = "ollama"
    base_url: str = ""
    model: str = DEFAULT_MODEL
    api_key: str = ""

    def validate(self) -> Settings:
        if not self.root.is_dir():
            raise ValueError("授权文件目录不存在，请在 M5 指定实际 Downloads 目录。")
        if self.root == self.data_dir or self.root in self.data_dir.parents:
            raise ValueError("运行数据和报告目录必须放在授权输入目录之外。")
        if self.provider not in {"ollama", "gateway", "codex_cli", "claude_code_cli"}:
            raise ValueError("模型服务类型不在已支持的 provider 列表中。")
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("模型地址应为 HTTP(S) 服务地址，不包含凭据、查询或片段。")
        if not self.model.strip():
            raise ValueError("模型名称不能为空。")
        return self

    def public(self) -> dict:
        return {
            "root": str(self.root),
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
        }

    @property
    def configured(self) -> bool:
        if self.provider in {"codex_cli", "claude_code_cli"}:
            from .cli_provider import ready

            return ready(self.provider, self.model)
        return bool(self.base_url)


def load_settings(root: str | None = None, data_dir: str | None = None) -> Settings:
    path = Path(os.environ.get("AGENT_CONFIG", str(CONFIG_PATH))).expanduser()
    values = {}
    if path.exists():
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError
        except (ValueError, OSError) as exc:
            raise ValueError("agent 配置文件无法读取，请重新运行 configure。") from exc

    def pick(name: str, default: str = "") -> str:
        return os.environ.get(f"AGENT_{name.upper()}", str(values.get(name, default)))

    provider = pick("provider")
    base_url = pick("base_url")
    key = pick("api_key")
    provider = provider or "ollama"
    default_model = "brain" if provider == "gateway" else DEFAULT_MODEL
    if provider == "codex_cli":
        default_model = os.environ.get("CODEX_CLI_MODEL", "")
    elif provider == "claude_code_cli":
        default_model = os.environ.get("CLAUDE_CODE_CLI_MODEL", "")
    return Settings(
        root=Path(root or pick("root", str(Path.home() / "Downloads"))).expanduser().resolve(),
        data_dir=Path(
            data_dir or pick("data_dir", str(Path.home() / ".local/share/agent"))
        ).expanduser().resolve(),
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=pick("model", default_model),
        api_key=key,
    ).validate()
