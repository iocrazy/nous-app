# 资产库重构（Asset Loadout）— 设计

- **日期**：2026-08-28
- **状态**：设计已过用户逐条确认（9 屏 mockup v4），待写实施计划
- **分支**：`feat/asset-library-redesign`
- **UI mockup**：https://claude.ai/code/artifact/3db4a9bf-907d-40c5-bd21-40694107efbb （9 屏，键盘 ←/→ 翻页；本文用「屏 N」指代）
- **参考**：Infinite-Canvas 素材/提示词库、AIGC 资产库看板（文件+四维标签）、布丁入画（实体卡 + 件套完整度）、RunningHub（一张制作画布铺全部资产）、ArcReel / AID / nodetool（类型集合与"canonical descriptor 逐字拼进 prompt"的印证）

## 0. 一句话

**资产库是文件之上的语义层**：一张卡是一个实体（角色 / 场景 / 道具 / 服装 / 提示词 / 音频），文件挂在实体的槽位上；实体归 team，项目只是引用；画布里放的是实体的引用，永远不是副本。

心智模型叫「角色卡 + 装备槽」（Loadout）：角色卡 = 实体页；装备槽 = 槽位；装备 = 服装/道具；配装 = 角色 + 一套服装 + 若干道具的命名组合；上阵 = 发到画布。**"装备槽"只是设计语言，界面上一律用行业词**（Character / Costume / Loadout / Sheet）。

## 1. 现状与落差（代码实证，2026-08-28）

| 现有面 | 本质 | 问题 |
|---|---|---|
| 我的上传 | `resources` + `folders`，team 作用域，无 `project_id` | 没问题，是文件仓 |
| 工程资产 | `project → canvases → canvas_resource_refs → resources` 的投影；Chat Uploads 是前端拼的虚根（temp 文件夹，TTL 已退役） | 按容器分组的树：实体画布混在里面、一排 0、未命名画布当目录、Chat Uploads 与项目并列语义不通 |
| Generations | `generated_media` 独立表，"保存" = promote 进 `resources` | 只有一个"保存"，没有"归入资产" |
| 项目里的人物 / 场景库 / 道具 | `project_characters`（name / role_tag / description / tags / portrait_url / source）+ `project_lib_entities`（entity_type / name / badge_tag / description / tags / cover_url / source），**项目私有**；每个实体一张 `kind=character\|location\|prop` 的画布；卡片下素材条按 `generated_media.params.entity_kind/entity_id` 反查 | 跨项目不能复用；没有"这张图是谁的什么视图"这层语义；服装不存在 |
| 提示词 | `resources.gen_prompt*` 四列 + `tags.prompt_trigger`；画布 PromptNode 的 Library 弹层从 resources 筛"带 prompt 的文件" | 提示词模板不是实体，依附在文件上 |

## 2. 已拍板的决策（按对话顺序）

1. **资产归 team（全局库），项目只是引用/筛选**（方案 A）。个人作用域同理有一个资产库。
2. **实体即资产，文件挂在实体下**；文件层保留标签检索（文件仍是普通 `resources`）。
3. **服装是独立资产，"谁穿它"是资产间关系**（`wears`），一套服装可被多个角色穿；同理道具 `holds`。
4. **六种类型**：`character` / `location` / `prop` / `costume` / `prompt` / `audio`（music / sfx / voice 子类）。视频类型 v1 不做，枚举留口子。
5. **归入资产的文件不搬家、不复制**（甲）。同一文件可属于多个实体。
6. **侧栏四个位置**：My Downloads（个人）/ My Uploads 或 Libraries / **Generated** / **Assets**。一个作用域一个资产库，不随库的多少变。
7. **工程资产 + Chat Uploads + Generations 合并成「Generated」**：扁平流 + 筛选条，没有树；状态 Unreviewed / Saved / In Assets；批量清理手动触发，不自动删。
8. **产出 → 我的上传 → 资产库 三层流水线**：Save（进我的上传）/ Save as Asset…（Save + 归入，一步）。不是资产的美图止步于我的上传。
9. **画布引用实体（配装），不是引用文件**（甲）。
10. **主图槽是一张合成设定图**（角色 = 特写 + 三视图），不拆成 Front/Side/Back；实体页视觉区是**只读画板式布局**，不是画布。
11. **主图槽有图 = Ready**，补充槽只影响丰富度。
12. **图鉴页有类型切换条**（All + 六类，带计数），与侧栏子项双向同步。
13. **实体专属画布不再自动建**；"Open in canvas" 退到实体页次要位置，点了才建，建出来仍是普通无限画布，只是标记归属。迁移时现有实体画布随实体搬。
14. **资产唯一性**：`assets` 是唯一真源；画布节点只存引用；节点上的 loadout / 参考图勾选是节点自身状态；同作用域同类型同名建实体时提示"已存在，是否关联"；只有主动 Duplicate 才产生第二个 id。
15. **不做**（YAGNI）：Style 作为资产（`project_style_profile` 已在，且风格是项目属性）；组合方案 / 视频资产；给项目自动生成"官方制作画布"；用户自定义槽位；等级 / 稀有度 / 抽卡类假指标；emoji。

## 3. 数据模型

所有 id Snowflake BIGINT。建表顺序：`assets` → `asset_loadouts` → `asset_files`（后者外键引用前者）→ `asset_links` → `asset_project_refs` → `canvas_asset_refs`。作用域沿用 `resource_items.scope_id → teams.id` 的约定（个人作用域即个人 team）。

### 3.1 `assets` — 实体（唯一真源）

```sql
CREATE TABLE assets (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  scope_id        BIGINT REFERENCES teams(id) ON DELETE CASCADE,   -- NULL 仅允许系统预设（全局只读）
  asset_type      TEXT   NOT NULL CHECK (asset_type IN ('character','location','prop','costume','prompt','audio')),
  subtype         TEXT,                      -- audio: music|sfx|voice ; prompt: character|storyboard|product|lighting|…
  name            TEXT   NOT NULL,
  role_tag        TEXT   NOT NULL DEFAULT '', -- character: role_tag ; location: exterior/interior 等 ; 其它可空
  description     TEXT   NOT NULL DEFAULT '',
  attrs           JSONB  NOT NULL DEFAULT '{}'::jsonb,  -- 类型专属属性（location: time_of_day[]; audio: duration_ms, loop_range; prompt: placeholders[]）
  prompt_positive TEXT,                      -- 一致性提示词 / prompt 模板正文
  prompt_negative TEXT,
  prompt_positive_zh TEXT,
  prompt_negative_zh TEXT,
  platform_params JSONB  NOT NULL DEFAULT '{}'::jsonb,  -- prompt 类型：{midjourney:'--ar 1:1 …', seedream:'…', flux:'…'}
  cover_file_id   BIGINT REFERENCES resources(id) ON DELETE SET NULL,  -- 卡片头像；默认取主图槽第一张
  source          TEXT   NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','script_import','generated','migrated','duplicated','system_preset')),
  duplicated_from BIGINT REFERENCES assets(id) ON DELETE SET NULL,
  is_system_preset BOOLEAN NOT NULL DEFAULT false,  -- prompt 预设只读
  tags            JSONB  NOT NULL DEFAULT '{}'::jsonb,
  sort_order      INT    NOT NULL DEFAULT 0,
  created_by      UUID,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  CONSTRAINT assets_scope_or_preset CHECK (scope_id IS NOT NULL OR is_system_preset)
);
CREATE INDEX idx_assets_scope_type ON assets(scope_id, asset_type) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_assets_scope_type_name ON assets(COALESCE(scope_id, 0), asset_type, lower(name)) WHERE deleted_at IS NULL;

-- 六张表全部 ENABLE ROW LEVEL SECURITY + 仅 service_role 策略（照 mig 441 cover_template_usage）：
-- 前端不得 supabase.from('assets'…) 直读，一律走 /api/v1/assets。
```

迁移落地的**约束名**（schema drift 门禁与 `tests/migrations/test_445_asset_library_core.py` 都按名断言，改名即改契约）：`assets_asset_type_check`、`assets_source_check`、`assets_scope_or_preset`。

`uq_assets_scope_type_name` 是决策 14 的"同名提示"落点：API 捕获 23505 后返回 `409 {existing_asset_id}`，前端弹"已存在，是否关联"。

**系统预设**（`is_system_preset=true, scope_id NULL`）是唯一的全局行：列表查询 `scope_id = :scope OR is_system_preset`，只读，Duplicate 落到调用者 scope。

wire 形状上 `scope_id` 因此**也是可空的**（`AssetResponse.scope_id: Optional[str]`）—— 预设那一行的 `scope_id` 是 NULL，声明成必填会让第一个预设直接 500。

「只读」是**每一条写路径**的约束，不只是 `PATCH`/`DELETE`：`asset_files` / `asset_links` / `asset_loadouts` / `asset_project_refs` 都只按 `asset_id` 键控，没有 scope 谓词兜底，所以 service 侧统一走 `_require_writable()`（= 取行 + 预设则 403 `system_preset_readonly`），漏一条就是跨租户写 + 跨租户 id 泄露。

### 3.2 `asset_files` — 文件挂到槽位

```sql
CREATE TABLE asset_files (
  asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  resource_id  BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  slot         TEXT   NOT NULL,   -- 见 §3.6 槽位表；'unsorted' 合法
  loadout_id   BIGINT REFERENCES asset_loadouts(id) ON DELETE SET NULL,  -- 仅 character 的 Worn/Stills 可绑 loadout
  sort_order   INT    NOT NULL DEFAULT 0,
  note         TEXT,
  attached_by  UUID,
  attached_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (asset_id, resource_id, slot)
);
CREATE INDEX idx_asset_files_resource ON asset_files(resource_id);
```

- **不搬文件**：只写这张表，`resource_items.folder_id` 不动。
- 删 `resources` 行 → 级联消失；从实体移除 → 只删这行。
- 同一 `resource_id` 可出现在多个 `asset_id` 下（决策 5）。
- ⚠️ **主键 `(asset_id, resource_id, slot)` 不含 `loadout_id`**，其直接后果是：**同一张图不能在同一个槽位下同时属于两个 loadout**。挂第二次只会 upsert 掉第一行的 `loadout_id`。这在 P0 是刻意接受的（loadout 绑定只用在 character 的 `worn`/`stills`），但**是否要放开留作 P4 问题** —— 只有画布 / loadout UI 真跑起来才知道它咬不咬人。要放开就得把 `loadout_id` 提进主键，那会同时改变 upsert 语义。

### 3.3 `asset_links` — 实体间关系

```sql
CREATE TABLE asset_links (
  from_asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  to_asset_id   BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  relation      TEXT   NOT NULL CHECK (relation IN ('wears','holds','ambience_of','voice_of')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (from_asset_id, to_asset_id, relation),
  CHECK (from_asset_id <> to_asset_id)
);
CREATE INDEX idx_asset_links_to ON asset_links(to_asset_id, relation);
```

落地的约束名：`asset_links_relation_check`（relation 取值）、`asset_links_no_self`（`from <> to`）。

| relation | from → to | 校验 |
|---|---|---|
| `wears` | character → costume | 类型由 service 校验（表层不做跨表 CHECK） |
| `holds` | character → prop | 同上 |
| `ambience_of` | audio(sfx/music) → location | 同上 |
| `voice_of` | audio(voice) → character | 同上 |

场景不参与关系；"角色出现在场景"留给分镜层表达。

### 3.4 `asset_loadouts` — 配装

```sql
CREATE TABLE asset_loadouts (
  id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,  -- 只允许 character
  name         TEXT   NOT NULL,
  is_default   BOOLEAN NOT NULL DEFAULT false,
  costume_ids  BIGINT[] NOT NULL DEFAULT '{}',  -- 引用 assets(costume)，必须 ⊆ 该角色 wears 集
  prop_ids     BIGINT[] NOT NULL DEFAULT '{}',  -- 引用 assets(prop)，必须 ⊆ holds 集
  prompt_extra TEXT,                            -- 该配装的附加提示词（"black hooded night robe…"）
  sort_order   INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_loadout_default ON asset_loadouts(asset_id) WHERE is_default;
```

- 建角色时自动建一个 `Default`（空配装）。
- `costume_ids / prop_ids ⊆ links` 由 service 校验；解除 wears 时从所有 loadout 里剔除。
- 额外索引：`idx_asset_loadouts_asset (asset_id, sort_order)`。
- ⚠️ **`uq_loadout_default` 是 partial unique *index*，PostgreSQL 逐行检查、无法 deferrable**。所以 `set_default` 必须**先清兄弟行、再置目标行**；反过来写在常规路径上必然撞上仍然存在的旧 default。归属判定（这个 loadout 是不是这个 asset 的）用一次 `SELECT … FOR UPDATE` 探针在任何写之前做完 —— 否则「不属于本 asset」这条路径会把角色留在**一个 default 都没有**的状态。
- `is_default=false` **不是一个操作**：唯一索引意味着「恰好一个 default」，只有「把另一个设为 default」，没有「取消 default」。PATCH 体里的 `is_default=false` 因此被丢弃，是刻意的。

### 3.5 `asset_project_refs` — 项目引用

```sql
CREATE TABLE asset_project_refs (
  asset_id    BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  linked_by   UUID,
  linked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (asset_id, project_id)
);
CREATE INDEX idx_apr_project ON asset_project_refs(project_id);
```

`canvas_asset_refs` 侧另有 `idx_car_asset (asset_id)`（按资产反查画布）。

项目工作区的人物 / 场景库 / 道具 / 服装页 = `assets JOIN asset_project_refs WHERE project_id = ?`。在项目里 New → 建 asset + 写一行 ref。Link from library → 只写 ref。Unlink → 只删 ref。

### 3.6 槽位（代码常量，不进表）

| 类型 | 主图槽（决定 Ready） | 补充槽 |
|---|---|---|
| character | `sheet`（特写 + 三视图合成） | `stills` `expressions` `extras` `worn`（来自服装，随 loadout） |
| location | `establishing` | `keyframes` `details` `layout` |
| prop | `turnaround` | `in_scene` `details` |
| costume | `flat` | `worn` `details` |
| prompt | —（`prompt_positive` 非空即 Ready） | `examples` |
| audio | `primary` | `variants` |

所有类型都接受 `unsorted`。每个槽可多张，`sort_order=0` 为代表。**用户不能自定义槽位**（决策 15）。

`readiness` 是派生值（service 计算，不存列）：`ready` = 主图槽 ≥1 张（prompt 看正文）；否则 `draft`，附 `missing: [slot]`。

### 3.7 既有表改动

- `canvases` 加 `asset_id BIGINT NULL REFERENCES assets(id) ON DELETE SET NULL`；`kind` CHECK 增 `'costume'`（`project_id` 保持 NOT NULL，记录从哪个项目打开的）。
  ⚠️ **枚举原本只有 DB 一侧（裁决 G）**：`costume` 在 CHECK 里存在，而 `schemas/canvas.py` 的 `CanvasKind` / `CreatableCanvasKind` 与 `frontend/features/canvas-core/types.ts` 都没有它，`canvasKindFor` 于是把 costume 降级成 `smart`。P4 Task 4 把两侧枚举补齐，`canvasKindFor(costume)` 返回 `'costume'`。
  ✅ **`asset_id` 在 P4 起有读方**：空画布 + `canvases.asset_id` 非空 → 画布加载后播一张绑定该资产的 asset 卡。原来那条 `?characterId=` / `?entityId=` 查询串播种分支同批删除 —— 自 P3 Task 6 起就没有任何 UI 产出过那两个参数。
- **新表 `canvas_asset_refs`**（镜像 `canvas_resource_refs`）：`(canvas_id, asset_id, node_id, loadout_id)`；`CanvasService.save` 从 `nodes_json` 提取 `type='asset'` 节点维护，失败不阻塞保存，可 backfill。
  ✅ **P4 已接线**：抽取器是 `backend/app/services/canvas/asset_node_refs.py::extract_asset_node_refs`（**新模块名**，裁决 J —— 同目录下 `asset_refs.py::extract_asset_refs` 早被 **resource** refs 占用，旧名不改以免无关 churn，只在文件顶部加了一行注释指明它是哪一种），维护走 `canvas_service._sync_refs`，与 resource refs 同样 try/except + log。
  ⚠️ **写法不能照抄 resource refs（裁决 I）**：`loadout_id` **不在唯一键里**（键是 `(canvas_id, asset_id, node_id)`），所以 `on_conflict_do_nothing` 会把换过造型的节点留在旧 `loadout_id` 上。实现是 **DELETE-all + `on_conflict_do_update(set_={loadout_id})`**，而且 DELETE 与 INSERT 在**同一个 `write_scope()` 事务**里 —— resource refs 那边用两个事务并称「部分失败是自愈而非损坏」，对这张表是假的：一个节点带着不存在的 asset 雪花会让 INSERT 抛错，DELETE 已提交则整块画布的 refs 全没了，且每次保存重复同一结局，表现就是 `used_in` 对着用户看得见的画布回答「没被用到」。
  不属于本资产的 `loadout_id` 在插入时被置 NULL 并计数上报，不是静默丢弃。
- `generated_media` 加 `review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (IN ('unreviewed','saved','in_assets','deleted'))` 与 `source_asset_id BIGINT NULL`（从资产节点/实体画布生成时预填）。
  ✅ **P4 起画布真的在写它（裁决 H）**：画布生成把最近上游 asset 节点写进 `params.source_asset_id`（外加 `params.loadout_id`），并由 `GenerationOrigin` 落到 `source_asset_id` **列**上，所以收件箱与实体页的 Generation history 看得到画布跑的图。同一批**退役了旧的 `entity_kind` / `entity_id` 戳**：它们的 id 是 `_legacy_project_*` 的行号，读的那半边（`fetchEntityGenerations`）自 P3 Task 6 起就没有调用方了，两半一起删，有负向测试钉住不会再写回。
  ⚠️ **`origin_kind` 不加 CHECK，也没有「扩枚举」这回事**（P0 实证纠偏）：该列自 mig 307 起就是裸 `TEXT NOT NULL`，`schema_baseline.sql` 确认从未有过 CHECK；而 `idx_genmedia_node_shot`（mig 354）的谓词是 `origin_kind IN ('shot_generate','shot_video')` —— 这两个值不在本文档原先列出的枚举里。对一个从没有约束的列「扩枚举」等于**新加限制**，会当场把活数据判违规。`origin_kind` 因此保持**代码级枚举、数据库不设约束**。
- **Chat Uploads 迁入 `generated_media`**：temp 文件夹里的 `resources` 逐行登记为 `origin_kind='chat_upload'`、`promoted_resource_id` 指向自身、`review_state='saved'`（它们本来就在 resources）。temp 文件夹本身不再有特殊语义；`tempResources` 代码路径退役。⚠️ **P1 只做了这半边**：`ResourceGrid` 仍然把 `temp` 从根目录网格里滤掉，文件夹也没有改名为 "Chat uploads" —— "保留为普通文件夹、在 My Uploads 下可见"这一半**移交 P6**（与 `tempResources` 的删除同批）。功能上不阻塞：这些文件通过 Generated 收件箱可达。
- `tags.prompt_trigger` 与 `resources.gen_prompt*` **保留不动**——文件级 prompt 仍可搜；提示词**模板**才是 `assets(type=prompt)`。

### 3.8 退役

- `project_characters`、`project_lib_entities` → 迁到 `assets` 后 DROP（分两步：先 rename 成 `_legacy_*` 一个版本周期，再删）。
  ✅ **rename 半程已完成（mig 447）**：生产迁移 2026-09-02 跑完并对账全中（8 assets + 8 refs，重跑 0/8/0，3 张实体画布挂上），两张表随即改名 `_legacy_project_characters` / `_legacy_project_lib_entities`；同一个 PR 删掉了 `/projects/{id}/characters*` 与 `/projects/{id}/lib/*` 全部端点、两个 repository 和两份 Pydantic schema（删除时前端零调用方，已复核）。**唯一剩下的读方**是迁移 workflow `backfill_assets_from_project_entities`（ORM 模型 `__tablename__` 已跟着改名），刻意保留以便窗口期内应急重跑；它与 `_BACKFILLS` 注册项一并在 P6 随 DROP 删除。
  ⏳ **DROP 待做（P6）**：一个版本周期后执行。索引/约束/RLS policy 名仍是改名前的拼写（`ALTER TABLE ... RENAME` 只换 OID 指向不换名字），随 DROP 一起消失，刻意不单独改名。回滚 = 改回原名 + 还原两个 `__tablename__`，无数据变更。
- `canvas_resource_refs` 保留（文件级反查照旧）。
- `temp_resource_sweeper` 的**调度早已停用**（cron 装饰器自 2026-06-13 起就是注释掉的，已在 merge base 核实）；P1 只移除了它那个 bundle 导入 —— 也就是说这**不是**一次对现存用户的行为改变。代码删除留 P6。
- 智能文件夹"提示词库"是用户数据，不动；文档提示用 Assets → Prompts。

## 4. 迁移路径（一次性 backfill，幂等）

1. `project_characters` → `assets(type=character, scope_id = 项目所属 team 或个人 team, name, role_tag, description, tags, source='migrated')` + `asset_project_refs`；`portrait_url` 若能解析到 `resources` 行则写 `cover_file_id` 并挂 `unsorted`；建 Default loadout。
2. `project_lib_entities` → 同上，`entity_type` → `asset_type`，`badge_tag` → `role_tag`，`cover_url` 同处理。
3. 同 team 内**同类型同名**冲突：合并为一个 asset，refs 各指向各自项目（这正是跨项目复用的第一批受益者）；冲突记录写迁移日志。
4. 现有 `kind IN ('character','location','prop')` 画布：按名字规则 `"{name} · {Kind}"` 反解到 asset，写 `canvases.asset_id`；解析不到的保留为普通画布。
5. `generated_media.params.entity_kind/entity_id` → `source_asset_id`（按 1/2 的 id 映射表）；`promoted_resource_id IS NOT NULL` 的置 `review_state='saved'`；已被 `asset_files` 引用的置 `in_assets`。
6. Chat Uploads 登记（§3.7）。
7. `canvas_asset_refs` 从 `nodes_json` 重建（此时应为空，占位）。

✅ **个人项目的映射已由 P3 拍板（`projects.team_id IS NULL`）**：迁到 **owner 的个人 team** —— 也就是 `assets_router._project_scope_id` 为 `GET /projects/{id}/assets` 解析出的同一个 scope。选它的理由不是"最自然"，而是**工作区那四个页面就是这么读的**：写到别处等于迁完了 UI 还是空的。

合并语义与 team 项目**完全对称**（这正是 P0 说要先定的那一条）：映射后的 scope 直接进同一个 `(scope_id, asset_type, lower(name))` 分组，所以同一个人在自己两个个人项目里的同名角色**合并成一个 asset、被两个项目各自 ref**，跟同一个 team 里两个项目的行为一字不差。planner 里没有任何个人项目分支（分支只在 `_load_inputs` 的 scope 解析里），第 4 步画布反解因此自动继承这个映射。不同 owner 的同名角色不会合并 —— 个人 team 天然按人分开。

生产地面真值（拍板依据，2026-09-01 实测）：个人侧 **1 个 character + 7 个 lib entity，全在一个个人项目里**；team 侧 **0 行**。也就是说这条映射不是理论补全，它是**目前唯一有数据要迁的那一侧**；不做就等于这批行在 P3 之后既迁不走、前端又已经切到 `assets` 读不到它们（T6 已删掉旧读者），而 P6 会 DROP 那两张表。

剩下的残差只有一个桶：`skipped_unmappable_personal` —— 项目无 team **且** owner 连 `kind='personal'` 的 `teams` 行都没有。`assets.scope_id` 是 `teams` 外键，没有可写的 scope，所以只计数、不猜。它与 `skipped_unknown_project`（项目 id 根本解析不到）分开计数，两者都写进 Task Center subtitle —— 否则一个全是跳过行的工作区会显示成「0 assets from 42 rows」，读起来就是「没东西可迁」。⚠️ 这个桶在 P3 之前叫 `skipped_personal_project` 且含义是**所有**个人项目；改名而非复用，是为了让 P3 之前跑过的 dry-run 元数据不会被当成同一个意思读。

每步可重跑。**对账口径见下 —— ⚠️ 不要用 `count(assets where source='migrated')` 那条老公式对账**（P3 已作废）：`_apply` **刻意认领任意来源的同名资产**，包括用户手建的 `source='manual'` 行（那正是合并语义想要的：legacy 行和手建资产是同一个角色）。所以一次**正确**的运行里，`source='migrated'` 的行数会**少于**计划的资产数，少的恰好是被认领的那些 —— 照老公式算会算出缺口，把一次干净的迁移读成失败。P3 的 E2 用例正好构造了这个场景（一个大小写不同的手建 asset 被认领）。

真正的对账由 live 那次运行**自动收尾**（`_reconcile` → `reconcile_counts`），口径是两条，**都不看 `source`**：

1. 计划里的每一个 key `(scope_id, asset_type, lower(name))` 在库里都有一个存活资产 —— 问法与 `_apply` 认领时问的**完全同一句**；
2. 计划**要求**的每一对 `(asset_id, project_id)` 都有 `asset_project_refs` 行 —— 只数计划要的那些，因为被认领的资产可能早就带着本次计划没提到的项目 ref。

口径不符 → 运行直接变红（phase=failed），报告在抛出**之前**已写进 `task_tracking.metadata.reconciled`，所以失败的运行里点名了缺哪些 key。操作员的动作是**读那次运行的 `metadata.reconciled`**，不是自己跑 SQL 数行 —— 完整流程见 PR body 的「部署后必做」。

## 5. API（`/api/v1`）

### 5.1 Assets

| 端点 | 说明 |
|---|---|
| `GET /assets?scope_id&type&project_id&q&limit&offset` | 图鉴列表；返回派生 `readiness / missing / project_ids / file_counts_by_slot / loadout_count`。**`readiness` / `tag` / `sort` 三个筛选参数是 P2**，P0 只实现前五个 |
| `POST /assets` | 建实体；同名 → `409 {existing_asset_id}` |
| `GET /assets/{id}` | 实体页全量：files by slot、links（含反向 worn_by/held_by）、loadouts、project refs、used_in、generation_history（generated_media by source_asset_id）。✅ **`used_in` 自 P4 起是真的**（裁决 F）：`used_in.canvases[]` 每行 `{canvas_id, canvas_name, kind, project_id, node_ids[], loadout_ids[]}`，按画布聚合（一块画布放三张卡是一行三个 node_id），并**按调用方 scope 过滤**（系统预设对每个 scope 可读，不过滤会把别的 team 的画布名讲出去）。`used_in.storyboards` 声明在协议里且**恒为空** —— 分镜侧还没有 ref 镜像；写成「声明且为空」而不是「缺这个键」，是为了让客户端渲染「没有分镜引用」而不是去分支判断键存不存在。实体页右栏的 Used In 面板 P4 起渲染它（画布名可点，带 `?node=<第一张卡>`） |
| `PATCH /assets/{id}` | 头部字段 / 提示词 / attrs；system preset 拒绝 |
| `DELETE /assets/{id}` | 软删；画布节点回读得到 `asset_removed` |
| `POST /assets/{id}/duplicate` | **P2**（P0 不实现：系统预设还不存在，而 Duplicate 的第一用途就是「预设落到自己 scope」）。复制实体（文件关联复制、links 复制、loadouts 复制），`duplicated_from` |
| `POST /assets/{id}/files` `{resource_id, slot, loadout_id?}` | 挂文件（=归入资产）；批量 `{items:[…]}` |
| `DELETE /assets/{id}/files/{resource_id}/{slot}` | 解关联 |
| `POST /assets/{id}/links` `{to_asset_id, relation}` / `DELETE …` | 关系 |
| `POST /assets/{id}/loadouts` / `PATCH` / `DELETE` | 配装 |
| `POST /assets/{id}/prompt/translate` | 复用现有 translate agent |
| `POST /assets/{id}/prompt/regenerate` | 复用 vision agent，从主图槽反推 |
| `POST /assets/{id}/generate-slot` `{slot, loadout_id?, model?}` | "Generate missing"：用主图 + 一致性提示词 + 槽位模板生成，落 `generated_media(source_asset_id, params.target_slot)` |
| `GET /assets/{id}/bundle?model=&loadout_id=` | **投递协议**（§6.3）：返回 `{prompt:{positive,negative}, reference_resource_ids[], dropped[], max_refs}`。⚠️ **P4 改了路径（裁决 C）**：原文写成 `/loadouts/{lid}/bundle`，但只有 character 有 loadout，其余五类根本没有可填的 `{lid}`；`loadout_id` 因此降为**可选查询参数**。`dropped[]` 是必须渲染的那一半 —— 每个被裁掉的参考都带 `reason`（`over_limit` / `no_image_file` / `provider_no_refs`），只读 id 列表会把「裁剪过的投递」报成完整投递 |
| `POST /assets/{id}/project-refs` `{project_id}` / `DELETE` | Link / Unlink |
| `GET /projects/{pid}/assets?type=` | 项目分级视图（= 上面 GET 的固定筛选） |
| `POST /projects/{pid}/assets/import-from-script` | 现有"一键导入"改落 assets + refs |
| `GET /canvases/{id}/asset-refs` / `GET /assets/{id}/canvas-refs` | 反查 |

### 5.2 Generated

| 端点 | 说明 |
|---|---|
| `GET /generated?scope_id&state&origin_kind&project_id&media_kind&model&since&cursor&limit` | 扁平列表，默认 `state=unreviewed`（`state=all` 是无过滤别名）；`cursor` keyset 翻页，`limit` 1–200、默认 60；每项带 `source {kind, canvas_id, node_id, shot_id, label, deep_link}` |
| `POST /generated/{id}/save` | 现有 promote，且置 `saved` |
| `POST /generated/{id}/save-as-asset` `{asset_id \| new_asset:{type,name}, slot, loadout_id?}` | `promote`（幂等，事务外）→ 然后 `asset_files` attach + `in_assets` 在同一个事务里。目标校验（asset 可写 + slot 合法）在 `promote` **之前**跑，纯校验失败不留下已 promote 的资源 |
| `POST /generated/batch` `{ids, action: save\|save_as_asset\|delete, …}` | 批量 |
| `POST /generated/cleanup` `{older_than_days, dry_run}` | 手动清理；`dry_run` 返回数量与样例，UI 必须先 dry-run 再确认 |

所有 Generated 读取走后端端点（`generated_media` 已是 service-role-only RLS）。

## 6. 前端

### 6.1 资源库（屏 1–3, 5）

- `ResourcesSidebar`：位置区改为 Downloads / Uploads(或 Libraries) / Generated(未处理计数 pill) / Assets(六个子项带实体计数)。`project-assets` / `temp` 两个 `SidebarView` 退役，路由 `/resources/project-assets` `/resources/temp` → redirect `/resources/generated`。
- **`GeneratedView`**（新）：状态 tabs + FilterBar（Source / Project / Type / Model / Date）+ 卡片网格。卡片三行（标题 = prompt 首句或文件名 / 来源行可点 deep_link / 模型·日期），动作 Save / As Asset… / 删除；多选 → 批量条；Clean up… 弹窗强制 dry-run。
- **`AssetsView`**（新）：类型切换条（All + 六类，计数）↔ 侧栏子项同步；FilterBar（Project / Readiness / Tags / Sort）；`AssetCard`（头像 / 名字 / role / 槽位小方块 / Ready·Draft chip 含 missing / 项目 chip / 完成度环 SVG）；All 视图卡片左上带类型标；`+ New ▾`。
- **`SaveAsAssetDialog`**（新，屏 5）：类型 seg → 候选（带 suggested：`source_asset_id` 命中放首行）→ 槽位 seg（可留空 = unsorted）→ loadout（仅 character）→ tags；批量模式下槽位改为逐项列表。四个入口共用：Generated 卡片、My Uploads 右键、画布 Output 节点、聊天附件右键。页脚固定文案 "File stays where it is · attaching never moves or copies"。

### 6.2 实体页（屏 4 / 4b / 4c）

`AssetSheetPage`，按 `asset_type` 切子布局，骨架一致：头部（头像 / 名 / chips / 描述 / loadout chips[character]）→ **Board**（画板式：主图大幅 + 补充 pins；Arrange 拖排存 `attrs.board_layout`；Equip… 打开文件选择器；Generate missing…）→ 关系区（Wears / Holds / Worn by / Held by / Attached to）→ 提示词区（正/负 · EN/中 · Translate · Regenerate）→ 右栏（Send to Canvas / Send to Agent / Copy loadout prompt；Details；Used in；Generation history；"Open in canvas ›" 小链接）。

- prompt 类型：Board 换成正/负文本编辑 + 占位符面板 + 平台参数 + Examples；system preset 只读，Duplicate to My Prompts。
- audio 类型：Board 换成波形（复用现有音频播放器）+ Variants 列表 + Attached to；主按钮 Send to Timeline。

### 6.3 画布（屏 6）

- 新节点类型 `asset`：`{type:'asset', asset_id, loadout_id, selected_file_ids[]}`。渲染为迷你角色卡（头像 / 名 / loadout 切换 / 参考图勾选，灰掉空槽）。`selected_file_ids` 是节点状态，不写回资产。
- **投递协议**（bundle）：生成节点上游有 asset 节点时，调 `GET /assets/{id}/bundle?model=&loadout_id=`，按 provider 能力表裁剪：优先级 主图槽 > 服装 worn > stills；超限的在节点上灰掉并提示。prompt 拼接顺序固定：`asset.prompt_positive` → `loadout.prompt_extra` → 各 costume/prop 的 `prompt_positive` → 场景 asset 的 `prompt_positive` → 用户文本；negative 取并集去重。**这层写成纯函数 + 单测**（每种 provider 一组用例），不散在节点里。
  - **能力表在代码里，不在 `config.yml`（裁决 A）**：唯一真源是 `backend/app/services/generation/provider_protocols/base.py::ProviderCapabilities`，它自己的注释就明文禁止第二处真相，`config.yml` 里从来没有这个键。bundle 读 `resolve_generation_protocol(model).capabilities`，前端读 `GET /canvases/generation-capabilities`。未知 model → 422 `model_unknown`，不给保守缺省 —— 猜一个上限就是编一个答案。
  - **`multi_subject` 删除（裁决 B）**：全仓库没有这个概念，写进能力表等于给一个不存在的开关留位置。
  - **灰掉超限用 `max_refs`（裁决 E）**：卡片的参考勾选读 `useModelCapabilities(model).max_refs`（`null` = 未知 ⇒ 按全支持渲染），排序用与后端 `_slot_priority` 对齐的镜像常量，**最终真相仍是后端 `reconcile` 的 `dropped_knobs` 与 bundle 的 `dropped[]`**。
  - **参考图要过资源桥（裁决 D）**：画布生成链原先只认 `/api/v1/generated-media/` 前缀的 URL，`resources` 的 URL 会被**静默丢弃且不进 dropped**。P4 建了桥 —— 后端 `generated_media_service` 认 `/api/v1/resources/{id}/(cover|file)` 并在 `system_request_scope` 下物化（校验该行属于**画布所在项目的 scope**，由 `canvas_id` 服务端推导，不信客户端），前端 `DURABLE_PREFIXES` 扩成两个前缀。解析不了的进结果的 `dropped_refs`，永不静默。
- Output 节点 → Generated；Output 的 As Asset… 预填 `source_asset_id + loadout_id`（读 `GET /generated/{id}` 拿真行，不用节点数据合成 —— 那一列节点从来没见过）。
- 动作 **Insert project assets**：在任意画布上按类型四条 lane 铺出本项目引用的资产节点（RunningHub 式制作画布，零手工）；不自动建"官方制作画布"。
- 资产被删：节点显示 `Asset removed` 占位，不静默消失。
- 复制节点 = 复制引用（同 asset_id）：`asset_id` / `loadout_id` / `selected_file_ids` **不进** `remapSmartTags`，有测试钉住。
- **旧智能卡迁移**：加载后（不阻塞首屏）对 `character` / `location` / `prop` 节点按 `data.character_id` / `data.entity_id` 调 `GET /assets/resolve-legacy?kind=&legacy_id=`；命中 → **原地**改成 asset 节点（同 node id、同位置，连线不动），未命中（`{"asset_id": null}`，一个真的 200）→ 保留旧卡并打 `Unmigrated` 标记；请求失败 → 什么都不改（「问不到」不是「没有」）。写回走 `setNodesTransient`，不进历史也不强制保存 —— 仅仅打开一块老画布不该产生一次 PUT。

  ⚠️ **代价：`canvas_asset_refs` 要等到下一次真实保存才补上。** 该镜像由 `CanvasService` 在保存路径上从 `nodes_json` 重建，而这里刻意不保存，所以刚迁完卡片的画布**不会**出现在资产页的 Used In 面板里，直到有人编辑并保存那块画布。这是「打开不该是写入」这条取舍的直接后果，不是缺陷；写在这里是因为「卡片明明在画布上，Used In 却说没有」正是 `canvas_asset_refs` 一开始要防的那种读法。要立刻补齐可跑 `backend/scripts/backfill_canvas_asset_refs.py`。

### 6.4 项目工作区（屏 6 下半）

- 侧栏「素材」组：人物 / 场景库 / 道具 / **服装**（新）；数据源换 `GET /projects/{pid}/assets?type=`。
- 卡片 = `AssetCard`（同一组件），点进 `AssetSheetPage`；卡片上不再有大按钮「在画布中打开」。
- 顶部动作：Link from library（选择器，列出本 team 未关联的同类资产）/ + New / Import from script（横幅照旧）。
- `EntityAssetStrip` 退役，被实体页 Generation history 取代。

### 6.5 聊天 / agent

- Send to Agent：复用 `pendingResource` 通道，扩展为 `pendingAsset {asset_id, loadout_id}`；后端 `resolve_resource_refs` 增补 asset 引用 → 展开为主图 + 一致性提示词进 `<available_resources>`（走 `escape_frame_body`，并把 `<asset>` 框登记进 `OWNED_FRAMES`）。
- 聊天里 `@` 选择器加 Assets 一栏（v1 只做插入引用，不做 loadout 选择）。

## 7. 错误处理与不变量

### 7.0 作用域与权限（2026-08-28 复盘补）

| 规则 | 落点 |
|---|---|
| 资产归 `scope_id = teams.id`（个人 = 个人 team）；`/assets*` 门控 = **team 成员**（与 `verify_scope_access` 同口径） | router `_gate` |
| 写权限 = 任意 team 成员，v1 不分角色（与 resources 一致） | 明示取舍，不做 |
| 只在 `project_members`、不在 team 的协作者：经 `/projects/{id}/assets` **只读**；`/assets?scope_id` 会 403 | 项目视图是只读投影 |
| `asset_files.resource_id` 必须在同 scope（`resource_items.scope_id`） | service `attach_file` → 404 |
| `asset_links` 两端必须同 scope | service `add_link` → 404 |
| `asset_project_refs` 的项目必须属于同 scope（`projects.team_id == scope_id`，个人项目 `team_id NULL` 对应个人 team） | service `link_project` → 422 `project_scope_mismatch` |
| 六张表 RLS：service_role-only，前端零直读；P2 加前端 tripwire 测试（grep `from('assets`）拒绝 | mig 445 + 前端测试 |
| 系统预设 `scope_id NULL`，任何 scope 可读、不可改 | §3.1 |

存储：资产库**不写字节**——`asset_files` 只指 `resources`，封面走 `/resources/{id}/cover`（已兼容 `sb://` 对象存储）。P3 迁移把旧 `portrait_url/cover_url`（URL）反解为 `cover_file_id`，反解不到的留在 `attrs.legacy_cover_url`。


- 归入资产的每条路径都返回类型化结果（成功 / 409 已存在 / 404 资源不可见 / 422 槽位不合法），前端逐条回显——沿用"触发路径必须类型化失败回显"纪律。
- `save-as-asset` 分两段：`promote` 先跑（幂等，靠 `promoted_resource_id` 短路），**不在事务内** —— 它会 `SET LOCAL ROLE service_role`、做存储 I/O、且内部有"非致命"的 backlink 兜底，三者都不能待在共享事务里。attach + `in_assets` 在一个事务里：attach 失败则该行停在 `saved`（一个合法状态，等同于普通 Save），**永远不会出现 `in_assets` 却没有附件**。
  纯校验失败（asset 不存在 / 是系统预设 / slot 与该 asset_type 不匹配）在 `promote` 之前就拒绝，所以 404/422 不会已经把文件拷进 My Uploads；`save` 同理先校验 scope 再 promote。
- `canvas_asset_refs` 维护失败只记日志，不阻塞画布保存；backfill 可重建。
- readiness 永远派生，不缓存进列；列表端点用一条带 `EXISTS` 的查询算。
- 边界 mock 用真实 wire 形状：`assets.id` 与 `canvases` 一样显式 `str()`，但落点是 **repository 的序列化边界**（`_serialize` / `_serialize_file` / `_serialize_link` / `_serialize_loadout`），不是 router —— 数组元素（`costume_ids` / `prop_ids`）也在那里逐个 `str()`；`asset_files.resource_id` 同。测试 fixture 必须覆盖数字 id 分支（2026-08-12 教训）。
- 投递协议超限**不静默截断**：返回 `dropped: [{resource_id, reason}]`，节点显示。

## 8. 测试

- 后端：assets CRUD 与 409 同名；files 多实体共享 + 级联；links 类型校验 + loadout 子集校验；loadout 默认唯一；readiness 派生（六类各一组）；bundle 纯函数（每 provider 上限 / 优先级 / 拼接顺序 / negative 去重 / dropped 回显）；save-as-asset 事务回滚；generated 列表默认 unreviewed、cleanup dry-run；迁移脚本幂等 + 同名合并 + 对账数。
- 前端：侧栏计数与路由 redirect；GeneratedView 状态切换与来源 deep_link；AssetsView 类型条 ↔ 侧栏同步；AssetCard 完成度环 / missing 文案；SaveAsAssetDialog 四入口预填（suggested 首行）；AssetSheetPage 三种子布局渲染；asset 节点 selected_file_ids 不写回；Insert project assets 分 lane；Asset removed 占位。
- E2E（真栈，`e2e-prod`）：产出页保存为资产 → 资产库出现 Ready 卡 → 项目人物页可见 → 拖进画布生成 → 新图回到 Generated 且 As Asset 预填同一角色（**可证伪：断言 asset_id 相等**）。

## 9. 分期（每期一个 PR 串，可独立上线）

| 期 | 内容 | 依赖 |
|---|---|---|
| **P0 数据层** | mig：assets / asset_files / asset_links / asset_loadouts / asset_project_refs / canvas_asset_refs；generated_media 新列；canvases.asset_id；ORM + repo + 基础 API；迁移脚本（只读演练 + 对账） | — |
| **P1 Generated** | GeneratedView + 侧栏改造 + Chat Uploads 登记 + save-as-asset 端点 + SaveAsAssetDialog（Generated 入口） | P0 |
| **P2 Assets 图鉴 + 实体页** | AssetsView / AssetCard / AssetSheetPage（六类）/ Board / 关系 / loadout / 提示词 agent 接线 / Generate missing | P0 |
| **P3 项目分级视图 + 迁移执行** | 项目侧栏改数据源 / Link from library / import-from-script 改落 assets / 跑迁移 / 旧表 rename legacy | P2 |
| **P4 画布** ✅ 2026-09-02 | asset 节点 / bundle 投递协议（含**资源参考桥**）/ Output 的 As Asset 预填 / Send To Canvas / Insert project assets / canvas_asset_refs + `used_in` 反查 / 旧智能卡加载时迁移 | P2 |
| **P5 聊天 / agent** | pendingAsset / resolver / `<asset>` 框 / @ 选择器 | P2 |
| **P6 清尾** | 删 legacy 表 / EntityAssetStrip / tempResources / temp_resource_sweeper（含 `ChatTempTtlPanel` + `tempTtlService`，P1 已下架其渲染点）；temp 文件夹改名 "Chat uploads" 并在 My Uploads 下可见（§3.7 未做的那半边）；My Uploads 右键的 As Asset 入口（**Output 节点那个 P4 已做**） | P3 P4 |

P1 与 P2 可并行（不同 worktree）。

## 10. 风险

- **迁移同名合并误合**：不同项目里两个真的不同的"老张"。缓解：合并前列出清单人工过目；合并后的 asset `attrs.merged_from[]` 可拆回。
- **画布节点旧数据**：现有画布没有 asset 节点，无影响；`kind=character` 画布反解失败的留普通画布，不丢数据。
- **generated_media 体量**：Chat Uploads 登记会一次性加行；表本就设计为高流失层，索引 `(scope_id, review_state, created_at DESC)`。
- **投递协议 provider 能力表会过时**：⚠️ **本条已按裁决 A 作废**。能力表**不放 `config.yml`** —— 它已经在代码里（`ProviderCapabilities`），再放一份就是两处真相；「缺省保守 1 张」也不做，未知 model 一律 422 `model_unknown`。过时的缓解是加 provider 时改那一个类，`GET /canvases/generation-capabilities` 与 bundle 同源读它。
- **权限**：assets 沿用 team membership；system preset prompt 全局只读；跨 team 不可见（RLS + 后端 gate 双层）。
- **协作者看不到自己在画布上跑出来的图（P4 已知缺口）**：画布生成的产物按**画布所属项目的 scope** 登记，而 Generated 收件箱的门是**团队成员身份**。一个不是团队成员的项目协作者因此跑得动生成、却在收件箱里看不到结果。这与 P3 Task 5 记下的是同一类缺口（项目协作者模型与团队作用域模型不重合），不在 P4 修复范围内。
- **被「收养」的资产找不回旧卡** ✅ **P4 收尾波已修**：adopt 分支现在幂等地把 `[table, id]` 追加进 `attrs.legacy_ids`（集合语义，`attrs` 其余键原样保留，不写 `merged_from`），所以被收养的老实体和其它实体一样能被 `resolve-legacy` 找到。**存量需要重跑一次迁移**才补齐——重跑对已打标的行是完全的 no-op（连 `updated_at` 都不动），生产当前 adopted 计数为 0，所以窗口很小。仍然刻意不靠名字猜：那会把卡片接到没人选过的资产上。
