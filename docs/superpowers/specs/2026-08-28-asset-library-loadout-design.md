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
  scope_id        BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
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
  deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_assets_scope_type ON assets(scope_id, asset_type) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_assets_scope_type_name ON assets(scope_id, asset_type, lower(name)) WHERE deleted_at IS NULL;
```

`uq_assets_scope_type_name` 是决策 14 的"同名提示"落点：API 捕获 23505 后返回 `409 {existing_asset_id}`，前端弹"已存在，是否关联"。

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
- **新表 `canvas_asset_refs`**（镜像 `canvas_resource_refs`）：`(canvas_id, asset_id, node_id, loadout_id)`；`CanvasService.save` 从 `nodes_json` 提取 `type='asset'` 节点维护，失败不阻塞保存，可 backfill。
- `generated_media` 加 `review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (IN ('unreviewed','saved','in_assets','deleted'))` 与 `source_asset_id BIGINT NULL`（从资产节点/实体画布生成时预填）；`origin_kind` 枚举扩到 `canvas_run | agent_run | storyboard | cover_studio | chat_upload`。
- **Chat Uploads 迁入 `generated_media`**：temp 文件夹里的 `resources` 逐行登记为 `origin_kind='chat_upload'`、`promoted_resource_id` 指向自身、`review_state='saved'`（它们本来就在 resources）。temp 文件夹本身保留为普通文件夹"Chat uploads"（在 My Uploads 下可见），不再有特殊语义；`tempResources` 代码路径退役。
- `tags.prompt_trigger` 与 `resources.gen_prompt*` **保留不动**——文件级 prompt 仍可搜；提示词**模板**才是 `assets(type=prompt)`。

### 3.8 退役

- `project_characters`、`project_lib_entities` → 迁到 `assets` 后 DROP（分两步：先 rename 成 `_legacy_*` 一个版本周期，再删）。
- `canvas_resource_refs` 保留（文件级反查照旧）。
- `temp_resource_sweeper` 早已停用，代码删除。
- 智能文件夹"提示词库"是用户数据，不动；文档提示用 Assets → Prompts。

## 4. 迁移路径（一次性 backfill，幂等）

1. `project_characters` → `assets(type=character, scope_id = 项目所属 team 或个人 team, name, role_tag, description, tags, source='migrated')` + `asset_project_refs`；`portrait_url` 若能解析到 `resources` 行则写 `cover_file_id` 并挂 `unsorted`；建 Default loadout。
2. `project_lib_entities` → 同上，`entity_type` → `asset_type`，`badge_tag` → `role_tag`，`cover_url` 同处理。
3. 同 team 内**同类型同名**冲突：合并为一个 asset，refs 各指向各自项目（这正是跨项目复用的第一批受益者）；冲突记录写迁移日志。
4. 现有 `kind IN ('character','location','prop')` 画布：按名字规则 `"{name} · {Kind}"` 反解到 asset，写 `canvases.asset_id`；解析不到的保留为普通画布。
5. `generated_media.params.entity_kind/entity_id` → `source_asset_id`（按 1/2 的 id 映射表）；`promoted_resource_id IS NOT NULL` 的置 `review_state='saved'`；已被 `asset_files` 引用的置 `in_assets`。
6. Chat Uploads 登记（§3.7）。
7. `canvas_asset_refs` 从 `nodes_json` 重建（此时应为空，占位）。

每步可重跑；跑完对账：`count(assets where source='migrated') = count(project_characters) + count(project_lib_entities) − 合并数`。

## 5. API（`/api/v1`）

### 5.1 Assets

| 端点 | 说明 |
|---|---|
| `GET /assets?scope_id&type&project_id&readiness&tag&q&sort` | 图鉴列表；返回派生 `readiness / missing / project_ids / file_counts_by_slot / loadout_count` |
| `POST /assets` | 建实体；同名 → `409 {existing_asset_id}` |
| `GET /assets/{id}` | 实体页全量：files by slot、links（含反向 worn_by/held_by）、loadouts、project refs、used_in（canvas_asset_refs + storyboard 引用）、generation_history（generated_media by source_asset_id） |
| `PATCH /assets/{id}` | 头部字段 / 提示词 / attrs；system preset 拒绝 |
| `DELETE /assets/{id}` | 软删；画布节点回读得到 `asset_removed` |
| `POST /assets/{id}/duplicate` | 复制实体（文件关联复制、links 复制、loadouts 复制），`duplicated_from` |
| `POST /assets/{id}/files` `{resource_id, slot, loadout_id?}` | 挂文件（=归入资产）；批量 `{items:[…]}` |
| `DELETE /assets/{id}/files/{resource_id}/{slot}` | 解关联 |
| `POST /assets/{id}/links` `{to_asset_id, relation}` / `DELETE …` | 关系 |
| `POST /assets/{id}/loadouts` / `PATCH` / `DELETE` | 配装 |
| `POST /assets/{id}/prompt/translate` | 复用现有 translate agent |
| `POST /assets/{id}/prompt/regenerate` | 复用 vision agent，从主图槽反推 |
| `POST /assets/{id}/generate-slot` `{slot, loadout_id?, model?}` | "Generate missing"：用主图 + 一致性提示词 + 槽位模板生成，落 `generated_media(source_asset_id, params.target_slot)` |
| `GET /assets/{id}/loadouts/{lid}/bundle?model=` | **投递协议**（§6.3）：返回裁剪后的 `{reference_resource_ids[], prompt}` |
| `POST /assets/{id}/project-refs` `{project_id}` / `DELETE` | Link / Unlink |
| `GET /projects/{pid}/assets?type=` | 项目分级视图（= 上面 GET 的固定筛选） |
| `POST /projects/{pid}/assets/import-from-script` | 现有"一键导入"改落 assets + refs |
| `GET /canvases/{id}/asset-refs` / `GET /assets/{id}/canvas-refs` | 反查 |

### 5.2 Generated

| 端点 | 说明 |
|---|---|
| `GET /generated?scope_id&state&origin_kind&project_id&media_kind&model&since` | 扁平列表，默认 `state=unreviewed`；每项带 `source {kind, canvas_id, node_id, shot_id, label, deep_link}` |
| `POST /generated/{id}/save` | 现有 promote，且置 `saved` |
| `POST /generated/{id}/save-as-asset` `{asset_id \| new_asset:{type,name}, slot, loadout_id?}` | promote + `asset_files` + `in_assets`，一个事务 |
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
- **投递协议**（bundle）：生成节点上游有 asset 节点时，调 `/bundle?model=`，按 provider 能力表（`max_reference_images / aspect / multi_subject`）裁剪：优先级 主图槽 > 服装 worn > stills；超限的在节点上灰掉并提示 "seedream-4 accepts up to 3 references"。prompt 拼接顺序固定：`asset.prompt_positive` → `loadout.prompt_extra` → 各 costume/prop 的 `prompt_positive` → 场景 asset 的 `prompt_positive` → 用户文本；negative 取并集去重。**这层写成纯函数 + 单测**（每种 provider 一组用例），不散在节点里。
- Output 节点 → Generated；Output 的 As Asset… 预填 `source_asset_id + loadout_id`。
- 动作 **Insert project assets**：在任意画布上按类型四条 lane 铺出本项目引用的资产节点（RunningHub 式制作画布，零手工）；不自动建"官方制作画布"。
- 资产被删：节点显示 `Asset removed` 占位，不静默消失。
- 复制节点 = 复制引用（同 asset_id）。

### 6.4 项目工作区（屏 6 下半）

- 侧栏「素材」组：人物 / 场景库 / 道具 / **服装**（新）；数据源换 `GET /projects/{pid}/assets?type=`。
- 卡片 = `AssetCard`（同一组件），点进 `AssetSheetPage`；卡片上不再有大按钮「在画布中打开」。
- 顶部动作：Link from library（选择器，列出本 team 未关联的同类资产）/ + New / Import from script（横幅照旧）。
- `EntityAssetStrip` 退役，被实体页 Generation history 取代。

### 6.5 聊天 / agent

- Send to Agent：复用 `pendingResource` 通道，扩展为 `pendingAsset {asset_id, loadout_id}`；后端 `resolve_resource_refs` 增补 asset 引用 → 展开为主图 + 一致性提示词进 `<available_resources>`（走 `escape_frame_body`，并把 `<asset>` 框登记进 `OWNED_FRAMES`）。
- 聊天里 `@` 选择器加 Assets 一栏（v1 只做插入引用，不做 loadout 选择）。

## 7. 错误处理与不变量

- 归入资产的每条路径都返回类型化结果（成功 / 409 已存在 / 404 资源不可见 / 422 槽位不合法），前端逐条回显——沿用"触发路径必须类型化失败回显"纪律。
- `save-as-asset` 是一个事务：promote 失败不写 `asset_files`；写 `asset_files` 失败回滚 promote 状态（`review_state` 不变）。
- `canvas_asset_refs` 维护失败只记日志，不阻塞画布保存；backfill 可重建。
- readiness 永远派生，不缓存进列；列表端点用一条带 `EXISTS` 的查询算。
- 边界 mock 用真实 wire 形状：`assets.id` 与 `canvases` 一样在 router 显式 `str()`；`asset_files.resource_id` 同。测试 fixture 必须覆盖数字 id 分支（2026-08-12 教训）。
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
| **P4 画布** | asset 节点 / bundle 投递协议 / Output 预填 / Insert project assets / canvas_asset_refs | P2 |
| **P5 聊天 / agent** | pendingAsset / resolver / `<asset>` 框 / @ 选择器 | P2 |
| **P6 清尾** | 删 legacy 表 / EntityAssetStrip / tempResources / temp_resource_sweeper；My Uploads 右键与 Output 节点的 As Asset 入口 | P3 P4 |

P1 与 P2 可并行（不同 worktree）。

## 10. 风险

- **迁移同名合并误合**：不同项目里两个真的不同的"老张"。缓解：合并前列出清单人工过目；合并后的 asset `attrs.merged_from[]` 可拆回。
- **画布节点旧数据**：现有画布没有 asset 节点，无影响；`kind=character` 画布反解失败的留普通画布，不丢数据。
- **generated_media 体量**：Chat Uploads 登记会一次性加行；表本就设计为高流失层，索引 `(scope_id, review_state, created_at DESC)`。
- **投递协议 provider 能力表会过时**：放 `config.yml`，缺省保守（1 张）；模型目录已有 admin 页，后续可并入。
- **权限**：assets 沿用 team membership；system preset prompt 全局只读；跨 team 不可见（RLS + 后端 gate 双层）。
