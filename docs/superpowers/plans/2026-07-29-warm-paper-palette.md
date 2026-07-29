# 全站暖纸配色系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Spec = `docs/superpowers/specs/2026-07-29-warm-paper-palette-design.md`(色值/映射表/纪律以 spec 为准);视觉基准 = 同日 `project-palette-mockup.html`。

**Goal:** 全站换血为暖纸基底 + 低饱和五色点缀系统(绿主角),Tailwind v4 @theme 色阶重映射为主、组件黑 active 小清扫为辅。

**Architecture:** W1 在 `frontend/index.css` 的 @theme 层覆写旧色阶(indigo/violet/emerald/green→绿,purple→李紫,amber→赭,red/rose→砖红,blue/sky→钢蓝)+ 暖纸基底 + 语义 token,1700 处硬编码类零改动换血;W2-W4 按模块清扫 theme 层覆盖不到的黑 active 胶囊等,并刷新 e2e 截图。

**Tech Stack:** Tailwind v4 @theme / React 19 / Playwright(截图基准)。

## Global Constraints

- 色值以 spec §1 为准:绿 #1E7A5B、赭 #A87B2B、砖红 #AD5147、钢蓝 #46708E、李紫 #7A5E8F;暖纸 #F3F0E9/#FCFBF8/#F6F4EE;线 #E5E0D4/#D6D0C0
- 色阶生成:以各语义色为 600 档锚点,OKLCH 空间插值出 50-950(用一次性 node 脚本生成,culori 或手工 oklch 计算;脚本本身不入库,生成值写死进 index.css 并注释锚点与生成方式)
- 对比度硬线:600 实底配白字 ≥4.5:1;100/soft 底配 700+ 深字 ≥4.5:1(生成后逐对验算,写进报告)
- dark 主题:基底不动;点缀五色给暗色适配档(同色相提亮),light/dark 都必须过对比度线
- **类名语义失真是接受的代价**(spec §2.1);新代码一律用语义 token(`ok/warn/danger/info/agent` + `-soft`/`-line`),CLAUDE.md 增补一行纪律
- 每波一个 PR,base master,CI 绿即合(**核对 0 fail 后再合并,两步分开执行**);commit 尾行 `Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`
- push 前 `npm run lint` + `npx tsc --noEmit`(只看自己文件);`frontend/index.css.test.ts` 若断言旧色值需同步更新
- e2e 截图仅存档不 diff——重跑生成新基准即可,但必须人工过目双主题截图并在报告贴结论

---

## PR-K W1 token 换血

### Task K1: @theme 色阶重映射 + 暖纸基底 + 语义 token

**Files:**
- Modify: `frontend/index.css` —— ①@theme 内覆写 indigo/violet/purple/emerald/green/amber/red/rose/blue/sky 全档(50-950)为映射表对应语义色的新梯度;②新增 `--color-ok/warn/danger/info/agent` 及 `-soft`/`-line` 档;③`[data-theme="light"]` 基底变量换暖纸(app-bg/island/island-2/card/line/line-strong/content 阶 + light 档 ink 阶暖化);④`--color-accent` → 绿;⑤`btn-tint-*` light 覆写(~:170)换新色相;⑥dark 主题点缀色适配档
- Modify: `frontend/index.css.test.ts`(断言更新:新锚点值存在、旧 indigo 锚点不再是 #6366f1 等)
- Modify: `CLAUDE.md` UI 规范节加一行:新代码用语义色 token,禁再引入旧色相类名

**Steps:**
- [ ] 写梯度生成脚本(scratch)→ 验算对比度矩阵(600×白、100×700,十个色阶 × light/dark)→ 全部达标后把值写进 index.css
- [ ] `npm run build` 成功;`npx vitest run index.css.test` 绿
- [ ] `npx playwright test workflow-walkthrough stage-board --update-snapshots` 不需要——直接重跑生成新截图,双主题人工过目(重点:项目页、工作区、编辑器右栏与 mockup 对味)
- [ ] commit `feat(theme): 暖纸基底 + 五色低饱和点缀系统 — @theme 色阶整体重映射`

### Task K2: e2e 全量重跑 + 视觉走查报告

**Steps:**
- [ ] `npx playwright test` 全量(所有 spec 的截图在新配色下重生成;断言失败的逐个判断是"断言颜色类名"还是真回归,前者更新断言)
- [ ] 报告贴:五大模块 × 双主题截图结论 + 对比度矩阵
- [ ] commit `test(theme): e2e 截图基准全量刷新至暖纸配色`

**/ship PR-K**:title `feat(theme): warm-paper palette system — full-scale @theme remap (W1)`

---

## PR-L W2 项目模块清扫

### Task L1: 黑 active/中性强调清扫(项目面)

**Files:**
- Modify: `frontend/components/workspace/*`、`components/workflow/*`、`components/Project*`、`pages/ProjectsPage.tsx`、`editor/*` 中的:黑色 active 胶囊(视图切换/分页/格式 segmented/tab,`bg-ink-900 text-white`/`bg-black` active 语义)→ `bg-ok text-white`(即绿);EP chip 黑底 → 中性描边(mockup「topbar」样式);"已保存"chip → ok-soft
- 先 `grep -rnE 'bg-(ink-9|black)' components/workspace components/workflow editor components/Project* pages/ProjectsPage.tsx` 建清单,逐个判断是否 active 语义(非 active 的深底如遮罩、代码块不动),清单和判断写进报告
- Test: 相关 vitest 快照/断言更新;`workflow-walkthrough`/`stage-board` e2e 重跑双主题截图过目

**Steps:**
- [ ] 清单 → 清扫 → vitest + tsc + lint + e2e 绿
- [ ] commit `style(projects): 黑 active 退役 — 项目模块强调态统一绿系`

**/ship PR-L**:title `style(theme): project module accent cleanup (W2)`

---

## PR-M W3 资源库 + 灵感库清扫

### Task M1: 同 L1 方法论,范围 `components/Resource*`、`components/resources/*`(若有)、`ResourcesShell/Sidebar/Grid` 家族、`components/Inspiration/*`、`components/TopicInspiration/*`、`DownloadsView` 等

**Steps:**
- [ ] grep 清单 → 清扫 → 相关 vitest + e2e(resources/inspiration 相关 spec)双主题截图过目
- [ ] commit + **/ship PR-M**:title `style(theme): resources & inspiration accent cleanup (W3)`

---

## PR-N W4 AI 库/聊天/设置/剩余 + 收尾验收

### Task N1: 清扫 `components/ai-library/*`(或实际目录)、`ChatPage`/聊天组件、`Settings*`、`Todolist/*`、导航壳(`AppLayout`/`Sidebar`/`IslandShell`)残余

### Task N2: 收尾验收
- [ ] 全站 grep 复查:不再有 active 语义的黑底;`bg-ink-900` 仅剩非强调用途(清单归档报告)
- [ ] playwright 全量绿;五模块 × 双主题最终截图集 + 对比度复核贴报告
- [ ] commit + **/ship PR-N**:title `style(theme): remaining modules cleanup + visual acceptance (W4)`

---

## Self-Review 摘要

- spec §2.1-§2.5 全部有归属:2.1/2.2/2.3→K1,2.4→L/M/N 分模块,2.5→K2+N2;§4 不做清单未入任务。
- 顺序:K 先行(全局变色)再清扫,避免清扫时对着旧色调色;L-N 互独立但按模块串行,便于每波人工过目。
- 风险:@theme 覆写若有组件依赖旧色相做语义判断(如 JS 里读色值)会漂——K1 实施者需 grep `#6366f1|#f59e0b|#ef4444` 等十六进制硬编码并入报告(JS/内联 style 层的硬编码色不在 theme 覆盖内,发现多少处理多少,大的上报)。
