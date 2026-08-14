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
[ -z "$(git status --porcelain -- "$SRC")" ]; check "源码目录 git status 干净" "$?"
src_vn=$(python3 -c "import json;print(json.load(open('$SRC/manifest.json')).get('version_name',''))")
[ -z "$src_vn" ]; check "源码 manifest 没有 version_name" "$?"

echo "== 工作区脏时标 -dirty =="
# 在源码目录造一个未跟踪文件 → git status 非空 → 脚本该标 -dirty。
# 用 __ 前缀 + 立即删除,避免留下垃圾。
touch "$SRC/__dirty_probe.tmp"
bash scripts/package-extension.sh >/dev/null
dirty_vn=$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('version_name',''))")
rm -f "$SRC/__dirty_probe.tmp"
case "$dirty_vn" in *-dirty*) r=0;; *) r=1;; esac
check "有未提交改动时 version_name 带 -dirty" "$r"
# 探针删掉后重打包,标记该消失(否则 -dirty 会粘住,变成永远的假警报)
bash scripts/package-extension.sh >/dev/null
clean_vn=$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('version_name',''))")
case "$clean_vn" in *-dirty*) r=1;; *) r=0;; esac
check "工作区恢复干净后 -dirty 消失" "$r"

echo "== 先清空:源码没有的文件不该留在产物里 =="
touch "$OUT/__stale_probe.js"
bash scripts/package-extension.sh >/dev/null
[ ! -f "$OUT/__stale_probe.js" ]; check "重跑后陈旧文件被清掉" "$?"

echo "== 清理范围:不碰 release/ 其它内容 =="
mkdir -p release && touch release/__sibling_probe.txt
bash scripts/package-extension.sh >/dev/null
[ -f release/__sibling_probe.txt ]; check "release/ 下的兄弟文件未被误删" "$?"
rm -f release/__sibling_probe.txt

printf '\n通过 %s / 失败 %s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
