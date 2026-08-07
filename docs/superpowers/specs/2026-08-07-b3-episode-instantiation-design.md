# B3 — 剧集级工作流实例化 设计

日期:2026-08-07 ｜ 状态:已确认(用户 2026-08-07 拍板三处决策)
上游 spec:[`2026-08-04-episode-level-workflow-design.md`](2026-08-04-episode-level-workflow-design.md) §9 第③期
前置:B1(数据层 mig 402 — `project_stage_nodes.episode_id` / `surface` / `episodes.current_node_id`)已上线;B2(推进机按集化 #1712)已上线;B5(UI #1720)已上线

## 0. 问题与目标

B1/B2/B5 都已上线**但休眠**:生产 `project_stage_nodes` 只有「个人项目测试 1」的 11 个节点、`episode_id` 全 NULL(走 B2 的逐字兼容旧项目级路径);该项目有 12 集。B2 的按集推进机(`list_nodes_by_episode` + `episodes.current_node_id`)已就位,只等节点的 `episode_id` 被填上、每集游标被设好。

**B3 = 点火钥匙**:把「一条项目级链」变成「每集一条链」。填 `episode_id`、冻结 `surface`(B1 已在实例化拷贝)、设每集游标、按集触发到达钩子。

**硬约束(来自 B2 终审)**:节点绑 episode 的写入必须 **all-or-nothing 原子**。半绑定态(部分节点有 `episode_id`、部分仍 NULL)会让 B2 autopilot 的 legacy 判别(看「是否存在 `episode_id` 非空节点」)翻到按集路径,而仍 NULL 的节点被按集查询静默排除 → 静默停摆。

## 1. 现状盘点(读到代码,非命名推断)

- **实例化**:`project_stage_nodes_repository.instantiate_from_template`(单 `write_scope()` 事务 + 每项目 `pg_advisory_xact_lock` + 幂等)只生成**一条项目级链**(`episode_id` 显式不设,注释明写留给 B3);`surface` 已冻结拷贝。
- **编排**:`services/workflow/instantiation.py::instantiate_project_workflow` 实例化后对**项目单个**首活跃组触发 mirror issues / folders / arrival notify / stage-hook / autopilot tick(全 best-effort)。
- **触发入口**:① `attach_project_workflow`(projects_router,`expect_fresh=True`→409)② `maybe_instantiate_project_workflow`(create-path,吞错)③ 剧集创建 `episode_repository.create`(`episodes_router` + `projects_service` 默认集)。
- **项目不存模板绑定**:`projects.workflow_id` 是 mig 047 死列,从不写;「项目是否有工作流」靠实例节点存在判断。节点带 `source_template_node_id` 可反查模板。
- **B2 shim**:`advance_service._load_scoped_nodes_and_cursor` / `_write_cursor` 按 `episode_id` 分叉,None=legacy 项目级、给定=按集读写 `episodes.current_node_id`。`get_active_group(project_id, episode_id)` 已支持按集。folder 命名 `_episode_scoped_folder_name` 已按集隔离。

## 2. 数据模型(mig 409 + 同批 ORM)

| 改动 | 说明 |
|---|---|
| `projects.workflow_template_id BIGINT NULL` | FK → `workflow_templates(id)` `ON DELETE SET NULL`。attach/create 时写入,新增剧集据此复用模板。模板被删则置空(新集不再自动挂链,可接受)。 |
| `projects.workflow_method TEXT NULL` | 记住 `live`/`ai`/`hybrid`,保证同项目各集实例化口径一致。CHECK 允许 NULL 或三值之一。 |

**纪律**:迁移与 ORM `mapped_column` + FK relationship 声明**同一 PR**(吸取 mig-404 教训:迁移建 FK 而模型只有裸列会被 schema-drift 守卫拦)。`ALTER ... ADD COLUMN IF NOT EXISTS` + CHECK 用 DROP IF EXISTS + ADD 幂等惯例(同 mig 402)。结尾 `NOTIFY pgrst`。

## 3. 按集实例化机制(核心)

- 把 `instantiate_from_template` 的**单节点克隆循环**(§342-468 planned→insert→members→deps)抽成共享私有 helper `_clone_template_chain(session, pid, tpl_nodes, ..., episode_id)`,现有项目级调用传 `episode_id=None` 逐字不变。
- 新增 `instantiate_episode_chains(project_id, template_id, episode_ids, *, method, overrides, expect_fresh)`:
  - **单个 `write_scope()` 事务** + 每项目 advisory lock;
  - 幂等:已有任意节点的项目按 `expect_fresh` 语义(True→`WorkflowAlreadyInstantiated`,False→返回现有);
  - 对每个 `episode_id` 调 `_clone_template_chain(..., episode_id=eid)`,`surface`/`events`/`completion_policy`/`form_schema` 冻结拷贝;
  - **原子**:所有集的链在同一事务内落库,任一集失败整体回滚 → 永不留混合态;
  - 返回 `{episode_id: [nodes...]}`,供编排层按集设游标 + 触发钩子。
- 新增单集入口 `instantiate_single_episode_chain(project_id, episode_id)`:读项目的 `workflow_template_id`/`workflow_method`,为一集补链(新增剧集路径用)。

## 4. 每集游标 + 到达钩子按集触发

`instantiate_project_workflow` 改造为按集扇出:
- 写项目模板绑定(`workflow_template_id` + `workflow_method`);
- 扇出到项目所有现存 episode(拿 `episode_repository.list_by_project` 或等价);
- **对每一集**:算该集首活跃组(`_first_active_group` 复用,作用于该集节点子集)→ `episode_repository.set_current_node_id(eid, head)` → 触发 mirror issues / folders / arrival notify / stage-hook / autopilot tick,全部 best-effort(沿用「hiccup 绝不失败创建」纪律);
- 无 episode 的项目:只写模板绑定、不建链(留给首个新集)。

## 5. 触发入口接线(3 处)

1. **`attach_project_workflow`**:写绑定 + 扇出到所有现存 episode。`expect_fresh=True` 保留(竞态→409)。
2. **`maybe_instantiate_project_workflow`(create-path)**:同上,扇到 create 时的默认集。
3. **剧集新建**:`episode_repository.create` 的调用方,新集建成后若项目有 `workflow_template_id`,调 `instantiate_single_episode_chain` + 该集钩子。**best-effort,绝不失败建集**(与「触发路径类型化失败回显」同族:失败要日志可见)。**排序防重**:create-project 默认集由入口 2 覆盖;入口 3 只对「工作流绑定已存在后」新建的集触发(据 `workflow_template_id` 判断,幂等跳过已有节点的集)。

## 6. 生产点火 backfill(admin 端点,一次性)

目标:「个人项目测试 1」11 个 NULL 节点 → 12 集各一条链。

- **做成 app 层幂等操作**,非纯 SQL 迁移。两个理由:① `run-migration.yml` 与 `deploy-gpu.yml` 无顺序保证(CLAUDE.md 明列),纯 SQL 假设新代码在场有风险;② mirror issues 生成在 Python,SQL 复刻脆弱。
- 端点 `POST /projects/{id}/workflow/reinstantiate-per-episode`(admin/owner 守卫,复用 `verify_project_write_access`):
  - **单事务**:删该项目所有 `episode_id IS NULL` 的节点(连带关掉其 mirror issues,复用现有关闭链路)→ 按 12 集扇出建链 → 设每集游标 → 写 `workflow_template_id` + `workflow_method`;
  - `workflow_method` 从现有节点 skip 态反推(Shooting/Canvas 是否 skipped → live/ai/hybrid);`workflow_template_id` 从现有节点 `source_template_node_id` 反查;
  - **删+重挂同事务 → 无混合态**(满足硬约束);
  - 幂等:已是按集态(存在 `episode_id` 非空节点)则 no-op 返回。
- 部署后用调试账号手动跑一次点火。**与 B6「遗留项目级节点清除」重叠,但点火本就要清旧链,归 B3 合理**。

## 7. 不做 / 边界

- 不做 B4(surface 自动完成判据接线,依赖 B3)。
- 不动交付物版本/审阅链路、agent 派发/needs_input、跨集依赖(B2 已有 #1721)。
- 不改 B2 legacy 路径(`episode_id=None`):任何残留 NULL 项目继续逐字兼容。
- 不写通用批量迁移脚本:生产只此一个项目,走一次性端点。

## 8. 测试

- **单元(TDD)**:
  - `instantiate_episode_chains` 每集填 `episode_id`、冻结 `surface`、设每集游标;
  - 扇出中途失败整体回滚(原子,无混合态);
  - 幂等(重复调用不翻倍);
  - 新集钩子(`instantiate_single_episode_chain` 据绑定补链);
  - backfill 把 legacy→按集,单事务无混合态,幂等 no-op。
- **回归**:
  - B2 `advance` 对 B3 实例化的节点按集正常推进(接上 #1712 的按集用例);
  - legacy 项目级路径(`episode_id=None`)行为逐字不变;
  - 删场景对镜头卡计数正确重算(spec §5②,一条回归即可)。

## 9. 分期实施(单 PR `feat/b3-episode-instantiation`,各 task 独立 commit)

1. mig 409 + ORM 两列(schema-drift 绿)
2. `_clone_template_chain` 抽取(项目级零回退) + `instantiate_episode_chains` + `instantiate_single_episode_chain`(TDD)
3. `instantiate_project_workflow` 按集扇出 + 每集钩子 + 3 处入口接线(TDD)
4. backfill admin 端点(TDD)
5. 回归 + PR

## 10. 自审记录(2026-08-07)

- **原子性**:所有节点绑定写在单事务(扇出 / backfill 删+重挂),满足 B2 硬约束;幂等靠既有 advisory lock + 存在性检查。
- **无命名推断**:`instantiate_from_template` 的 surface 冻结、B2 shim 的按集读写、folder 按集命名、B1 的三列 schema 均已读到代码/迁移原文确认。
- **method 反推的脆弱点**:仅用于 backfill 一个已知项目;反推错顶多影响该项目 Shooting/Canvas 的 skip 呈现,不影响链结构,可接受。go-forward 路径 method 从入口显式传入,不反推。
