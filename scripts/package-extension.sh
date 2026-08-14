#!/usr/bin/env bash
# scripts/package-extension.sh
#
# 把 chrome-extension/ 打包到 release/chrome-extension/,供 Chrome 的
# Load unpacked 指向。目的是让"正在使用的扩展"与 git 工作树解耦 —— 直接加载
# 源码目录会让切分支 / git clean / 未提交改动立刻作用到生产用的扩展上。
#
# 产物那份 manifest 会被注入 version_name(含 commit sha),弹窗里能直接看到
# 装的是哪一版;源码那份一个字节都不动,避免打包弄脏工作区。
#
# 跑法: bash scripts/package-extension.sh
# 之后: chrome://extensions/ → 该扩展点刷新;首次则 Load unpacked 选产物目录。
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

SRC="chrome-extension"
OUT="release/chrome-extension"

[ -d "$SRC" ] || { echo "ERROR: 找不到源码目录 $SRC" >&2; exit 1; }

# 版本标签:干净工作区用 sha,有未提交改动加 -dirty —— 此时 sha 已不能代表
# 产物内容,不标出来就是给一个假保证。
sha="$(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain -- "$SRC")" ]; then
  sha="${sha}-dirty"
fi

# 只删产物目录这一级。release/ 本身可能放别的东西,rm -rf release/ 会误伤。
rm -rf "${OUT:?}"
mkdir -p "$OUT"
cp -R "$SRC/." "$OUT/"

# 注入 version_name(仅产物)。用 python3 而非 sed,避免破坏 JSON 结构。
python3 - "$OUT/manifest.json" "$sha" <<'PY'
import json, sys
path, sha = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    m = json.load(f)
m["version_name"] = f"{m['version']} ({sha})"
with open(path, "w", encoding="utf-8") as f:
    json.dump(m, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

label="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version_name'])" "$OUT/manifest.json")"
echo "打包完成: $REPO_ROOT/$OUT"
echo "版本标签: $label"
echo "下一步: chrome://extensions/ → 点该扩展的刷新图标(首次则 Load unpacked 选上面的目录)"
