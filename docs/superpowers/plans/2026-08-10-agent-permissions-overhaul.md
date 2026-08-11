# Agent 权限页梳理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 权限页三柱重组（聊天与范围 / 创作能力 / 变更记录）+ 假开关下架 + 权限变更审计 + gate 拦截可见化。

**Architecture:** 前端只动 `PermissionsSection` 及其挂载点；审计走新表 `agent_permission_audits`（append-only，与 profile 更新同事务）；拦截可见化复用既有 transcript 事件流（`HookResult` 加 `abort_code` → `_run_pre_hooks` 落 `capability_denied` 事件 → 前端从 `useRunToolActivity` 分流渲染警示条）。

**Tech Stack:** FastAPI + SQLAlchemy async ORM、PostgreSQL（migration 取号见 Task 1）、React 19 + vitest、i18next。

**Spec:** `docs/superpowers/specs/2026-08-10-agent-permissions-overhaul-design.md`（已获批；§8 的开关→执行点对照表是 tooltip 文案事实来源）。

## Global Constraints

- UI 文本一律英文（Title Case），禁 emoji，图标用 lucide；语义色 token（ok/warn/danger/info/agent），**不得**新增 indigo/amber 等旧色相类（现文件里 Toggle 的 `bg-indigo-500` 是存量，本次重写时顺手换成 `bg-agent`）。
- i18n：en.json 与 zh.json 的 key 集合必须一致；key camelCase。
- 后端禁裸 SQL（`text()`）——唯一例外是 `run_recorder.record_event` 既有实现（本计划不改它的写法，只调用它）。
- 所有 BIGINT id 走字符串过 HTTP；`agent_permission_audits.id` 是 snowflake，`agent_id`/`changed_by` 是 UUID。
- 每个 task：RED 测试先行 → 实现 → GREEN → isort/black（后端）→ commit（中文 conventional commit）。
- 工作区：worktree `.worktrees/feat-agent-permissions-overhaul`，分支 `feat/agent-permissions-overhaul`（已基于 97309388 之后的 origin/master）。测试命令：后端 `cd backend && uv run pytest ...`，前端 `cd frontend && npx vitest run ...`。
- **不改动**：`high_risk_caps.py` 的 parser 语义、`TOOL_REQUIREMENTS` 表、`CapabilitiesIn` 的字段集合（deprecated 只加注释）、两个假开关的后端行为。
- spec 已拍板的偏离：审计端点 v1 只返回 `changed_by` uuid 字符串（前端显示前 8 位），display name 解析不做（spec §3 说 best-effort，实际 profiles join 无既有范式，YAGNI）。

---

### Task 1: 审计表 migration + ORM 模型

**Files:**
- Create: `supabase/migrations/420_agent_permission_audits.sql`（号以 fetch 复核为准）
- Modify: `backend/app/models/agents.py`（新 `AgentPermissionAudits` 类）
- Modify: `backend/app/models/__init__.py`（导出，按字母序插入——教训：'O'<'S' 这类 isort 顺序错误上个立项吃过 Critical）

**Interfaces:**
- Produces: ORM 类 `AgentPermissionAudits`（列：`id:int, agent_id:UUID, changed_by:UUID, before_json:dict, after_json:dict, reason:Optional[str], created_at:datetime`）。Task 2 直接 import。

- [ ] **Step 1: fetch 复核取号**

```bash
git fetch origin master && ls supabase/migrations/ | sort | tail -3
```

写计划时最新是 `419_episode_owner.sql`，预定取 **420**。若已被占则顺延，并同步改本文件与 SQL 文件名/头注释（只改本计划新增的引用）。

- [ ] **Step 2: 写 migration**

```sql
-- 420_agent_permission_audits.sql
--
-- Agent 权限变更审计（spec: docs/superpowers/specs/2026-08-10-agent-permissions-overhaul-design.md §3）。
-- 在此之前 capability_profile 的 PATCH 只有 logger.info 一行日志，无从回答
-- "谁在何时给哪个 agent 开了 write"。qm「查得清」柱子对照下唯一的实缺口。
--
-- append-only：不提供任何 UPDATE/DELETE 路径。快照存的是 parser 解析后的
-- 生效值（与 /agents 响应的 _with_resolved_permissions 同源），不是原始 JSONB
-- ——审计要记录"运行时会放行什么"。
--
-- RLS 不启用：后端内部表，不经 PostgREST，可见性由 API 层按
-- _can_edit_chat_permissions 同款闸门控制（与 script_shot_ops 同口径）。

CREATE TABLE IF NOT EXISTS public.agent_permission_audits (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  agent_id UUID NOT NULL REFERENCES public.ai_agents(id) ON DELETE CASCADE,
  changed_by UUID NOT NULL,
  before_json JSONB NOT NULL,
  after_json JSONB NOT NULL,
  reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_permission_audits_agent
  ON public.agent_permission_audits (agent_id, created_at DESC);
```

- [ ] **Step 3: ORM 模型**

`backend/app/models/agents.py`（放在文件中与 AiAgents 相邻处，风格照抄同文件既有类；import 区确认 `Uuid`/`JSONB`/`Index`/`ForeignKeyConstraint` 已有，缺则补）：

```python
class AgentPermissionAudits(Base):
    __tablename__ = "agent_permission_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_permission_audits_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_permission_audits_pkey"),
        Index("idx_agent_permission_audits_agent", "agent_id", "created_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

`models/__init__.py`：import 列表与 `__all__` 各加 `AgentPermissionAudits`（字母序）。

- [ ] **Step 4: 验证**

```bash
cd backend && uv run python -c "from app.models import AgentPermissionAudits; print(AgentPermissionAudits.__tablename__)"
uv run isort --check-only app/models/__init__.py && uv run black --check app/models/
uv run pytest tests/db/test_schema_drift.py -q 2>/dev/null || true   # 本地无 ephemeral 库则跳过,CI 会跑
```

Expected: 打印表名；isort/black 干净。**FK 教训**：migration 里的 FK 必须同步declare 在 `__table_args__`（schema-drift 零容忍 FK diff——上个立项因漏声明吃过 Critical）。

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/*agent_permission_audits.sql backend/app/models/
git commit -m "feat(db): agent 权限变更审计表 — append-only,快照存生效值 (mig 420)"
```

---

### Task 2: 审计写入 + 读取端点 + reason 字段

**Files:**
- Modify: `backend/app/schemas/ai_library.py`（`AgentUpdate.permission_change_reason` + `PermissionAuditItem` + `CapabilitiesIn` deprecated 注释）
- Modify: `backend/app/repositories/agent_repository.py`（先读 `update_fields_versioned` 现实现，扩展可选参数；新增 `list_permission_audits`）
- Modify: `backend/app/api/ai_library_router.py`（update_agent 组装审计行；新 GET 端点）
- Test: `backend/tests/test_agent_permission_audits.py`（新建；风格照抄 `backend/tests/test_ai_library_agent_permissions.py` 的 direct-call + patched-repo 范式，先读它）

**Interfaces:**
- Consumes: Task 1 `AgentPermissionAudits`；既有 `agent_chat_caps()` / `high_risk_caps()` parser、`ChatPermissionsOut.from_caps` / `CapabilitiesOut.from_caps`、`_can_edit_chat_permissions`。
- Produces:
  - `AgentUpdate.permission_change_reason: Optional[str]`（max_length=500；不属于 content 字段——update_agent 的 `exclude` 集合要加它）
  - repo：`update_fields_versioned(..., permission_audit: Optional[dict] = None)`——audit dict 形如 `{"agent_id": UUID, "changed_by": UUID, "before_json": dict, "after_json": dict, "reason": Optional[str]}`，在**同一事务**内 `insert(AgentPermissionAudits)`（读完该方法现有事务结构后接入；若它内部是单个 `write_scope` 块则直接加一条 insert）
  - repo：`async def list_permission_audits(self, agent_id: UUID, *, limit: int = 20) -> list[dict]`（按 created_at DESC，行转 dict，id 转 str，datetime isoformat）
  - `GET /api/v1/ai-library/agents/{slug}/permission-audits` → `{"items": [PermissionAuditItem]}`，闸门 `_can_edit_chat_permissions`，非授权 403、agent 不存在 404
  - `PermissionAuditItem`：`id: str, changed_by: str, before: dict, after: dict, reason: Optional[str], created_at: datetime`

**实现要点：**
- 审计行的 before/after 用**生效值快照**（spec §3）：

```python
# update_agent 权限分支里（merged_profile 组装完之后）:
from app.services.ai.permissions.agent_chat_caps import agent_chat_caps
from app.services.ai.permissions.high_risk_caps import high_risk_caps

def _resolved_snapshot(profile: Any) -> dict:
    probe = {"capability_profile": profile}
    return {
        "chat": ChatPermissionsOut.from_caps(agent_chat_caps(probe)).model_dump(),
        "capabilities": CapabilitiesOut.from_caps(high_risk_caps(probe)).model_dump(),
    }

permission_audit = {
    "agent_id": agent_uuid,
    "changed_by": user_uuid,
    "before_json": _resolved_snapshot(existing_profile),
    "after_json": _resolved_snapshot(merged_profile),
    "reason": payload.permission_change_reason,
}
```

（`_resolved_snapshot` 作为模块级私有函数放 router；parser 只读 `capability_profile` 键，probe dict 足够——实现前用现有 parser 签名核一遍。）
- 只在 `payload.chat_permissions is not None or payload.capabilities is not None` 时产生审计行并传给 `update_fields_versioned`；现有的两条 `logger.info`（router:834-841）保留。
- 既有半成品 `chat_audit`/`caps_audit` 变量（router:752-784）如在改造后不再被 logger 之外使用，保留其 logger 用途即可，不强行删。
- `CapabilitiesIn.delete` / `.external_publish` 字段加注释：`# DEPRECATED-UI (2026-08-10 spec §4): 无工具消费,UI 已下架;字段保留仅为向后兼容,勿删(extra="forbid" 下删字段会 422 旧客户端)。`

- [ ] **Step 1: RED 测试**

`backend/tests/test_agent_permission_audits.py` 用例（direct-call + MagicMock repo）：

```python
# 1) PATCH capabilities → update_fields_versioned 被调用时携带 permission_audit,
#    其 before_json/after_json 是 resolved shape(含 chat+capabilities 两个子树、
#    write_level 键存在),reason 透传。
# 2) PATCH 仅 content 字段(如 name)→ permission_audit 为 None(不落审计)。
# 3) GET permission-audits:非授权(_can_edit_chat_permissions=False)→403;
#    agent 不存在→404;正常→items 列表,id/changed_by 为 str。
# 4) AgentUpdate 接受 permission_change_reason 且它不进 content updates
#    (update_fields_versioned 的 updates 参数里没有该键)。
```

- [ ] **Step 2: RED 确认**

```bash
cd backend && uv run pytest tests/test_agent_permission_audits.py -v
```

- [ ] **Step 3: 实现**（schemas → repo → router，按 Interfaces 块）

- [ ] **Step 4: GREEN + 回归**

```bash
cd backend && uv run pytest tests/test_agent_permission_audits.py tests/test_ai_library_agent_permissions.py -q
uv run isort --check-only <touched> && uv run black --check <touched>
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(ai): 权限变更审计 — PATCH 同事务落 agent_permission_audits + 只读审计端点"
```

---

### Task 3: 权限页三柱重组 + 假开关下架 + 边界化文案

**Files:**
- Modify: `frontend/components/AILibrary/PermissionsSection.tsx`（重写主体）
- Modify: `frontend/public/locales/en.json` + `zh.json`（`aiLibrary.permissions.*`）
- Test: `frontend/components/AILibrary/PermissionsSection.test.tsx`（先读现有用例再改造）

**Interfaces:**
- Consumes: 现有 props（`value/onChange/capabilities/onCapabilitiesChange`）**不变**——AgentEditor 挂载点零改动（变更记录组在 Task 4 另行挂载）。
- Produces: 分组后的 DOM 带 `data-testid="perm-group-scope" / "perm-group-capabilities"` 两个组容器（Task 4 加第三组）。

**实现要点（对照 spec §2）：**
1. 组① Chat & Scope：`enableChat`、`autoBroadcast`、`readTeamFiles`、`crossEpisodeRead`（从能力组移入；仍写 `capabilities.cross_episode_read`，只是 UI 归组）。组头新增 `scopeTitle`/`scopeIntro`。enabled=false 时的置灰包裹保持现行为（只包 chat 三项，`crossEpisodeRead` 不受 enabled 置灰——它不依赖聊天）。
2. 组② Creative Capabilities：`writeLevel` 四档、`generateImage`、`generateVideo`、`mediaCap`。**`mediaCap` 输入框在 `!mediaOn` 时加 `disabled` 属性**（现在只有视觉置灰、仍可编辑），desc 换成条件文案：`mediaOn ? mediaCapDesc : mediaCapDisabledHint`。
3. **删除 `deleteCap` 与 `externalPublish` 两个 Toggle 的 JSX**（连同其 i18n key）。
4. Toggle 组件的 `bg-indigo-500` → `bg-agent`（顺手清存量旧色相类，一行）。
5. i18n（en 为准，zh 同步中文值）——**删** `deleteCap/deleteCapDesc/externalPublish/externalPublishDesc`；**改**（边界化文案，spec §2-C，事实来源 spec §8 对照表）：

```json
"writeLevelDesc": "How far the agent may go when it wants to change a script. Tools outside the granted tier are hidden from the agent AND blocked at dispatch — hiding alone is never the guard.",
"readTeamFilesDesc": "When off, the ResourceFetch tool is neither offered to the agent nor honored if called.",
"crossEpisodeReadDesc": "Allow reading episodes beyond the one this run was given, for continuity. Scope can widen to the project, never beyond it.",
"mediaCapDesc": "Ceiling on how many images or videos the agent may produce in a single turn. Enforced per call at dispatch.",
```

**新增**：

```json
"scopeTitle": "Chat & Scope",
"scopeIntro": "Where this agent may act and what it may see. All off by default.",
"mediaCapDisabledHint": "Enable image or video generation first — the ceiling has no effect until one is granted."
```

（`capabilitiesIntro` 里删去 "or publish outside" 措辞——发布能力已下架。）

- [ ] **Step 1: 读现有测试与组件 → 写 RED 测试**

改造 `PermissionsSection.test.tsx`：
```tsx
// 1) 渲染两个组容器 testid;crossEpisodeRead 的 toggle 位于 perm-group-scope 内。
// 2) deleteCap/externalPublish 的文案与 toggle 不再渲染(getByText 断言 absent)。
// 3) media 全关时 #media-cap 有 disabled 属性;开 image 后可编辑。
// 4) 既有交互用例(toggle onChange/writeLevel onSelect/clamp)保持通过(按新结构微调 selector)。
```

- [ ] **Step 2: RED 确认** `cd frontend && npx vitest run components/AILibrary/PermissionsSection.test.tsx`

- [ ] **Step 3: 实现重组**（按要点 1-5；保持现文件的 Toggle/WriteLevelPicker 子组件结构，只调整排列与分组容器）

- [ ] **Step 4: GREEN + i18n 齐平校验**

```bash
npx vitest run components/AILibrary/ && npx tsc --noEmit
node -e "const e=require('./public/locales/en.json').aiLibrary.permissions,z=require('./public/locales/zh.json').aiLibrary.permissions;const ek=Object.keys(e).sort(),zk=Object.keys(z).sort();if(JSON.stringify(ek)!==JSON.stringify(zk)){console.error('MISMATCH',ek.filter(k=>!zk.includes(k)),zk.filter(k=>!ek.includes(k)));process.exit(1)}console.log('i18n keys aligned:',ek.length)"
```

- [ ] **Step 5: Commit** `git commit -m "feat(fe): 权限页三柱重组 — 聊天与范围/创作能力分组 + 假开关下架 + 边界化文案"`

---

### Task 4: 变更记录组（Change Log）

**Files:**
- Modify: `frontend/types.ts`（`AgentPermissionAudit` 接口）
- Modify: `frontend/services/aiLibraryService.ts`（`getPermissionAudits`；`updateAgentPermissions` 透传 reason）
- Create: `frontend/components/AILibrary/PermissionChangeLog.tsx`
- Modify: `frontend/components/AILibrary/AgentEditor.tsx`（permissions tab 挂第三组 + reason 输入接线）
- Test: `frontend/components/AILibrary/PermissionChangeLog.test.tsx`
- Modify: en.json/zh.json（新 key）

**Interfaces:**
- Consumes: Task 2 端点 `GET /agents/{slug}/permission-audits` → `{items: [{id, changed_by, before, after, reason, created_at}]}`；`AgentUpdate.permission_change_reason`。
- Produces:

```ts
// types.ts
export interface AgentPermissionAudit {
  id: string;
  changed_by: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reason: string | null;
  created_at: string;
}
// aiLibraryService
async getPermissionAudits(slug: string): Promise<{ items: AgentPermissionAudit[] }>
// updateAgentPermissions 第二参扩展:
perms: { chat_permissions?; capabilities?; permission_change_reason?: string }
// 组件
<PermissionChangeLog audits={AgentPermissionAudit[]} loading={boolean} />
```

**实现要点：**
- `PermissionChangeLog`：只读列表。每行：`created_at`（toLocaleString）· `changed_by.slice(0, 8)` · 变更摘要 · reason（有则显示）。变更摘要 = 对比 before/after 两个平铺后的 dict，列出变了的 key（`flattenDiff(before, after) → ["capabilities.write_level: none → write", ...]`，写成组件内纯函数并导出供测试）。空态文案 `changeLogEmpty`。
- AgentEditor：permissions tab 内容区在 `<PermissionsSection .../>` 之后渲染 reason 输入 + `<PermissionChangeLog/>`；audits 在 tab 首次激活时拉取（仿既有 skills 懒加载模式），`savePermissions` 成功后重新拉取；`savePermissions` 的 PATCH body 加 `permission_change_reason: permReason || undefined`，成功后清空输入。403（非 owner 看不了审计）时静默不渲染该组（`catch (err) { console.error(...) }` + 空态不展示）。
- i18n 新 key：

```json
"changeLogTitle": "Change Log",
"changeLogIntro": "Recent permission changes to this agent. Records are append-only.",
"changeLogEmpty": "No permission changes recorded yet.",
"changeReasonLabel": "Reason for change (optional)",
"changeReasonPlaceholder": "Why are you changing these permissions?"
```

- [ ] **Step 1: RED 测试**（`PermissionChangeLog.test.tsx`：空态/行渲染/`flattenDiff` 纯函数三档——值变、新增键、无变更返回空数组）
- [ ] **Step 2: RED 确认**
- [ ] **Step 3: 实现**（types → service → 组件 → AgentEditor 接线 → i18n）
- [ ] **Step 4: GREEN**：`npx vitest run components/AILibrary/ && npx tsc --noEmit` + Task 3 的 i18n 齐平脚本
- [ ] **Step 5: Commit** `git commit -m "feat(fe): 权限变更记录组 — 审计列表 + 可选变更理由(qm purpose 借鉴)"`

---

### Task 5: 拦截可见化（后端）

**Files:**
- Modify: `backend/app/services/infra/hooks/__init__.py`（`HookResult.abort_code`）
- Modify: `backend/app/services/infra/hooks/high_risk_capability_gate.py`（`_deny` 带 code）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（`_run_pre_hooks` abort 时落事件 + 去重）
- Test: `backend/tests/test_capability_denied_event.py`（新建；gate 既有测试文件先 grep `high_risk` 找到并保证不回归）

**Interfaces:**
- Consumes: `RunRecorder.record_event(event_type: str, payload: dict)`（既有，best-effort 永不 raise）；`HookContext.tool_name/agent_slug`。
- Produces: transcript 事件 `event_type="capability_denied"`，payload `{"tool": str, "reason": str}`（reason = abort_reason 全文，已含所需档位与现档位）。Task 6 消费。

**实现要点：**
1. `HookResult` 加字段（frozen dataclass，向后兼容）：

```python
    # 机器可读的 abort 分类(如 "capability_denied")。None = 未分类。
    # 供 AgentRunner 决定是否落用户可见的 transcript 事件——"触发路径必须
    # 类型化回显"纪律在权限域的落地(2026-08-10 spec §5)。
    abort_code: Optional[str] = None
```

2. `high_risk_capability_gate._deny` 的 `HookResult(...)` 加 `abort_code="capability_denied"`。
3. `AgentRunner._run_pre_hooks`：在 `if hook_result.decision in ("abort", "await_approval"): return hook_result` 之前插入：

```python
            if (
                hook_result.decision == "abort"
                and hook_result.abort_code == "capability_denied"
                and recorder is not None
                and hasattr(recorder, "record_event")
                and tool_name not in self._denied_tools_this_turn
            ):
                # 同一 turn 同一工具只落第一条,防模型重试刷屏(spec §5)。
                self._denied_tools_this_turn.add(tool_name)
                await recorder.record_event(
                    "capability_denied",
                    {"tool": tool_name, "reason": hook_result.abort_reason or ""},
                )
```

`self._denied_tools_this_turn: set[str]` 在 AgentRunner 的 turn 入口初始化（读 `run_turn`/streaming 入口，找 per-turn 状态初始化处，如 loop_guard 同位置；若 runner 实例即 per-turn 则在 `__init__`）。**先读代码确认 runner 实例生命周期再定位置，两处入口都要覆盖。**

- [ ] **Step 1: RED 测试**

```python
# test_capability_denied_event.py(fake recorder 记录 record_event 调用):
# 1) gate 拒绝(write_level none 调 CreateShot)→ recorder 收到一条
#    ("capability_denied", {"tool": "CreateShot", "reason": <含 "write">})。
# 2) 同 turn 第二次同工具拒绝 → 不再落第二条。
# 3) 非 capability 类 abort(手写一个 abort_code=None 的假 hook)→ 不落事件。
# 4) recorder=None → 不抛。
```

- [ ] **Step 2: RED 确认** `uv run pytest tests/test_capability_denied_event.py -v`
- [ ] **Step 3: 实现**（按要点 1-3）
- [ ] **Step 4: GREEN + gate/runner 既有测试回归**

```bash
uv run pytest tests/test_capability_denied_event.py -q
uv run pytest tests/ -q -k "high_risk or capability or runner" 
```

- [ ] **Step 5: Commit** `git commit -m "feat(ai): gate 拦截可见化 — capability_denied transcript 事件(每 turn 每工具一条)"`

---

### Task 6: 拦截可见化（前端警示条）

**Files:**
- Modify: `frontend/components/agentActivity/toolActivity.ts`（`denialsFromTranscriptEvents`）
- Modify: `frontend/components/agentActivity/useRunToolActivity.ts`（返回值加 `denials`）
- Create: `frontend/components/agentActivity/CapabilityDeniedNotice.tsx`
- Modify: `frontend/components/chat/AIChatBubble.tsx` + `frontend/components/Todolist/IssueChatThread.tsx`（渲染接线）
- Test: `frontend/components/agentActivity/CapabilityDeniedNotice.test.tsx` + `toolActivity.test.ts` 追加
- Modify: en.json/zh.json

**Interfaces:**
- Consumes: Task 5 的事件 shape `{event_type:"capability_denied", payload:{tool, reason}, seq}`；`AIChatBubble` 已有 `runId` prop（undo 立项加的）；`useRunToolActivity` 的既有缓存机制。
- Produces:

```ts
export interface CapabilityDenial { key: string; tool: string; reason: string }
export function denialsFromTranscriptEvents(events: AgentRunEvent[]): CapabilityDenial[]
// useRunToolActivity 返回值: { activities, denials: CapabilityDenial[], loaded }
// 组件: <CapabilityDeniedNotice denials={...} interactive={boolean} />
```

**实现要点：**
- `denialsFromTranscriptEvents`：过滤 `event_type === 'capability_denied'`，key=`seq:${ev.seq}` 去重，payload.tool/reason 缺失则跳过。
- `useRunToolActivity`：`fromTranscriptEvents` 调用旁并行产出 denials，settledCache 的值改为 `{activities, denials}`（内部结构变更，外部字段新增——`__clearRunToolActivityCache` 不变）。
- `CapabilityDeniedNotice`：danger/warn 语义色的窄条（lucide `ShieldAlert`），文案 `t('agentActivity.capabilityDenied', { tool })` + reason 原文（次级小字）。`interactive` 时右侧 CTA 链接 **`/settings?tab=ai`**（AI Library 设置根——per-agent 深链寻址今天不存在，见 spec §10 范围外；用 react-router 的 `Link` 或既有导航范式，先 grep AIChatBubble 所在树可用的导航方式，不可用则 `<a href>`）。非 interactive（issue timeline）纯文本无 CTA。
- 接线：`AIChatBubble` 内 `const { denials } = useRunToolActivity(runId, false)`——**允许**：头注释禁的是 tool_call 双渲染，denials 不在 in-hand trace 里，在调用处加一行注释说明；`denials.length>0` 时渲染于 SubTaskList 之后。`IssueChatThread` 的 `RunToolActivity` 组件同样从 hook 取 denials 渲染（`interactive={false}`）。
- i18n：

```json
"capabilityDenied": "Agent tried {{tool}} — blocked by permissions",
"capabilityDeniedCta": "Open AI Library"
```

- [ ] **Step 1: RED 测试**（`denialsFromTranscriptEvents` 三档：正常/缺字段跳过/按 seq 去重；组件两态：interactive 带 CTA、非 interactive 无 CTA；AIChatBubble 有 denials 时渲染 notice——mock hook）
- [ ] **Step 2: RED 确认** `npx vitest run components/agentActivity/`
- [ ] **Step 3: 实现**
- [ ] **Step 4: GREEN + 全量**：`npx vitest run && npx tsc --noEmit`（tsc 只要求不新增错误）
- [ ] **Step 5: Commit** `git commit -m "feat(fe): 拦截可见化 — capability_denied 警示条 + Open AI Library CTA"`

---

## 收尾

- [ ] 全量基线：`cd backend && uv run pytest -q`；`cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
- [ ] 推分支开 PR（base master）。PR 描述：spec 路径 + 六决策表 + 「migration 与后端部署无顺序保证」提示（本期新表只被新端点读写，窗口内仅审计端点 500，无静默降级面）+ 验收步骤（改一次权限 → Change Log 出现一行；用一个 write_level=none 的 agent 触发 CreateShot → 聊天流出现警示条）。
- [ ] merge 后按 PR 验收步骤在生产走一遍（可用 Claude 调试账号）。

## Self-Review 记录

- spec §2（三柱+置灰+下架+边界文案）→ Task 3；§3（审计表/同事务/生效值快照/reason/端点/闸门）→ Task 1+2；§4（schema 保留+deprecated 注释）→ Task 2；§5（拦截可见化/去重/非交互降级）→ Task 5+6；§6 纪律与 §7 路线图为文档性内容（已在 spec，无代码任务）；§9 测试口径分布于各 task。
- 已拍板偏离：① changed_by 不解析 display name（v1 显示 uuid 前 8 位）；② 警示条 CTA 链到 AI Library 设置根而非 per-agent 权限 tab（深链寻址不存在，spec §10 已列范围外）。
- 类型一致性：`AgentPermissionAudits`/`permission_audit` dict/`PermissionAuditItem`/`AgentPermissionAudit`/`getPermissionAudits`/`denialsFromTranscriptEvents`/`CapabilityDenial` 在产出与消费 task 间逐一核对。
