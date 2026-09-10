#!/usr/bin/env bash
# ci-changed-areas.sh —— 给 ci.yml 的 `changes` job 算「这个 PR 碰了哪些区域」，
# 五个测试 job 据此决定跑不跑。输出写到 $GITHUB_OUTPUT（未设置时打到 stdout）：
#   frontend=true|false  backend=…  browser=…  rust=…  codex_daemon=…
#
# 为什么存在：托管 runner 按 **job 并行数** 计费，不按墙钟。五个 job 一起跑
# 一次 = 22–44 计费分钟；而最近 60 个已合并 PR 里 20% 是纯文档、25% 只碰
# backend/、18% 只碰 frontend/ —— 它们每次都把五个 job 跑满，等于把一半以上
# 的分钟数花在与改动无关的检查上（2026-09-09 实测，见 CLAUDE.md「CI/CD 部署」）。
#
# 为什么不用 on.pull_request.paths：那会让整个 workflow **不触发**，一旦哪天
# 开了 branch protection，required check 永远 pending 卡死 PR。job 级 `if:`
# 跳过的 job 在 GitHub 眼里是 Skipped、对 required check 记作通过，没有这个坑。
#
# ⚠️ 三条不变量，改任何一条先去 ci-changed-areas.selftest.sh 看会红哪个：
#
# 1. **兜底只许退化成「全跑」，绝不许退化成「全跳」。** 拿不到文件清单、清单
#    为空、清单撞到 REST 的 3000 条上限 —— 一律五个 job 全 true 并打
#    ::warning::。2026-08-23 的 lint 门禁就是反例：diff 失败被 `|| true` 吞成
#    空列表，三个 linter 全 SKIPPED 而 job 报绿（ci.yml backend job 有记）。
# 2. **映射表外的一切路径都全跑。** 只有明确落进某个区域、或明确是文档的
#    路径才能让某个 job 不跑。新加一个顶层目录忘了登记，代价是多跑，不是漏跑。
# 3. **区域目录下的任何文件都算该区域，包括 .md。** `backend/seeds/**/SKILL.md`
#    会被 SeedLoader 读、被测试断言——文档豁免只对区域之外的路径生效。
#
# 文件清单来源是 GitHub REST（pulls/{n}/files），不是 `git diff origin/base...HEAD`：
# 后者依赖 fetch 深度，正是 2026-08-23 那次静默失效的触发条件。
# 覆盖点 CHANGED_FILES_CMD 只给自测用。

set -uo pipefail

AREAS=(frontend backend browser rust codex_daemon)

emit() {  # emit <frontend> <backend> <browser> <rust> <codex_daemon> <reason>
  local i=0 line
  for a in "${AREAS[@]}"; do
    i=$((i + 1))
    line="$a=${!i}"
    if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$line" >> "$GITHUB_OUTPUT"; else echo "$line"; fi
  done
  echo "changed-areas: $6"
}

fallback_all() {  # 不变量 1：任何自身故障 → 全跑
  echo "::warning::ci-changed-areas: $1 —— 退化为五个 job 全跑"
  emit true true true true true "全跑（兜底：$1）"
  exit 0
}

if [ -z "${CHANGED_FILES_CMD:-}" ]; then
  if [ -z "${PR_NUMBER:-}" ] || [ -z "${GITHUB_REPOSITORY:-}" ]; then
    fallback_all "缺 PR_NUMBER / GITHUB_REPOSITORY"
  fi
  CHANGED_FILES_CMD="gh api --paginate repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/files --jq .[].filename"
fi

# "命令失败" 与 "清单为空" 必须分开：前者是探针坏了，后者是探针没探到——
# 两者都不能读成"没改动"（一个 PR 不可能零文件）。
if ! files=$(bash -c "$CHANGED_FILES_CMD" 2>&1); then
  fallback_all "取 PR 文件清单失败：$(echo "$files" | tail -1)"
fi
count=$(printf '%s\n' "$files" | grep -c .)
[ "$count" -eq 0 ] && fallback_all "PR 文件清单为空"
[ "$count" -ge 3000 ] && fallback_all "PR 有 ${count} 个文件，撞到 REST 上限，清单可能被截断"

frontend=false backend=false browser=false rust=false codex_daemon=false
forced=""      # 第一个把我们逼成全跑的路径（写进摘要，让门禁决策在 PR 上看得见）

while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    frontend/*)            frontend=true ;;
    backend/*)             backend=true ;;
    supabase/*)            backend=true ;;            # 迁移取号查重跑在 backend job
    browser/*)             browser=true ;;
    nous-core/*)           rust=true; backend=true ;; # pyo3 扩展；backend 运行时 import nous_core
    tools/codex-daemon/*)  codex_daemon=true ;;
    # —— 文档豁免：只对区域之外的路径生效（不变量 3）——
    docs/*|.claude/*|.superpowers/*) ;;
    *.md)  case "$f" in */*) forced="${forced:-$f}" ;; esac ;;  # 只豁免根目录的 .md
    # —— 不变量 2：其余一切全跑 ——
    *)     forced="${forced:-$f}" ;;
  esac
done <<< "$files"

if [ -n "$forced" ]; then
  emit true true true true true "全跑（${forced} 不在区域映射表内；共 ${count} 个文件）"
else
  emit "$frontend" "$backend" "$browser" "$rust" "$codex_daemon" \
    "frontend=${frontend} backend=${backend} browser=${browser} rust=${rust} codex_daemon=${codex_daemon}（共 ${count} 个文件）"
fi
