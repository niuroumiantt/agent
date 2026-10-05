#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "在已能执行 ssh spark 的 M5 本机运行此脚本。"
  exit 1
fi
echo "保持此终端打开：M5 127.0.0.1:11435 → Spark Ollama 127.0.0.1:11434。"
echo "工作台配置选择 Spark Ollama API，地址填写 http://127.0.0.1:11435。"
exec ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:11435:127.0.0.1:11434 spark
