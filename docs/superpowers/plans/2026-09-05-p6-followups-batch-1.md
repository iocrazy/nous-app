# P6 后续小票批次 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收掉 P6 披露的三张小票：schema-drift 集成 step 的 full-skip 守卫补齐到全部 step；Generated 收件箱卡片补 audio 分支；「As Asset」文案统一。

**Architecture:** 三项互相独立，各一个提交。守卫抽成一段可复用 shell（同一份文本、每个 step 调用），用本地无 DSN / 部分 skip / 有 DSN 三态自证；收件箱 audio 分支复用现有 `media_kind` 判定与 P6 对话框的 `coverFallbackIcon` 思路；文案统一以计划裁决「As Asset」为准，改 `generated.action.saveAsAsset` 两种 locale 与所有断言。

**Tech Stack:** GitHub Actions (self-hosted + hosted), pytest, React 19 + vitest/RTL + Playwright mocked。

**Spec:** 无独立 spec；来源 = PR #2107 已知清单 + `.superpowers/sdd/2026-09-04-asset-library-p6-cleanup/task-2-report.md`（守卫原型）与 `task-4-report.md`（audio 卡片）。

## 裁决

| # | 裁决 |
|---|---|
| A | 守卫判据：`passed_lines==0` 才红；部分 skip 且有 passed 为绿；抽成一处（step 内 `run:` 复用同一 heredoc 文本或 `.github/scripts/pytest-no-full-skip.sh`），八个 step 全部接入；用 actionlint 与本地三态实测证明 |
| B | 收件箱 audio 卡：`media_kind='audio'` 渲染音频占位（lucide `AudioLines`/`Music` + 文件名 + 时长若有）；Type chip 加 Audio；不做播放器（另票） |
| C | 文案统一为 **As Asset**（Title Case）：`generated.action.saveAsAsset` en/zh、Output 节点、右键均一致；相关测试/e2e 断言同步；zh 用「存为资产」 |

## Global Constraints
- 实施者/审查者 opus；UI Title Case、禁 emoji、语义 token；i18n parity + referenced-keys；vitest 禁 `--reporter=basic`；tsc 基线 fresh；禁 `git add -A`；`frontend/node_modules` 是符号链接禁 `npm ci`；改 workflow 先本地 actionlint。

---

### Task 1: schema-drift 八个集成 step 的 full-skip 守卫
**Files:** Modify `.github/workflows/schema-drift.yml`；Create `.github/scripts/pytest-no-full-skip.sh`（若选脚本方案）；证明记录进报告。
- [ ] 提交 `ci(schema-drift): every integration step refuses a full-skip run`

### Task 2: Generated 收件箱 audio 分支
**Files:** Modify `frontend/components/resources/generated/GeneratedCard.tsx:111`、`GeneratedView.tsx:1128-1132`、`generatedFilters.ts`（Type chip）；Test 各对应 + i18n。
- [ ] 提交 `feat(generated): audio rows render an audio card and filter`

### Task 3: 「As Asset」文案统一
**Files:** Modify `frontend/public/locales/{en,zh}.json`（`generated.action.saveAsAsset`），受影响测试/e2e 断言。
- [ ] 提交 `chore(i18n): unify the save-as-asset entry label to As Asset`
