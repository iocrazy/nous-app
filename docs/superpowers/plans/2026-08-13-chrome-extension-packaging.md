# Chrome 扩展打包到独立发布目录 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `chrome-extension/` 打包到 `release/chrome-extension/`，让 Chrome 加载产物而非 git 工作树，并把 commit sha 显示在扩展弹窗里。

**Architecture:** 一个 bash 脚本清空目标目录、复制源码、往产物那份 `manifest.json` 注入 `version_name`（含 sha，工作区脏时加 `-dirty`）。源码目录零改动。`popup.js` 显示版本时优先读 `version_name`、缺失回落 `version`，保证从源码目录加载仍可用。

**Tech Stack:** bash（`git rev-parse` / `git status --porcelain`）、python3（改 JSON，仓库已依赖）、无新增依赖。

## Global Constraints

- **源码 `chrome-extension/manifest.json` 绝不能被脚本修改** —— `version_name` 只存在于产物中。脚本跑完 `git status --porcelain chrome-extension/` 必须为空。
- **清理范围严格限定 `release/chrome-extension/`**，绝不 `rm -rf release/`（该目录将来可能放别的产物）。
- **`popup.js` 的回落路径不能断** —— 直接从源码目录 Load unpacked 时 `version_name` 不存在，必须显示 `v<version>` 而不是 `vundefined`。
- 扩展目录**无测试框架**（无 `package.json`、免构建）。脚本的验证用独立 bash 测试脚本完成。
- UI 文本一律英文（仓库规范）；脚本注释与文档用中文。
- 独立 `.sh` 文件无 CI shellcheck 门禁，但仍按 shellcheck 干净标准写（引号包裹变量、`set -euo pipefail`）。
- 参考 `scripts/branch-health.sh` 的风格：`#!/usr/bin/env bash` + 顶部注释块说明用途。

---

### Task 1: 打包脚本

**Files:**
- Create: `scripts/package-extension.sh`
- Create: `scripts/package-extension.test.sh`

**Interfaces:**
- Produces: 可执行脚本 `scripts/package-extension.sh`，无参数，从仓库任意目录可跑；成功时 exit 0 并在 stdout 打印产物路径与版本标签。产出目录 `release/chrome-extension/`。

- [ ] **Step 1: 写失败的测试**

创建 `scripts/package-extension.test.sh`：

```bash
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
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `bash scripts/package-extension.test.sh`
Expected: FAIL —— `scripts/package-extension.sh: No such file or directory`，且末尾失败数 > 0、退出码非 0。

- [ ] **Step 3: 写打包脚本**

创建 `scripts/package-extension.sh`：

```bash
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
```

- [ ] **Step 4: 加可执行位并跑测试确认通过**

Run:
```bash
chmod +x scripts/package-extension.sh scripts/package-extension.test.sh
bash scripts/package-extension.test.sh
```
Expected: 全部 PASS，末尾 `失败 0`，退出码 0。

- [ ] **Step 5: 确认 release/ 确实被 git 忽略**

Run: `git status --porcelain release/`
Expected: 无输出（`.gitignore` 第 17 行的 `release/` 生效）。若有输出说明忽略规则没盖住，必须先修 `.gitignore` 再继续。

- [ ] **Step 6: 提交**

```bash
git add scripts/package-extension.sh scripts/package-extension.test.sh
git commit -m "chore(ext): 打包脚本 — 扩展产物落到 release/,与 git 工作树解耦

Chrome 的 Load unpacked 直接指向源码目录,于是切分支 / git clean / 未提交改动
都会作用到正在使用的扩展上。改为打包到 release/chrome-extension/。

- version_name 注入含 sha,工作区脏时标 -dirty(此时 sha 不能代表产物内容)
- 只写产物 manifest,源码零改动(测试断言 git status 干净)
- 清理范围严格限定 release/chrome-extension/,不碰 release/ 其它内容
- 配套 package-extension.test.sh:扩展无测试框架,用它断言可观察行为"
```

---

### Task 2: 弹窗显示 version_name + 文档

**Files:**
- Modify: `chrome-extension/popup.js:33-38`
- Modify: `chrome-extension/README.md:5-9`

**Interfaces:**
- Consumes: Task 1 产出的 `release/chrome-extension/manifest.json`（含 `version_name`）。
- Produces: 无代码接口；弹窗右上角文本变为 `v<version_name>`，源码目录加载时回落为 `v<version>`。

- [ ] **Step 1: 改版本显示逻辑**

`chrome-extension/popup.js` 第 33-38 行现状：

```js
// Show the extension version beside the header — read at runtime from the
// manifest so it never drifts from manifest.json.
const versionEl = document.getElementById('appVersion');
if (versionEl && chrome.runtime && chrome.runtime.getManifest) {
  versionEl.textContent = 'v' + chrome.runtime.getManifest().version;
}
```

替换为：

```js
// Show the extension version beside the header — read at runtime from the
// manifest so it never drifts from manifest.json.
//
// Prefer version_name: scripts/package-extension.sh stamps it onto the COPY in
// release/ as "1.3.1 (aefb817e)" so the popup says exactly which commit is
// installed. The fallback matters — loading this folder directly (the debug
// path) has no version_name, and must show "v1.3.1", never "vundefined".
const versionEl = document.getElementById('appVersion');
if (versionEl && chrome.runtime && chrome.runtime.getManifest) {
  const manifest = chrome.runtime.getManifest();
  versionEl.textContent = 'v' + (manifest.version_name || manifest.version);
}
```

- [ ] **Step 2: 验证回落逻辑（node 断言，不依赖浏览器）**

Run:
```bash
node -e '
const pick = (m) => "v" + (m.version_name || m.version);
const cases = [
  [{version:"1.3.1", version_name:"1.3.1 (aefb817e)"}, "v1.3.1 (aefb817e)"],
  [{version:"1.3.1"},                                   "v1.3.1"],
  [{version:"1.3.1", version_name:""},                  "v1.3.1"],
];
let bad = 0;
for (const [m, want] of cases) {
  const got = pick(m);
  console.log(got === want ? "PASS " + got : `FAIL got=${got} want=${want}`);
  if (got !== want) bad++;
}
process.exit(bad ? 1 : 0);
'
```
Expected: 三条全 PASS，退出码 0。第三条覆盖 `version_name` 为空串的情况（`||` 会正确回落，`??` 则不会 —— 这是必须用 `||` 的原因）。

- [ ] **Step 3: 语法检查**

Run: `node --check chrome-extension/popup.js`
Expected: 无输出，退出码 0。

- [ ] **Step 4: 改 README 安装章节**

`chrome-extension/README.md` 第 5-9 行现状：

```markdown
## Install

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select this `chrome-extension/` folder
```

替换为：

```markdown
## Install

Build a release copy first — Chrome remembers whichever folder you pick, and
pointing it at this source folder means branch switches, `git clean`, and
uncommitted work all land straight in the extension you use every day.

```bash
bash scripts/package-extension.sh   # → release/chrome-extension/
```

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select `release/chrome-extension/`

After pulling new code, re-run the script, then hit the reload icon on the
extension card. The popup header shows `v1.3.1 (<commit>)` so you can tell at a
glance which build is loaded — if that commit does not match `git log -1`, the
release copy is stale and needs a re-run. A `-dirty` suffix means it was built
with uncommitted changes.

Loading this `chrome-extension/` folder directly still works for debugging; the
popup then shows the bare version with no commit.
```

- [ ] **Step 5: 重新打包并核对回落两条路径**

Run:
```bash
bash scripts/package-extension.sh
python3 -c "import json;print('产物:', json.load(open('release/chrome-extension/manifest.json')).get('version_name'))"
python3 -c "import json;print('源码:', json.load(open('chrome-extension/manifest.json')).get('version_name'))"
```
Expected: 产物打印 `1.3.1 (<sha>)`；源码打印 `None`（证明源码未被污染，且回落路径确实会被触发）。

- [ ] **Step 6: 跑打包测试确认没回退**

Run: `bash scripts/package-extension.test.sh`
Expected: 全部 PASS，`失败 0`。

- [ ] **Step 7: 提交**

```bash
git add chrome-extension/popup.js chrome-extension/README.md
git commit -m "docs(ext): 弹窗显示 version_name + README 改指发布目录

popup 版本显示改为优先 version_name(打包脚本注入,含 commit sha),缺失时回落
到 version —— 直接从源码目录 Load unpacked 的调试路径必须仍然可用,不能显示
vundefined。用 || 而非 ?? 是为了让空串也走回落。

README 安装章节改指 release/chrome-extension/,并写明:改代码要重跑脚本、
弹窗 sha 与 git log -1 对不上就说明产物过期、-dirty 表示含未提交改动。"
```

---

## 人工验收（合并前跑一遍）

自动化测试覆盖不到浏览器行为，这几步必须手点：

1. `chrome://extensions/` → Load unpacked → 选 `release/chrome-extension/` → 弹窗右上角应显示 `v1.3.1 (<sha>)`，且 sha 与 `git log -1 --format=%h` 一致
2. 再 Load unpacked 一次、这次选 `chrome-extension/` 源码目录 → 弹窗应显示 `v1.3.1`（**不是 `vundefined`**）
3. 随便改一行源码但不提交 → 重跑脚本 → 弹窗 sha 应带 `-dirty` 后缀
4. 扩展基本功能仍可用（打开弹窗、加载标签列表、Push 一个链接）—— 确认复制过程没漏文件

## 已知不做

- **不自动同步**。忘了跑脚本时没有任何主动提醒，只能靠弹窗 sha 与 `git log -1` 对不上时自己发现。git hook 方案在设计阶段已评估并否决（切功能分支时会把半成品推给 Chrome，且 hook 不进版本控制）。
- **不产出 zip**。Chrome 的 Load unpacked 只接受目录，zip 还得手动解压，当前没有分发需求。
- **不改 `manifest.json` 里的 name**（仍是 "MediaHub Push"，README 已是 "Nous Push"）。那是品牌改名遗留，与本次无关，混进来只会让 diff 难读。
