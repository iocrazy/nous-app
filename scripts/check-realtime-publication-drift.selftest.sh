#!/usr/bin/env bash
# check-realtime-publication-drift.sh 的自测：四个场景钉住 0/1/2 三个退出码。
# 只用临时目录与文件，不碰生产。
set -uo pipefail
CHECK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-realtime-publication-drift.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

expect() {
  local want="$1" name="$2"; shift 2
  "$@" >/dev/null 2>&1; local got=$?
  if [[ "$got" == "$want" ]]; then
    printf '  ✓ %s (exit %d)\n' "$name" "$got"; pass=$((pass + 1))
  else
    printf '  ✗ %s — 期望 exit %d，实得 %d\n' "$name" "$want" "$got"; fail=$((fail + 1))
  fi
}

mkdir -p "$TMP/fe/components" "$TMP/fe/components/__tests__"
cat > "$TMP/fe/components/a.ts" <<'TS'
supabase.channel('x').on('postgres_changes', { event: '*', schema: 'public', table: 'agent_runs' }, cb)
supabase.channel('y').on('postgres_changes', { event: '*', schema: 'public', table: 'issues' }, cb)
TS
# 测试文件里的订阅不算需求
echo "on('postgres_changes', { table: 'only_in_tests' })" > "$TMP/fe/components/a.test.ts"
printf 'agent_runs\nissues\nresources\n' > "$TMP/live_ok.txt"
printf 'issues\nresources\n'             > "$TMP/live_missing.txt"
: > "$TMP/live_empty.txt"
mkdir -p "$TMP/fe_none"; echo "const x = 1" > "$TMP/fe_none/a.ts"

expect 0 "订阅集 ⊆ 发布 → 绿（多出来的发布表不算漂移；测试文件不算需求）" \
  env FRONTEND_DIR="$TMP/fe" LIVE_TABLES_FILE="$TMP/live_ok.txt" bash "$CHECK"
expect 1 "发布里少一张前端订阅的表 → 红" \
  env FRONTEND_DIR="$TMP/fe" LIVE_TABLES_FILE="$TMP/live_missing.txt" bash "$CHECK"
expect 2 "发布列表为空 → 报'检查失效'而非'一致'" \
  env FRONTEND_DIR="$TMP/fe" LIVE_TABLES_FILE="$TMP/live_empty.txt" bash "$CHECK"
expect 2 "前端一处订阅都没扫到 → 报'检查失效'而非'一致'" \
  env FRONTEND_DIR="$TMP/fe_none" LIVE_TABLES_FILE="$TMP/live_ok.txt" bash "$CHECK"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" == 0 ]] || exit 1
