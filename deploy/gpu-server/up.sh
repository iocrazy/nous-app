#!/usr/bin/env bash
# GPU 机起栈统一入口：先验数据盘挂载 marker，再 compose up
# 用法: ./up.sh [--build] [service...]
set -euo pipefail
cd "$(dirname "$0")"

MARKER=/media/heygo/program/nous/.mounted
if [ ! -f "$MARKER" ]; then
  echo "❌ 数据盘未挂载（缺 $MARKER）——拒绝起栈，避免 bind mount 在系统盘生成影子目录" >&2
  exit 1
fi

exec docker compose up -d "$@"
