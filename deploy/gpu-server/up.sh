#!/usr/bin/env bash
# GPU 机起栈统一入口：先验数据盘挂载 marker，再 compose up
# 用法: ./up.sh [--build] [service...]
set -euo pipefail
cd "$(dirname "$0")"

MARKER=/media/heygo/program/datahub/nous/.mounted
if [ ! -f "$MARKER" ]; then
  echo "❌ 数据盘未挂载（缺 $MARKER）——拒绝起栈，避免 bind mount 在系统盘生成影子目录" >&2
  exit 1
fi
# 媒体中转盘(2026-09-07,/app/downloads 的来源,本机 NVMe):同一条纪律。
# 容器内还有 work_dir_probe 做第二道(marker + 真写),这里挡在起栈之前。
WORK_MARKER=/media/heygo/cache/nous-cache/.mounted
if [ ! -f "$WORK_MARKER" ]; then
  echo "❌ 媒体中转盘未挂载（缺 $WORK_MARKER）——拒绝起栈" >&2
  exit 1
fi

# 共享网络 nous-net 由此处统一预建（supabase 与本 compose 均以 external 挂入，
# 避免跨 compose 项目的网络标签冲突）
docker network inspect nous-net >/dev/null 2>&1 || docker network create nous-net

exec docker compose up -d "$@"
