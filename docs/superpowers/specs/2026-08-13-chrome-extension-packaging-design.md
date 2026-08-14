# Chrome 扩展打包到独立发布目录

**日期**：2026-08-13
**状态**：设计已确认，待实施

## 问题

`chrome-extension/` 是免构建的 MV3 扩展（无 `package.json`、无打包步骤），安装方式是 Chrome 里 **Load unpacked 直接指向 git 工作树中的源码目录**。

Chrome 会永久记住这个路径，于是开发目录的任何动荡都会直接作用到"正在使用的扩展"上：

| 操作 | 后果 |
|------|------|
| 切到功能分支 | 扩展变成那个分支的版本，可能是半成品 |
| `git clean -ffdx` | 扩展文件被删，Chrome 里直接报错 |
| 工作区有未提交改动 | 跑的是未经审查的代码，自己也未必记得 |

生产用的东西不该指向开发目录。

## 方案

加一个打包脚本，把源码复制到独立的发布目录，Chrome 改为指向发布目录。

```
chrome-extension/  ──复制──▶  release/chrome-extension/   ← Chrome 指向这里
    (源码,零改动)                (产物,.gitignore 已忽略 release/)
```

### 为什么产物放仓库内

`.gitignore` 第 17 行已有 `release/`，路径跟仓库在一起好找。

代价是 `git clean -ffdx` 会连忽略文件一起删掉——重跑一次脚本即可恢复，可接受。（备选方案是放仓库外如 `~/nous-extension/`，完全免疫 git 操作，但路径与仓库分家，需额外记文档。已评估后选择仓库内。）

### 版本可见化：`version_name`

Chrome 的 `manifest.version` 只允许数字，塞不进 commit sha。MV3 有官方展示字段 `version_name`：

```json
"version": "1.3.1",
"version_name": "1.3.1 (aefb817e)"
```

**只写产物那份 manifest，源码那份一个字节都不动。** 否则每次打包都会弄脏 git 工作区，用一个麻烦换另一个麻烦。

`popup.js` 当前只读 `version`：

```js
versionEl.textContent = 'v' + chrome.runtime.getManifest().version;
```

改为优先读 `version_name`、缺失则回落到 `version`。**回落路径不能断**——直接从源码目录 Load unpacked（调试时的常见做法）必须仍然正常工作，只是不显示 sha。

### 工作区脏时必须标出来

打包时若有未提交改动，sha 标为 `aefb817e-dirty`：

```
v1.3.1 (aefb817e)         干净,可追溯到具体提交
v1.3.1 (aefb817e-dirty)   含未提交改动,sha 仅供参考
```

此时 sha 已不能代表产物内容，不标出来就是给一个假保证。

### 先清空再复制

复制前先删除目标目录。否则源码里删掉的文件会以旧副本形式留在产物里，Chrome 仍会加载它——一种只在"删文件"时才发作的隐蔽故障。

**清理范围严格限定在 `release/chrome-extension/` 这一级**，绝不动 `release/` 本身。那个目录将来可能放别的产物，用 `rm -rf release/` 就会连带毁掉它们。

## 明确的局限

**这套方案不会自动发现过期，只让过期可见。**

打开弹窗看到的 sha 与 `git log -1` 对不上，就说明该重打包了。没有任何机制会主动提醒。

git hook（`post-merge` / `post-checkout`）自动重打包的方案**已评估并否决**：切到功能分支时它会把半成品代码推给 Chrome，且 hook 不进版本控制、换机器要重装。

日常流程：

```
git pull  →  bash scripts/package-extension.sh  →  chrome://extensions/ 点刷新
```

## 交付物

| 文件 | 改动 |
|------|------|
| `scripts/package-extension.sh` | 新增。清空目标 → 复制 → 写 `version_name` |
| `chrome-extension/popup.js` | 版本显示优先 `version_name`，回落 `version` |
| `chrome-extension/README.md` | 安装章节改指发布目录 + 说明改代码要重跑脚本 |

`chrome-extension/manifest.json` **不改**——`version_name` 只存在于产物中。

## 验收

1. 跑脚本 → `release/chrome-extension/` 内容与源码一致，且多出 `version_name`
2. 源码目录 `git status` 干净（脚本没污染工作区）
3. Chrome 加载发布目录 → 弹窗显示 `v1.3.1 (<sha>)`
4. Chrome 加载**源码**目录 → 弹窗显示 `v1.3.1`（回落生效，不报错）
5. 制造一个未提交改动再跑 → sha 带 `-dirty`
6. 源码删一个文件后重跑 → 产物里对应文件也消失（验证先清空）
