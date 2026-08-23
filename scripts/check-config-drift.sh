#!/usr/bin/env bash
# 生产 Supabase 栈配置漂移检查
#
# 为什么存在：同一份 compose 配置在机器上有两份 —— 仓库里被 git 管着的那份，
# 和 gpupc 上真正在跑的那份（`/media/heygo/program/datahub/nous/supabase/`，
# 不在任何 git 仓库里）。没有任何机制保证它们相等。
#
# 2026-08-22 实证了这个缺口的代价：PR #1964 把 `max_connections` 100→200 写进
# 仓库那份，PR 合了、CI 全绿、部署也绿 —— 而生产至今仍是 100，因为没有人去
# 同步活文件。整条链上没有任何一处会说出"这个改动其实没生效"。
# 那正是本仓库反复吃亏的那一族：绿灯覆盖着一个不存在的保护。
#
# 本脚本只做一件事：把两侧对一下，不一致就红，并说清差在哪、该跑什么。
#
# 用法：
#   bash scripts/check-config-drift.sh
#   LIVE_DIR=/tmp/fake bash scripts/check-config-drift.sh    # 测试用
#
# 退出码：0 = 一致；1 = 漂移；2 = 检查本身没能跑起来（见下）。
#
# ⚠️ 退出码 2 与 0 必须分开。"活目录不存在"或"一个文件都没比到"绝不能读作
# "没有漂移" —— 那会让这个守卫在自己坏掉的时候报平安，和它要防的故障同形。

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="${REPO_DIR:-$REPO_ROOT/deploy/gpu-server/supabase}"
LIVE_DIR="${LIVE_DIR:-/media/heygo/program/datahub/nous/supabase}"

# 仓库独有、生产不该有的文件。活目录里没有它们是正确的，不是漂移。
EXCLUDE=(
  ".env.example"   # 模板；真 .env 带密钥，永不进 git
  "README.md"      # 文档
)

red()  { printf '\033[0;31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[0;33m%s\033[0m\n' "$*"; }

is_excluded() {
  local f=$1 e
  for e in "${EXCLUDE[@]}"; do [ "$f" = "$e" ] && return 0; done
  return 1
}

echo "仓库侧: $REPO_DIR"
echo "生产侧: $LIVE_DIR"
echo

# ── 前置：检查本身能不能成立 ────────────────────────────────────────────
if [ ! -d "$REPO_DIR" ]; then
  red "✗ 仓库目录不存在: $REPO_DIR"
  red "  这个脚本的比对锚点搬家了 —— 修脚本，别忽略。"
  exit 2
fi

if [ ! -d "$LIVE_DIR" ]; then
  red "✗ 生产目录不存在: $LIVE_DIR"
  red "  只有 gpupc 上有这份活配置。若在别的机器上跑，这个检查不适用；"
  red "  若就在 gpupc 上，说明生产栈的位置变了 —— 那本身就是要查的事。"
  exit 2
fi

# 比对集 = 仓库侧被 git 跟踪的文件（未跟踪的本地产物不参与）
mapfile -t TRACKED < <(git -C "$REPO_ROOT" ls-files -- "${REPO_DIR#"$REPO_ROOT"/}" 2>/dev/null)
if [ "${#TRACKED[@]}" -eq 0 ]; then
  red "✗ 在仓库里没找到任何被跟踪的文件（路径: ${REPO_DIR#"$REPO_ROOT"/}）"
  red "  可能是路径写错、或不在 git 工作树里跑。不当作'没有漂移'。"
  exit 2
fi

# ── 逐文件比对 ──────────────────────────────────────────────────────────
compared=0
drifted=()
missing=()
skipped=0

for path in "${TRACKED[@]}"; do
  rel="${path#"${REPO_DIR#"$REPO_ROOT"/}/"}"
  if is_excluded "$rel"; then
    skipped=$((skipped + 1))
    continue
  fi
  live="$LIVE_DIR/$rel"
  if [ ! -f "$live" ]; then
    missing+=("$rel")
    continue
  fi
  compared=$((compared + 1))
  if ! diff -q "$REPO_ROOT/$path" "$live" >/dev/null 2>&1; then
    drifted+=("$rel")
  fi
done

# 自检：排除项之外一个都没比到，说明比对逻辑失效了，不是"全都一致"。
if [ "$compared" -eq 0 ] && [ "${#missing[@]}" -eq 0 ]; then
  red "✗ 一个文件都没比到（跟踪 ${#TRACKED[@]} 个，跳过 $skipped 个）"
  red "  比对逻辑失效。'没比到' 不等于 '没漂移'。"
  exit 2
fi

echo "已比对 $compared 个文件（跳过 $skipped 个仓库独有文件）"
echo

# ── 报告 ────────────────────────────────────────────────────────────────
fail=0

if [ "${#missing[@]}" -gt 0 ]; then
  fail=1
  red "✗ 生产侧缺失 ${#missing[@]} 个文件："
  for f in "${missing[@]}"; do echo "    $f"; done
  echo
fi

if [ "${#drifted[@]}" -gt 0 ]; then
  fail=1
  red "✗ ${#drifted[@]} 个文件在两侧不一致："
  echo
  for f in "${drifted[@]}"; do
    ylw "── $f ──"
    diff -u "$LIVE_DIR/$f" "$REPO_ROOT/${REPO_DIR#"$REPO_ROOT"/}/$f" \
      --label "生产（正在跑）" --label "仓库（master）" || true
    echo
  done
fi

if [ "$fail" -eq 1 ]; then
  cat <<'MSG'
────────────────────────────────────────────────────────────────────────
这不是 CI 环境问题，是生产配置与仓库不一致 —— 二者必有一个是错的。

方向判断：
  • 仓库比生产新 → 有人改了配置但没同步到生产。那个改动**当前不生效**，
    尽管它的 PR 是绿的、部署也是绿的。
  • 生产比仓库新 → 有人手改了生产但没回写仓库。仓库在说谎，下一个照着
    仓库做事的人会踩空。

同步（仓库 → 生产）。注意必须在**活目录**里跑，那才是 mediahub-sb-prod
这个 compose 项目；从仓库目录跑会拿到另一个项目名、且缺 .env：

  D=/media/heygo/program/datahub/nous/supabase
  R=<本仓库路径>
  cp -p "$D/docker-compose.yml" "$D/docker-compose.yml.bak-$(date +%Y%m%d-%H%M%S)"
  git -C "$R" show origin/master:deploy/gpu-server/supabase/docker-compose.yml > "$D/docker-compose.yml"
  cd "$D" && docker compose up -d db     # ⚠️ 挑业务空窗；restart 不重读 command

反向（生产 → 仓库）：把活文件的改动提交进仓库，并在 commit 里写清为什么。
────────────────────────────────────────────────────────────────────────
MSG
  exit 1
fi

grn "✓ 生产配置与仓库一致（$compared 个文件）"
exit 0
