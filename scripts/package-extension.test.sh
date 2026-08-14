#!/usr/bin/env bash
# scripts/package-extension.test.sh
#
# package-extension.sh 的验收测试。扩展目录没有测试框架(免构建的 MV3 扩展),
# 所以用这个脚本断言打包脚本的可观察行为。
#
# 跑法: bash scripts/package-extension.test.sh
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1

SRC="chrome-extension"
OUT="release/chrome-extension"
PASS=0
FAIL=0

check() {
  local desc="$1" cond="$2"
  if [ "$cond" = "0" ]; then
    printf '  PASS  %s\n' "$desc"; PASS=$((PASS + 1))
  else
    printf '  FAIL  %s\n' "$desc"; FAIL=$((FAIL + 1))
  fi
}

echo "== 跑打包脚本 =="
bash scripts/package-extension.sh >/dev/null
check "脚本退出码为 0" "$?"

echo "== 产物完整性 =="
[ -f "$OUT/manifest.json" ]; check "产物有 manifest.json" "$?"
[ -f "$OUT/popup.js" ];      check "产物有 popup.js" "$?"
[ -d "$OUT/icons" ];         check "产物有 icons/ 子目录" "$?"

# 源码有几个文件,产物就该有几个(不多不少)
src_count=$(find "$SRC" -type f | wc -l)
out_count=$(find "$OUT" -type f | wc -l)
[ "$src_count" -eq "$out_count" ]; check "文件数与源码一致 ($src_count)" "$?"

echo "== version_name 注入 =="
vn=$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('version_name',''))")
[ -n "$vn" ]; check "产物 manifest 有 version_name" "$?"
sha=$(git rev-parse --short HEAD)
case "$vn" in *"$sha"*) r=0;; *) r=1;; esac
check "version_name 含当前 sha ($sha)" "$r"

echo "== 源码零污染 =="
# 只测"打包脚本有没有往源码目录写东西",不测"源码目录本身干不干净"——
# 开发者在 chrome-extension/ 下有自己的未提交改动是完全合法的日常状态,
# 不该被这个测试判失败。做法:在跑打包脚本前后各拍一次 git status 快照,
# 断言"delta 为空",这样才是隔离出脚本自己的效果。
pre_src_status="$(git status --porcelain -- "$SRC")"
bash scripts/package-extension.sh >/dev/null
post_src_status="$(git status --porcelain -- "$SRC")"
[ "$pre_src_status" = "$post_src_status" ]; check "打包脚本未改动源码目录的 git status" "$?"
src_vn=$(python3 -c "import json;print(json.load(open('$SRC/manifest.json')).get('version_name',''))")
[ -z "$src_vn" ]; check "源码 manifest 没有 version_name" "$?"

echo "== 工作区脏时标 -dirty =="
# 在源码目录造一个未跟踪文件 → git status 非空 → 脚本该标 -dirty。
# 用 __ 前缀 + trap 兜底删除,避免中途 Ctrl-C 留下垃圾探针文件
# (未跟踪、不在 .gitignore 里,会让之后每次打包都误标 -dirty)。
dirty_probe="$SRC/__dirty_probe.tmp"
sibling_probe="release/__sibling_probe.txt"
cleanup_probes() { rm -f "$dirty_probe" "$sibling_probe"; }
trap cleanup_probes EXIT

# 打探针之前的快照决定后面"-dirty 消失"这条断言是否可信:如果开发者本来就
# 在 chrome-extension/ 下有未提交改动,探针删掉后工作区仍然是脏的,-dirty
# 理应继续出现,断言"应消失"就会产生假失败——这种情况下跳过该断言并打印
# SKIP,而不是算通过或失败。
pre_dirty_snapshot="$(git status --porcelain -- "$SRC")"
touch "$dirty_probe"
bash scripts/package-extension.sh >/dev/null
dirty_vn=$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('version_name',''))")
rm -f "$dirty_probe"
case "$dirty_vn" in *-dirty*) r=0;; *) r=1;; esac
check "有未跟踪文件时 version_name 带 -dirty" "$r"
# 探针删掉后重打包,标记该消失(否则 -dirty 会粘住,变成永远的假警报)——
# 仅当打探针之前工作区本就干净时才有意义。
bash scripts/package-extension.sh >/dev/null
if [ -z "$pre_dirty_snapshot" ]; then
  clean_vn=$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('version_name',''))")
  case "$clean_vn" in *-dirty*) r=1;; *) r=0;; esac
  check "工作区恢复干净后 -dirty 消失" "$r"
else
  printf '  SKIP  工作区恢复干净后 -dirty 消失 (打探针前 %s 下已有未提交改动,无法判定)\n' "$SRC"
fi

echo "== 先清空:源码没有的文件不该留在产物里 =="
touch "$OUT/__stale_probe.js"
bash scripts/package-extension.sh >/dev/null
[ ! -f "$OUT/__stale_probe.js" ]; check "重跑后陈旧文件被清掉" "$?"

echo "== 清理范围:不碰 release/ 其它内容 =="
mkdir -p release && touch "$sibling_probe"
bash scripts/package-extension.sh >/dev/null
[ -f "$sibling_probe" ]; check "release/ 下的兄弟文件未被误删" "$?"
rm -f "$sibling_probe"

printf '\n通过 %s / 失败 %s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
