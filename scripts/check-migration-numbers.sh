#!/usr/bin/env bash
#
# migration 取号查重 —— 拦住"两个 PR 各自取到同一个号"。
#
# 起因（2026-08-09，同一天两次）：
#   #1752 与 #1753 都取了 412；改名成 413 之后两小时，#1767 又取了 413。
#   两个 PR 在各自开发期互相看不见对方的分支，各自 fetch 到的 master 都不含
#   对方的迁移 —— **"取号前先 fetch"防不住这个窗口**，只有在 CI 运行那一刻
#   重新对最新 master 比一次才拦得住。
#
# 因此本脚本的核心是：**跟"CI 跑这一刻的 origin/<base>"比，不是跟 merge-base
# 比**。merge-base 是 PR 拉分支时的那个 master，它恰好看不见并发合入的迁移
# —— 拿它当基准等于把要防的窗口本身当成了基准。
#
# 只拦"本次新增/改名的号"，不体检全仓：仓库里有 15 个历史重复号
# （080 107 120 121 122 248 298 315 323 363 384 401 402 403 407），
# 它们是既成事实，全量校验会让每个 PR 都无辜变红，门禁三天内就会被绕过。
#
# 用法：
#   bash scripts/check-migration-numbers.sh pr [base_ref]   # PR 门禁（默认 error）
#   bash scripts/check-migration-numbers.sh push            # 合并后巡检（默认 warning）
#
# 严重级可用 MIGRATION_DUP_SEVERITY=error|warning 覆盖（供本地反向验证用）。

set -euo pipefail

MODE="${1:-}"
BASE_REF="${2:-master}"
DIR="supabase/migrations"

# 只看正向迁移。*_rollback.sql 是人工 workflow_dispatch 专用的逆向迁移，
# 与正向文件共号是设计如此（run-migration.yml / schema-drift.yml 都排除它们）。
list_migrations() {
  git ls-tree -r --name-only "$1" -- "$DIR" 2>/dev/null \
    | grep -E "^${DIR}/[0-9]{3}_.*\.sql$" \
    | grep -Ev '_rollback\.sql$' \
    || true
}

strip_blank() { sed '/^[[:space:]]*$/d'; }

case "$MODE" in
  pr)
    # 显式 fetch，拿 CI 运行这一刻的 base，而不是 checkout 时的快照。
    # 用 FETCH_HEAD 而不是 refs/remotes/origin/<base>：后者取决于仓库的
    # fetch refspec 配置（本仓库的 refspec 只跟踪 master），FETCH_HEAD 无此依赖。
    git fetch --no-tags --quiet origin "$BASE_REF"
    BASE_LIST=$(list_migrations FETCH_HEAD)
    HEAD_LIST=$(list_migrations HEAD)
    # 本 PR 引入的文件 = PR 树里有、最新 base 树里没有。
    # 用树差集而不是 `git diff --diff-filter=AR merge-base...HEAD`：树差集
    # 天然反映"合进去之后长什么样"，且不依赖 merge-base（正是要绕开的东西）。
    NEW=$(comm -13 \
      <(printf '%s\n' "$BASE_LIST" | strip_blank | sort) \
      <(printf '%s\n' "$HEAD_LIST" | strip_blank | sort))
    # 判重的全集必须是"最新 base ∪ PR"，只看其中一边都会漏掉这次的场景。
    UNIVERSE=$(printf '%s\n%s\n' "$BASE_LIST" "$HEAD_LIST" | strip_blank | sort -u)
    SEVERITY="${MIGRATION_DUP_SEVERITY:-error}"
    ;;
  push)
    # 合并后巡检：master 上 HEAD 就是最新 master，不需要 fetch。
    NEW=$(git diff --name-only --diff-filter=AR HEAD~1 HEAD -- "$DIR" \
      | grep -E "^${DIR}/[0-9]{3}_.*\.sql$" \
      | grep -Ev '_rollback\.sql$' \
      | strip_blank || true)
    UNIVERSE=$(list_migrations HEAD | strip_blank)
    # 默认只警告，不阻断：号已经合进来了，此刻 fail 也撤销不了重复，
    # 却会把 run-migration.yml 拦死 —— 代码已部署而 schema 没跟上，
    # 那比重复号本身危险得多。这里的价值是"立刻可见"，不是"阻止"。
    SEVERITY="${MIGRATION_DUP_SEVERITY:-warning}"
    ;;
  *)
    echo "usage: $0 <pr|push> [base_ref]" >&2
    exit 2
    ;;
esac

if [ -z "$NEW" ]; then
  echo "No new/renamed migration files in this ${MODE}. Nothing to check."
  exit 0
fi

echo "New/renamed migrations in this ${MODE}:"
printf '%s\n' "$NEW" | sed 's/^/  - /'
echo ""

status=0
checked=""
while read -r f; do
  [ -n "$f" ] || continue
  n=$(basename "$f" | cut -d_ -f1)
  # 同一个号在 NEW 里出现多次时只报一次
  case " $checked " in *" $n "*) continue ;; esac
  checked="$checked $n"

  hits=$(printf '%s\n' "$UNIVERSE" | grep -E "^${DIR}/${n}_" || true)
  count=$(printf '%s\n' "$hits" | strip_blank | wc -l | tr -d ' ')

  if [ "$count" -gt 1 ]; then
    joined=$(printf '%s\n' "$hits" | strip_blank | tr '\n' ' ')
    echo "::${SEVERITY}::migration 号 ${n} 重复: ${joined}— 取一个未占用的号改名（git mv），并把改名后的文件改成幂等（改名可能被 run-migration.yml 判成新增而在生产重跑）。"
    if [ "$SEVERITY" = "error" ]; then
      status=1
    fi
  else
    echo "OK: ${n} 唯一 (${hits})"
  fi
done <<EOF
$NEW
EOF

if [ "$status" -ne 0 ]; then
  echo ""
  echo "取号撞车。参考 PR #1762 / #1769 的处置规程：git mv 改号 + 连带改全仓引用 + 幂等化 + 一次性容器连跑两遍。"
fi
exit "$status"
