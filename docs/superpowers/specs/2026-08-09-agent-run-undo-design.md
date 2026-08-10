# Agent Run 撤销（Run-level Undo）设计

2026-08-09 · 前置背景：三视图 + Agent 面板五件套（PR #1755 / #1757 / #1758）已全部上线，
设计稿只剩「撤销」一件。`TurnWriteSummary.tsx` 头部注释记录了当时不能做的原因：
shot 写入无归属、无账本、无 run 级逆操作。本 spec 补齐这三块并接上前端按钮。

拍板决策（brainstorm 结论）：

| 决策点 | 结论 |
|--------|------|
| 撤销粒度 | **整 run 一键撤销**（shot 卡 + scene 正文编辑一起），不做单卡撤销 |
| 冲突策略 | **跳过 + 类型化报告**：被后续修改碰过的写入不动，永不销毁别人的工作 |
| 记账机制 | **新表 `script_shot_ops`**，不侵入 `script_ops` 的 watermark 语义 |

## 1. 现状盘点（为什么是这三块）

- **scene 正文已「半可撤销」**：agent 的 `EditSceneElements` 走 `apply_element_ops`，
  每笔进 `script_ops` 账本，`actor='agent:<run_id>'`，自带 `inverse`。缺的只是
  「选择性撤销某 run 的 ops」的逻辑——现有 `inverse_between` 是连续区间回滚
  （commit rollback 用），不能跳过中间别人的 ops。
- **shot 卡是真空地带**：`scoped_script_gateway.create_shot` / `update_shot` 直写 ORM，
  无归属列、无账本、无 before-image。
- **无 run 级逆操作端点**：run 端点族在 `ai_library_router` 的 `/runs/{run_id}/*`
  （live / events / children / cancel），undo 挂同族。

## 2. 数据模型（migration 417）

> 取号注意：当前最新已有一对 412 撞号（`412_retire_canvas_stage_node` /
> `412_social_accounts_realtime`），本迁移取 413；落地前再 fetch 复核。

### 2.1 `script_shots.created_by_agent_run_id`

```sql
ALTER TABLE public.script_shots
  ADD COLUMN created_by_agent_run_id BIGINT NULL
  REFERENCES public.agent_runs(id) ON DELETE SET NULL;
```

> `agent_runs.id` 是 BIGINT snowflake（mig 232 后），不是 UUID。

- 只在 agent `create_shot` 时写；人写 / Auto-Storyboard 的卡保持 NULL。
- 本期仅供撤销与后续 UI 使用；storyboard 卡上的归属 badge **不在本期**。

### 2.2 新表 `script_shot_ops`（shot 写入账本）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGINT snowflake PK | `generate_snowflake_id()` |
| `run_id` | BIGINT NOT NULL | FK → `agent_runs(id) ON DELETE CASCADE` |
| `shot_id` | BIGINT NOT NULL | FK → `script_shots(id) ON DELETE CASCADE` |
| `scene_id` | BIGINT NOT NULL | 冗余，供 scene 级查询 |
| `action` | TEXT NOT NULL | CHECK (`'create'` / `'update'`) |
| `before_json` | JSONB NULL | update 时旧值快照（仅本次涉及字段）；create 为 NULL |
| `after_json` | JSONB NOT NULL | 写完后的字段快照（`_WRITABLE_SHOT_FIELDS` 子集） |
| `created_at` | TIMESTAMPTZ NOT NULL | `now()` |

索引：`(run_id)`。

与 `script_ops` **平行、互不侵入**：`script_ops.op_seq == content_version` 的
watermark 语义是 P1 版本控制（commit / diff / rollback）的核心不变量，shot 写入
不碰 `content_version`，塞进去会破坏全链。

### 2.3 `agent_runs.undone_at`

```sql
ALTER TABLE public.agent_runs ADD COLUMN undone_at TIMESTAMPTZ NULL;
```

run 级一次性撤销标记，驱动前端按钮态与端点幂等。

## 3. 记账写入（`scoped_script_gateway`）

- **`create_shot`**：同事务追加账本行（`action='create'`，`after_json` = 写入字段快照）
  并落 `created_by_agent_run_id = scope.run_id`。
- **`update_shot`**：事务内 `SELECT ... FOR UPDATE` 先读旧值再 UPDATE；
  `before_json` = 旧值、`after_json` = 新值，均只含本次涉及字段。
- 账本 INSERT 用本文件既有的裸 ORM 写法（`insert(ScriptShotOps)`），不新增
  repository 调用——`test_scoped_script_gateway_calls_only_the_ops_channel` 的
  AST 白名单（只许 `apply_element_ops`）**不需松绑**；
  `test_scoped_script_gateway_takes_only_resolved_handles` 同样不受影响
  （不新增公开函数签名）。
- scene 正文侧**零改动**：账本与 actor 早已就位。

## 4. 撤销端点

```
POST /api/v1/ai-library/runs/{run_id}/undo
```

语义 = 整 run、逐项 CAS、跳过 + 类型化报告。

### 4.1 前置

- **权限**：run 属于当前用户（`agent_runs.user_id == 当前用户`）。
- **幂等**：`undone_at` 已设 → 返回 `already_undone`，不重复执行；
  执行完成（无论跳过多少项）设 `undone_at`。一次性，无 redo。

### 4.2 shot 的逆操作

按账本 `run_id` 取全部行，同一 `shot_id` 多行时按时间**逆序**归并：

- **create 的逆 = 删卡**，仅当三个条件同时成立（写进 `DELETE ... WHERE` 做 CAS，
  与并发编辑天然互斥）：
  1. 当前 writable 字段 == `after_json`（字节比对，不用时间戳启发式）；
  2. `status = 'empty'`；
  3. `image_url` / `thumbnail_url` / `video_url` 均为空——被渲染过的卡视为
     「被动过」，跳过，不销毁生成成果。
- **update 的逆 = 恢复 `before_json`**，仅当当前涉及字段 == `after_json`
  （条件 UPDATE CAS）。同一卡多次 update：比对用**最后一行**的 `after_json`，
  恢复用**第一行**的 `before_json`（回到 run 前状态）。
- 同一卡本 run 内先 create 后 update：归并后按 create 处理——比对条件用
  **最后一行**的 `after_json`，满足即整卡删除。
- **B4 回流**：删 empty 卡可能让「本集全部 done」的 storyboard 判据**变真**，
  删卡后必须走 `fire_surface_sync_for_shot` 同族回流（`set_shot_status` 的既有教训；
  `fire_*` 永不 raise）。

### 4.3 scene 正文的逆操作

1. 取 `actor = 'agent:<run_id>'` 的 `script_ops` 行（按 scene 分组）。
2. 对每个被碰过的 `element_id`：若存在**更高 `op_seq`、其他 actor** 的 op 碰过
   同一 element → 该 element 跳过（`edited_after_run`）。
3. 其余按 `op_seq` 降序取各行 `inverse` 中属于未跳过 element 的 op，
   通过 `apply_element_ops` 以 **`actor='undo:<run_id>'`** 作为**新 forward op**
   应用——沿用 rollback 范式，历史保持 append-only、可再回滚，编辑器 history
   面板可识别这不是用户手打的。
4. `VersionConflict` → 该 scene 整体跳过并报告（不重试，与 agent 写入路径
   「at most once」一致）。

### 4.4 返回（类型化报告）

「触发路径必须类型化回显」纪律——silent no-op 不可接受：

```json
{
  "status": "done" | "already_undone",
  "shots_deleted": 3,
  "shots_reverted": 1,
  "scene_elements_reverted": 4,
  "skipped": [
    {"kind": "shot", "id": "…", "reason": "edited_after_run"},
    {"kind": "shot", "id": "…", "reason": "rendered"},
    {"kind": "scene", "id": "…", "reason": "version_conflict"},
    {"kind": "scene_element", "id": "…", "reason": "edited_after_run"}
  ]
}
```

`reason` 枚举：`edited_after_run` / `rendered` / `version_conflict`。

## 5. 前端（TurnWriteSummary）

- `interactive` 且拿得到 `run_id` 且 run 未 undone 时，摘要头部行尾加 **Undo**
  按钮（lucide `Undo2`，英文 UI，无 emoji）；undone 后变禁用态 **Undone**。
- 点击 → 调端点 → 就地渲染报告：undone 计数 + `skipped` 逐项带 reason 文案，
  并触发 storyboard 数据刷新。
- `undone_at` 随 run 数据进入 `useRunToolActivity` 数据流。
- 无编辑器的 surface（issue timeline，`interactive=false`）沿用现状：不渲染按钮
  ——no affordance beats a dead one。

## 6. 测试

- **记账单测**：create/update 的 before/after 正确性、同事务性（写入失败不留账）。
- **选择性 inverse 纯逻辑单测**：foreign-actor-after → skip；降序归并正确。
- **端点集成测**：幂等（二次调用 `already_undone`）、权限（他人 run 403/404）、
  三类 skip 各一例、报告 shape、CAS 并发（条件不满足即跳过不误删）。
- **前端组件测**：按钮三态（可撤 / 已撤 / 不可交互）、报告渲染、i18n key。

## 7. 范围外（YAGNI）

- redo / 撤销的撤销
- 单卡撤销（storyboard 上直接删卡/改字已覆盖）
- 预检确认弹窗（先跳过+报告，不满意再迭代）
- storyboard 卡片上的 agent 归属 badge（列已落，UI 另立项）
- 三个顺手小尾巴不进本 spec，作为实施计划末尾的独立附带任务：
  strip 非当前节点点击闪面板的既有耦合、场次卡 Open 深链到 scene 级
  （接 shotFocusBus）、结构化上下文 payload（§5.3）
