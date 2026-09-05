#!/usr/bin/env bash
# 生产 supabase_realtime 发布列表 vs 前端实际订阅的表
#
# 为什么存在：前端用 `channel().on('postgres_changes', { table: 'x' })` 订阅一张
# 表时，如果那张表不在生产的 `supabase_realtime` 发布里，订阅**静默无事发生**——
# 不报错、不断线，只是永远收不到行。2026-09-04 实证：agent_runs / user_logs /
# system_status / resource_tags 四张表在生产发布里缺失（迁移 145 等早就 ADD 过，
# 发布列表在某次重置后丢了），任务中心的 agent run 卡片因此从不实时更新。
# 与 `check-config-drift.sh` 同族：绿灯覆盖着一个不存在的保护。
#
# 用法：
#   bash scripts/check-realtime-publication-drift.sh
#   FRONTEND_DIR=/tmp/fe LIVE_TABLES_FILE=/tmp/live.txt bash scripts/...   # 测试用
#
# 退出码：0 = 前端订阅的表全在发布里；1 = 有缺；2 = 检查本身没跑起来
#（前端一处订阅都没扫到 / 生产发布列表拿不到或为空）。
#
# ⚠️ 2 与 0 必须分开：扫不到订阅、连不上库，都不能读作"没漂移"。
set -uo pipefail
export LC_ALL=C   # sort 与 comm 必须同一排序规则，否则 comm 报 "not in sorted order"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${FRONTEND_DIR:-$REPO_ROOT/frontend}"
DB_CONTAINER="${DB_CONTAINER:-nous-db}"
DB_PORT="${DB_PORT:-55434}"

red()  { printf '\033[0;31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[0;33m%s\033[0m\n' "$*"; }

# ── 1. 前端订阅集 ────────────────────────────────────────────────────────
if [[ ! -d "$FRONTEND_DIR" ]]; then
  red "✗ 前端目录不存在：$FRONTEND_DIR"; exit 2
fi
# 只认生产代码里的订阅；测试文件里的 table 名不算需求。
frontend_tables=$(grep -rhoE "table: '[a-z_]+'" "$FRONTEND_DIR" \
  --include='*.ts' --include='*.tsx' \
  --exclude='*.test.ts' --exclude='*.test.tsx' --exclude-dir=node_modules --exclude-dir=dist \
  2>/dev/null | sed -E "s/table: '//; s/'//" | sort -u)
if [[ -z "$frontend_tables" ]]; then
  red "✗ 前端一处 realtime 订阅都没扫到 —— 是扫描坏了，不是没有漂移"; exit 2
fi

# ── 2. 生产发布列表 ──────────────────────────────────────────────────────
if [[ -n "${LIVE_TABLES_FILE:-}" ]]; then
  live_tables=$(sort -u "$LIVE_TABLES_FILE" 2>/dev/null)
else
  live_tables=$(docker exec "$DB_CONTAINER" psql -U postgres -p "$DB_PORT" -d postgres -Atc \
    "SELECT tablename FROM pg_publication_tables WHERE pubname='supabase_realtime' AND schemaname='public' ORDER BY 1" \
    2>/dev/null | sort -u)
fi
if [[ -z "$live_tables" ]]; then
  red "✗ 拿不到生产 supabase_realtime 的表列表（容器不在 / 发布为空）—— 检查失效，不是一致"; exit 2
fi

# ── 3. 比对 ──────────────────────────────────────────────────────────────
missing=$(comm -23 <(printf '%s\n' "$frontend_tables") <(printf '%s\n' "$live_tables"))
n_fe=$(printf '%s\n' "$frontend_tables" | wc -l | tr -d ' ')
if [[ -n "$missing" ]]; then
  red "✗ 前端订阅了、但生产 supabase_realtime 发布里没有的表："
  printf '   - %s\n' $missing
  ylw "  修法：这些表的 realtime 订阅在生产静默失效。跑（或重跑）迁移 452"
  ylw "  （幂等，把前端订阅集整体补齐），或手工："
  for t in $missing; do
    ylw "    ALTER PUBLICATION supabase_realtime ADD TABLE public.$t;"
  done
  exit 1
fi
grn "✓ 前端订阅的 $n_fe 张表全部在生产 supabase_realtime 发布里"
exit 0
