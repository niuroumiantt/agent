#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "此入口在 M5/M3/M4 的 macOS 本机运行；开发环境请使用 uv run glocal-agent serve。"
  exit 1
fi
AGENT_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${AGENT_REPO_DIR}"
if ! command -v uv >/dev/null 2>&1; then
  echo "本机需要 uv。请先用已有包管理器安装 uv，再重新运行本脚本。"
  exit 1
fi
uv sync --locked
if [ ! -f "${HOME}/.config/agent/config.json" ] && [ -z "${AGENT_BASE_URL:-}" ]; then
  uv run glocal-agent configure
fi
exec uv run glocal-agent serve
