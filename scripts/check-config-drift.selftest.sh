#!/usr/bin/env bash
# check-config-drift.sh 的自测 —— 守卫的守卫。
#
# 为什么需要：漂移检查平时的正常输出就是"一致"。如果它某天悄悄坏成
# "永远说一致"，表现与它健康时**完全同形**，没有任何东西会发现。
# 本仓吃过这一族的亏（not_probed / xvfb_ready / 遮蔽后的 pg_stat_activity）。
#
# 四个场景覆盖两向：该红的红，该绿的绿，以及检查自己跑不起来时**不许**报绿。
# 在 CI 里先于真检查执行；它不碰生产目录（全部用临时目录 + LIVE_DIR 覆盖）。

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
CHECK="$HERE/check-config-drift.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

expect() {
  local want=$1 name=$2; shift 2
  local got
  "$@" >/dev/null 2>&1
  got=$?
  if [ "$got" -eq "$want" ]; then
    printf '  ✓ %s (exit %d)\n' "$name" "$got"; pass=$((pass + 1))
  else
    printf '  ✗ %s — 期望 exit %d，实得 %d\n' "$name" "$want" "$got"; fail=$((fail + 1))
  fi
}

# 造一份与仓库完全一致的"生产"目录（排除项不复制，与真实生产同形）
mkdir -p "$TMP/same"
while IFS= read -r f; do
  rel="${f#deploy/gpu-server/supabase/}"
  case "$rel" in .env.example|README.md) continue ;; esac
  mkdir -p "$TMP/same/$(dirname "$rel")"
  cp "$REPO_ROOT/$f" "$TMP/same/$rel"
done < <(git -C "$REPO_ROOT" ls-files -- deploy/gpu-server/supabase)

# 场景 3 先跑：后面两个场景要在它的基础上制造偏差
echo "自测 check-config-drift.sh："
expect 0 "两侧一致 → 绿" env LIVE_DIR="$TMP/same" bash "$CHECK"

# 一个字节的差异就必须转红（不是只认整段改动）
cp -r "$TMP/same" "$TMP/onebyte"
printf '\n# hand-edited on the server\n' >> "$TMP/onebyte/docker-compose.yml"
expect 1 "生产被手改一行 → 红" env LIVE_DIR="$TMP/onebyte" bash "$CHECK"

# 生产缺文件 ≠ 没有漂移
mkdir -p "$TMP/empty"
expect 1 "生产侧文件全缺 → 红" env LIVE_DIR="$TMP/empty" bash "$CHECK"

# 检查跑不起来必须与"一致"区分开 —— 这条是整个自测的重点
expect 2 "活目录不存在 → 报'检查失效'而非'一致'" \
  env LIVE_DIR="$TMP/does-not-exist" bash "$CHECK"

echo
if [ "$fail" -gt 0 ]; then
  printf '✗ 自测失败：%d 通过，%d 失败 —— 漂移检查本身不可信，先修它。\n' "$pass" "$fail"
  exit 1
fi
printf '✓ 自测通过（%d/%d）\n' "$pass" "$pass"
