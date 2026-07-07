# Import Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** spec v3 未做项清账：用户把已有剧本导入成 v2 编辑器的 scenes+elements。两条导入路：**Fountain 语法 → 确定性解析器**（零 LLM 成本零等待）；**纯文本/散文 → 既有 convert-to-scenes LLM 管线**（`script_scene_convert.py` 复用）。FDX（Final Draft XML）后置（YAGNI）。

**Architecture:** 后端纯函数 Fountain 解析器（穷举单测）产 `scenes[{heading, elements[]}]` → 逐场景 `ScriptSceneRepository.create_with_content`（scene+genesis op 单事务，P1 既有）；import workflow（DBOS，路线 C）编排「建 script → 解析/或转交 LLM 管线 → 落场景」；前端入口=scripts 列表 Import 按钮 + 编辑器冷启动屏第三入口，modal 支持粘贴文本或上传 .fountain/.txt，task 轮询完成后跳编辑器。

**用户裁决：**「挨个」自主执行链内（2026-07-07）。

## Global Constraints

全部既有约束继承。特别：task_type=`script_import`（13≤20 ✓）；**workflow import 进 `_dispatch_bundle.py`+pin 测试**（#1055 血泪）；失败 raise 路线 C；解析器对不可信输入健壮（超长行/空文件/非 UTF-8→结构化 422）；上传文件大小上限（1MB 文本足够）；i18n en/zh 全等。

## File Structure

```
backend/app/services/script/fountain_parser.py    (new: 纯函数解析器)
backend/app/workflows/script_import.py            (new: import workflow)
backend/app/workflows/_dispatch_bundle.py         (modify: import+pin)
backend/app/api/script_import_router.py           (new: POST /scripts/import + 注册)
backend/tests/test_fountain_parser.py             (new: 穷举)
backend/tests/test_script_import.py               (new: wiring+workflow)
frontend/editor/importService.ts                  (new: API+轮询)
frontend/components/.../ImportScriptModal.tsx     (new: 粘贴/上传 modal——落点看 scripts 列表组件归属)
frontend/editor/components/EditorShell.tsx        (modify: 冷启动屏第三入口)
frontend/public/locales/{en,zh}.json              (modify)
```

**PR 切分：PR-I1** = Task 1-2（后端全量）；**PR-I2** = Task 3（前端入口+modal+轮询）。

---

### Task 1: Fountain 解析器（纯函数，先行穷举）

`parse_fountain(text: str) -> list[SceneDraft]`，`SceneDraft = {heading_int_ext, location_text, time_of_day, elements: [{type, text, character?}]}`：
- scene heading：行首 `INT./EXT./EST./INT/EXT/I/E.`（大小写不敏感，`.` 或空格后跟地点）+ ` - TIME` 后缀拆 time_of_day；无 heading 前的内容归入首个默认场景
- character cue：全大写行（≤40 字符，允许 `(V.O.)/(O.S.)` 后缀）且下一非空行存在 → 后续行为 dialogue；`(括注)` 行→parenthetical
- transition：全大写以 `TO:` 结尾 → transition
- 其余非空行 → action；连续空行分段
- 健壮性：CRLF 归一/BOM 剥离/超 1MB 拒绝/纯空 → 空列表
- element type 映射到编辑器 8 元素协议（scene/action/character/parenthetical/dialogue/transition——以 `scene_ops.py`/前端 elements 实际 type 枚举为准先核）
- 测试：每规则正反例 + 混合完整剧本样例 + 边界（连续 cue/无对白 cue 回落 action/heading 大小写）
- Commit `feat(script): fountain parser — deterministic screenplay import core`

### Task 2: import workflow + 端点

- `script_import.py`：入参 script_id+mode(`fountain`|`prose`)+content；fountain→parse+逐场景 create_with_content；prose→复用 `script_scene_convert` 的既有步骤链（先建 chapter 挂散文再走 LLM 拆场——读该 workflow 后决定是调用其步骤还是 dispatch 它，倾向直接复用步骤函数）；完成 task_tracking 走 manager API；失败 raise
- `POST /api/v1/scripts/import`：body {project_id, name, mode, content}；verify project 访问（照 scripts create 的守卫口径）→ 建 script（复用既有 create 服务）→ dispatch workflow → 返回 {script_id, task_id}
- content ≤1MB 422 闸；task_type=script_import；dispatch bundle+pin
- 测试：wiring（403/422/dispatch 断言）+ workflow 步骤单测（fake repo）
- Commit `feat(script): import workflow + endpoint — fountain and prose paths`

**→ PR-I1 ship**（终审重点：解析器 ReDoS 面/element type 映射正确性/prose 复用不破坏既有 convert）。

### Task 3: 前端入口 + modal

- scripts 列表（Projects→脚本 tab）头部 `Import Script` 按钮 + 编辑器冷启动屏（Create Story 旁）第三入口
- Modal：名字输入 + 两 tab（Paste text / Upload .fountain|.txt≤1MB）+ mode 自动判定（含 INT./EXT. heading ≥2 处→fountain，否则 prose，用户可覆盖）+ 提交 → task 轮询（复用既有 task 轮询范式）→ 完成 navigate 编辑器；失败 toast+可重试
- i18n/零 emoji/双主题；测试：mode 判定纯函数穷举 + modal 提交链 + 轮询跳转
- Commit `feat(editor): import script UI — paste or upload, fountain auto-detect`

**→ PR-I2 ship** → 真机 E2E（贴一段真 Fountain 样本→场景上屏；贴散文→LLM 拆场）→ memory。

## Self-Review 已做
- 两条路覆盖有格式/无格式全谱；FDX 后置决策记录；解析器纯函数可穷举；复用 create_with_content/convert 管线零重复造轮
- task_type 长度/dispatch bundle/路线 C/上传上限全排查
