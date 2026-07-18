# 全域标签统一（Unified Tags）设计 spec

日期：2026-07-17
状态：已与用户逐项确认（统一程度 / 入池策略 / 热点匹配 / 影子标签制）

## 1. 背景与目标

仓库现有三套互不相通的"标签"实现：

| 系统 | 存储 | 特点 |
|---|---|---|
| 资源标签 | `tags` 表（BIGINT Snowflake）+ `resource_tags` 关联 | 唯一正规体系：color/icon/`tag_groups` 分组/personal-team scope/merge RPC/starred 偏好 |
| 笔记标签 | `inspiration_notes.tags TEXT[]`（mig349） | 从正文 `#tag` 解析的自由文本；前后端镜像解析器（`note_tags.py` ↔ `noteTags.ts`）；无表 |
| 热点标签 | `hotspots.tags TEXT[]`（mig304） | topic-scorer LLM 生成的字符串 chip；热点数据本身全局共享（user_id 可空） |

**目标**：`tags` 表成为全域唯一标签实体。资源（已有）与笔记（新增关联表）真实挂接；热点保持 TEXT[]，查询期按名字对齐用户标签池。用户可在一处管理标签并跨域（资源+笔记+热点）筛选、统计。

## 2. 已拍板的决策（勿翻）

1. **数据层真统一**（非聚合层、非纯 UI 入口）。
2. **笔记入池策略 = 影子标签制**：
   - 笔记保存时解析 `#tag`，同名（`lower(name)` 匹配）命中池内已有标签 → 直接挂接；
   - 无命中 → 自动创建 `origin='note'` 的**影子标签**（type='user'，personal scope）；
   - 影子标签**默认不出现在资源打标签 picker / 补全**中；管理页单独区域展示，可一键晋升为正式标签。
3. **热点只匹配不新建，且为查询期名字匹配**：热点零 schema 改动，无 hotspot_tags 表，无定时物化任务；筛选/统计时以 `lower(name)` 对齐用户可见标签池。
4. **改名/合并不改写用户笔记正文**（不动用户内容是底线）。笔记标签真源永远是 `content_md`，行为见 §6。

## 3. Schema 变更（一个新 migration，编号取当时最新可用）

### 3.1 `tags.origin` 列

```sql
ALTER TABLE tags ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'curated'
  CHECK (origin IN ('curated', 'note'));
```

- 存量行全部 `'curated'`（默认值即达成）。
- 晋升 = `UPDATE tags SET origin='curated'`（单列 PATCH，已有 tags PUT 端点扩展）。
- 不做"降级"（curated → note）。

### 3.2 `note_tags` 关联表（镜像 `resource_tags` 范式）

```sql
CREATE TABLE note_tags (
  note_id BIGINT NOT NULL REFERENCES inspiration_notes(id) ON DELETE CASCADE,
  tag_id  BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (note_id, tag_id)
);
```

- RLS：owner-only，经由 `inspiration_notes.user_id = auth.uid()` 子查询（沿用 mig349 的 RLS 模板）。
- 索引：PK 覆盖 note→tags；另加 `idx_note_tags_tag_id`（tag→notes 反查，管理页用量用）。
- **保留 `inspiration_notes.tags TEXT[]`**：作为解析缓存（列表页展示零 join）。真源是 `content_md`，TEXT[] 与 note_tags 都是派生数据，漂移在下次保存时自愈。

### 3.3 回填（migration 内或伴随脚本，幂等）

遍历存量笔记的 `tags TEXT[]`：按 §5 的匹配规则为 owner 挂接或创建影子标签，写 `note_tags`。回填创建的新标签一律 `origin='note'`。

### 3.4 SQLAlchemy model 同 PR

schema-drift gate 铁律：`note_tags` model + `tags.origin` 列同 PR 加入；FK 声明 `ondelete='CASCADE'`（FK gate）。

## 4. 后端变更

### 4.1 笔记保存管线（`inspiration` service）

现有：保存/更新时 `parse_tags(content_md)` → 写 `tags TEXT[]`。追加：

1. `resolve_note_tags(user_id, names)`（TagsRepository 新方法）：
   - 对每个 name，按 §5 匹配用户可见池；无命中则创建影子标签（幂等：`unique_tag_per_scope` 约束 + upsert 容错并发）。
   - 返回 tag_id 列表。
2. diff 同步 `note_tags`：与现有关联行比对，只插入新增、删除消失的（不整表重写）。
3. 与笔记保存同一事务边界；标签同步失败 = 保存失败，显式报错（不静默吞）。

解析器契约（前后端镜像、小写化/fence 剥离/去重保序）**完全不动**。

### 4.2 匹配规则（§5 详述）供 statistics / topics 复用

### 4.3 统计端点扩展

`GET /api/v1/tags/statistics` 每个标签返回跨域用量：

```json
{ "tag_id": "…", "resources": 12, "notes": 5, "hotspots": 3 }
```

- resources：`resource_tags` 计数（现状）。
- notes：`note_tags` 计数（scope 内）。
- hotspots：近 N 天（默认 30，参数可调）按 §5 名字规则匹配计数（name 与 name_zh 各生成一个候选词，`tags && ARRAY[候选词]`）；热点量大，必须限窗。

### 4.4 热点标签筛选

`GET /api/v1/topics` 增加 `tag_id` 参数（逗号分隔标签 id）：后端解析出各标签的 `name` + `name_zh` 作为匹配词集合，`tags && ARRAY[...]`（overlap，GIN 索引 mig304 已有），与现有 source/category/view 筛选正交组合。传 id 而非裸词，保证筛选语义与统计口径（§4.3）一致。

### 4.5 merge RPC / 删除语义

- `merge_tags` RPC（mig267）扩展：迁移 `resource_tags` 之外同时迁移 `note_tags`（同款去重逻辑）。
- 删除标签：`note_tags` 级联删除即可；笔记正文中的 `#词` 不动（§6 行为）。

## 5. 名字匹配规则（唯一权威定义，三处复用）

输入 name 一律 `lower()` 后匹配，**同时比对 `lower(name)` 与 `lower(name_zh)`**（笔记/热点大量中文词，只比英文 name 会漏接，PR-0 已证实这类盲区）。候选池 = 用户可见标签：

1. 优先：本人 user 标签（personal scope，含影子）中 `lower(name)` 或 `lower(name_zh)` 相等者（name 命中优先于 name_zh 命中）；
2. 其次：system / time 标签中同规则相等者；
3. 多候选（大小写变体并存）：取 `created_at` 最早者（确定性）；
4. 无命中：创建影子标签（`name` 取解析后的小写原文——中文即中文原词，`type='user'`，`origin='note'`，personal scope）。

team scope 标签**不参与**笔记自动匹配（笔记是 owner-only，自动挂 team 标签会把私人笔记词与团队标签体系耦合；用户要用团队标签可先晋升/手动建同名个人标签）。

## 6. 语义规则（文档化的已知行为，非 bug）

1. **改名**：管理页把标签 `ai` 改名 `artificial-intelligence` → 关联行不变；但笔记正文仍是 `#ai`，该笔记下次编辑保存时会重新解析出 `ai` → 无命中 → 新建 `ai` 影子标签。改名不回写正文，此行为在 UI 提示（改名弹窗对含笔记用量的标签显示一行说明）。
2. **删除**：同理，正文里的 `#词` 会在下次保存时复活为影子标签。
3. **晋升**：单向（note → curated），晋升后该标签进入资源 picker/补全。
4. **热点侧只读**：热点卡片上的标签 chip 不可增删（TEXT[] 是 AI 产物）；能做的是"按我的标签筛热点"。

## 7. 前端变更

1. **笔记 `#` 补全**（`NoteEditor` tagSuggestions）：建议源从"历史词频"升级为"我的标签池"（curated + 影子 + system），curated 带颜色 chip 优先展示。打新词体验不变。
2. **资源 picker / 补全**（EagleTagPicker 等）：默认过滤 `origin='note'`。`unifiedTagService` 列表接口透传 origin，picker 端过滤。
3. **Tags 管理页（Settings）**：
   - 正式标签区照旧 + 每标签跨域用量（resources/notes/hotspots 三数字）；
   - 新增 "From notes" 折叠区：影子标签列表 + Promote 按钮（一键晋升）+ 用量；
   - 改名/删除弹窗对含笔记用量的标签追加说明文案（§6.1）。
4. **热点筛选**（TopicFilterBar）：新增 Tag chip（复用 FilterChip），选项 = 我的标签池中在当前窗口内有热点命中的标签；按 name 传 `tag` 参数。
5. **笔记 TagsPanel**：现有词频列表可保持（读 TEXT[] 聚合 RPC 不变），后续迭代可显示池标签颜色（非本期必须）。
6. UI 文案全英文 + i18n key（Promote / From notes / Filter by tag 等）。

## 8. 错误处理与边界

- `resolve_note_tags` 并发创建同名：靠 `unique_tag_per_scope` 唯一约束兜底，冲突时重读取现有行（幂等）。
- 标签同步失败：笔记保存事务整体失败，前端收到明确错误（不静默、不半写）。
- 回填幂等：`ON CONFLICT DO NOTHING`（note_tags PK）+ 匹配规则先查后建。
- 热点 `tag` 参数：服务端 `lower()` + 白名单字符校验（沿用现有 `sanitize_search` 思路），防注入。
- BIGINT 精度：tag_id 序列化 string（现有 schema 注释纪律），前端经 `bigIntSafeFetch`。

## 9. 测试

- 后端：`resolve_note_tags` 匹配四规则各一测 + 并发冲突幂等；保存管线 diff 同步（增/删/不变）；回填幂等；statistics 三域计数；topics tag 过滤；merge RPC 覆盖 note_tags。repo 测试断言绑定对象类型（date/datetime 纪律）。
- 前端：picker 过滤 origin；补全建议源；管理页晋升交互；TopicFilterBar tag chip。
- 解析器镜像契约测试不动（零改动即回归）。

## 10. 交付分期（trunk-based，每 PR ≤1 天）

| PR | 内容 | 用户可见变化 |
|---|---|---|
| PR-0 | 【独立 bug 修】资源页 Add Tag 选择器中文输入回车无法创建标签（用户 2026-07-17 报）。已知代码缺陷：`EagleTagBrowser.tsx` Enter 处理无 `e.nativeEvent.isComposing` 守卫（中文 IME 上屏 Enter 与确认 Enter 纠缠）；`noExactMatch` 只比对 `name` 不看 `name_zh`。修复前先真实浏览器复现确认根因（systematic-debugging，不盲修）；顺带全仓 grep 其他无 IME 守卫的 Enter 提交输入框一并修（fix-the-class 纪律） | 中文输入回车可直接建标签 |
| PR-1 | migration（origin 列 + note_tags + RLS）+ model + 回填 + 保存管线同步 + merge RPC 扩展 | 无（透明） |
| PR-2 | statistics 跨域用量 + topics `tag` 筛选参数 + tags 列表透传 origin | 无（端点就绪） |
| PR-3 | 前端四处：picker 过滤 / 笔记补全 / 管理页 From notes+Promote / 热点 Tag chip | 全部 |

PR-3 体量若超一天，拆 3a（管理页）/ 3b（热点筛选 + 补全）。改动均为增强型，无需 feature flag；如需保险，管理页 From notes 区可后置。

## 11. 明确不做（YAGNI）

- hotspot_tags 物化关联表 / 定时匹配任务；
- 改名/合并回写笔记正文；
- 标签降级（curated → note）；
- 笔记标签接入 team scope 自动匹配；
- 热点标签手动增删。
