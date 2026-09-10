#!/usr/bin/env bash
# ci-changed-areas.sh 的自测 —— 门禁的门禁。
#
# 这个脚本决定 ci.yml 五个 job 跑不跑。它最危险的坏法不是"该跑的没跑"这么
# 简单，而是**坏得与健康时同形**：某天悄悄把兜底从「全跑」改成「全跳」，
# 或者把一个新目录漏在映射表外却又被当成文档 —— 之后每个 PR 都绿，没人会
# 发现。2026-08-23 的 lint 门禁就是这么静默关掉的（ci.yml backend job 有记）。
#
# 所以每个场景都断言**完整的输出集合**，两向覆盖：该跑的跑、该跳的跳，
# 以及脚本自己拿不到文件清单时**只许**退化成全跑。
# 在 CI 里先于真计算执行；只用临时文件，不碰 GitHub API。

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/ci-changed-areas.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0

# expect <name> <期望的 key=value 行（空格分隔）> -- <喂给脚本的文件清单行…>
# 文件清单通过 CHANGED_FILES_CMD 注入；"__FAIL__" 模拟取清单的命令失败。
expect() {
  local name=$1 want=$2; shift 2
  [ "${1:-}" = "--" ] && shift
  local out="$TMP/out.$RANDOM" list="$TMP/list.$RANDOM"
  : > "$out"
  if [ "${1:-}" = "__FAIL__" ]; then
    export CHANGED_FILES_CMD="false"
  else
    printf '%s\n' "$@" > "$list"
    [ $# -eq 0 ] && : > "$list"
    export CHANGED_FILES_CMD="cat $list"
  fi
  GITHUB_OUTPUT="$out" bash "$SCRIPT" >"$TMP/log" 2>&1
  local rc=$?
  local got
  got=$(grep -E '^(frontend|backend|browser|rust|codex_daemon)=' "$out" | sort | tr '\n' ' ' | sed 's/ $//')
  local want_sorted
  want_sorted=$(printf '%s\n' $want | sort | tr '\n' ' ' | sed 's/ $//')
  if [ "$rc" -eq 0 ] && [ "$got" = "$want_sorted" ]; then
    printf '  ✓ %s\n' "$name"; pass=$((pass + 1))
  else
    printf '  ✗ %s\n      期望: %s\n      实得: %s (exit %d)\n' "$name" "$want_sorted" "$got" "$rc"
    sed 's/^/      | /' "$TMP/log"
    fail=$((fail + 1))
  fi
}

ALL="frontend=true backend=true browser=true rust=true codex_daemon=true"
NONE="frontend=false backend=false browser=false rust=false codex_daemon=false"

echo "ci-changed-areas 自测："

# —— 该跳的跳 ——
expect "纯文档 PR 五个 job 全跳" "$NONE" -- \
  docs/runbook/deploy-reference.md CLAUDE.md .claude/skills/x/SKILL.md .superpowers/sdd/ledger.md

expect "只改 backend/ 只跑 backend" \
  "frontend=false backend=true browser=false rust=false codex_daemon=false" -- \
  backend/app/api/issues_router.py backend/tests/api/test_x.py

expect "frontend + 文档 只跑 frontend" \
  "frontend=true backend=false browser=false rust=false codex_daemon=false" -- \
  frontend/components/Foo.tsx docs/superpowers/plans/x.md

expect "只改 tools/codex-daemon/ 只跑 codex_daemon" \
  "frontend=false backend=false browser=false rust=false codex_daemon=true" -- \
  tools/codex-daemon/src/main.js

# —— 跨区依赖必须是超集 ——
expect "supabase/migrations 触发 backend（取号查重跑在那个 job）" \
  "frontend=false backend=true browser=false rust=false codex_daemon=false" -- \
  supabase/migrations/462_x.sql

expect "nous-core/ 触发 rust + backend（pyo3 扩展是 backend 的运行时依赖）" \
  "frontend=false backend=true browser=false rust=true codex_daemon=false" -- \
  nous-core/src/lib.rs

expect "backend 目录下的 .md 仍算 backend（seeds/SKILL.md 会被 SeedLoader 读）" \
  "frontend=false backend=true browser=false rust=false codex_daemon=false" -- \
  backend/seeds/skills/foo/SKILL.md

# —— 映射表外的一切都全跑 ——
expect "碰 .github/workflows 全跑" "$ALL" -- .github/workflows/ci.yml docs/x.md
expect "碰 scripts/（两个 job 直接调用）全跑" "$ALL" -- scripts/check-no-zinc.sh
expect "根目录版本文件全跑" "$ALL" -- .nvmrc
expect "根目录非 .md 文件全跑" "$ALL" -- Dockerfile
expect "未知顶层目录全跑" "$ALL" -- admin/src/App.tsx

# —— 脚本自己出问题时只许退化成全跑 ——
expect "取清单的命令失败 → 全跑" "$ALL" -- __FAIL__
expect "清单为空 → 全跑（一个 PR 不可能零文件，空即是探针失效）" "$ALL" --
big="$TMP/big"; : > "$big"; for i in $(seq 1 3000); do echo "docs/f$i.md" >> "$big"; done
export CHANGED_FILES_CMD="cat $big"
out="$TMP/out.big"; : > "$out"
if GITHUB_OUTPUT="$out" bash "$SCRIPT" >/dev/null 2>&1 && ! grep -q '=false' "$out"; then
  printf '  ✓ %s\n' "3000 个文件（REST 上限，清单可能被截断）→ 全跑"; pass=$((pass + 1))
else
  printf '  ✗ %s\n' "3000 个文件（REST 上限，清单可能被截断）→ 全跑"; fail=$((fail + 1))
fi

echo "  通过 ${pass}，失败 ${fail}"
[ "$fail" -eq 0 ]
