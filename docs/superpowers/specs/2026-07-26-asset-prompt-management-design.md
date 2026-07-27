# Asset Prompt Management — 正/负提示词、触发标签、画板加载

- **日期**: 2026-07-26
- **状态**: 设计已过用户确认（UI mockup v4），待实现
- **UI mockup**: https://claude.ai/code/artifact/63c9c407-b95c-4e09-acfd-67f1bbb70621
- **调研参考**: A1111 `parameters` PNG chunk（事实标准）、Eagle ComfyUI Auto Tagger（标签+Note 双层落库）、Civitai generation data、OpenPromptStudio（中英对照词条）、Infinite-Canvas（画板 prompt 模板库范式）

## 1. 背景与现状

素材库已有一版**单条 prompt** 实现（IC-port P1, 2026-06-12）：

| 已有能力 | 位置 |
|---------|------|
| `resources.gen_prompt`（EN）/ `gen_prompt_zh`（ZH）两列 | mig 284 / 289 |
| 信息面板 Prompt 区块：EN/中切换、编辑、Copy | `ResourceDetailPage.tsx` |
| Translate 按钮（translation agent 中↔英互译） | `resources_ai_router.py::translate_gen_prompt` |
| Generate 按钮（vision agent 从图反推双语 prompt） | `resources_ai_router.py::generate_gen_prompt` |
| PNG 上传自动提取 A1111 `parameters` → `gen_prompt` | `png_prompt_extractor.py` + `upload_postprocess.py` |
| 画板 smart canvas：PromptNode / MediaNode / OutputLightbox | `features/canvas-core/` |

**本期缺口**：
1. 负面提示词被丢弃（extractor 解析到 `Negative prompt:` 截断，只存正向）
2. Prompt 区块常驻面板中段，长文本挤压布局
3. 无"哪些素材带 prompt"的网格可视标识
4. 无标签联动（打标签开启 prompt 入口 / 按标签筛 AI 素材）
5. 素材 prompt 无法进画板复用（IC 的模板库范式）

## 2. 需求（用户确认的最终形态）

1. **信息面板**：Prompt 区块移到 **Tags 之后、Properties 之前**；默认**折叠成一行预览**（长文本不挤布局）；点开 = 展开正/负双框编辑 + **自动补打默认触发标签**；展开态右下角 **Send to Canvas**。
2. **网格卡片**：带 prompt 的素材缩略图**右下角**显示 Prompt 角标（左上角留给多选 checkbox）；悬浮角标弹出预览（正/负截断 + Copy）。
3. **触发标签**：哪些标签算 "Prompt 标签" **用户自定义**（不限定 AI）；默认给一个 `AI` 起步；Settings 集中管理（右键菜单入口二期）。
4. **画板加载**：PromptNode 工具栏加 **Library** 按钮 → 「素材 Prompt 库」弹层（搜索 / 按触发标签筛 / EN·中切换）→ 加载生成**已连线的节点对**：MediaNode（素材缩略图，点开走 OutputLightbox 看大图，可当 i2i ref）+ PromptNode（正/负文本，可编辑）。反向入口 Send to Canvas 产出相同节点对。

## 3. 数据模型

### mig 384 — resources 负面提示词

```sql
ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_prompt_negative TEXT,
    ADD COLUMN IF NOT EXISTS gen_prompt_negative_zh TEXT;
```

- 命名对齐既有 `gen_prompt` / `gen_prompt_zh` 前缀。
- 字符上限沿用 `MAX_PROMPT_CHARS = 20000`（schema 层校验）。

### mig 385 — tags 触发开关

```sql
ALTER TABLE public.tags
    ADD COLUMN IF NOT EXISTS prompt_trigger BOOLEAN NOT NULL DEFAULT false;
```

- **不叫 `show_prompt`**：该名已被画板节点数据占用（`factories.ts` loop/output 的 `show_prompt`），避免语义撞车。
- 触发标签集合 = `SELECT * FROM tags WHERE prompt_trigger = true`（按用户/scope 既有隔离规则走 tags 现有 RLS/查询路径，无新表）。

### 素材 Prompt 库（无新表、无新端点）

画板 Library picker 数据源就是 `resources`，且**不加后端端点**——前端沿用 `resourceService` 的 Supabase 直查习惯（列表本来就是 `select('*')` + RLS）：

```ts
supabase.from('resources')
  .select('id, filename, thumbnail_path, cover_image_path, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh, updated_at')
  .or('gen_prompt.not.is.null,gen_prompt_zh.not.is.null,gen_prompt_negative.not.is.null,gen_prompt_negative_zh.not.is.null')
  .ilike(...)   // 搜索
  .limit(50 + 超采余量)
```

**过滤口径 = `hasPromptData` 四列语义**：gen_prompt / gen_prompt_zh / gen_prompt_negative / gen_prompt_negative_zh 任一 trim 后非空。实现分两层——Supabase 用 4 列 `.or(not.is.null)` 粗筛，客户端用 `hasPromptData` 精筛（PostgREST 不便表达 trim/空串组合）。负面-only 素材因此也进 Library；被清空成 `''` 的不再误列。
（修订记录：2026-07-27 Phase 3 统一；原口径只查正向两列，导致负面-only 缺席、空串误列——见 P2 终审 M2。）

按触发标签筛选复用现有 `resource_tags` 过滤路径（`fetchResourcesByTag` 同款 join）。scope 权限由 RLS 兜底。

## 4. 显隐规则（面板 + 角标共用）

| 素材状态 | 面板 | 卡片角标 |
|---------|------|---------|
| 有 prompt 数据（任一字段非空） | 折叠行 + 首行预览 | 显示 |
| 无数据、带触发标签 | 折叠行「+ Add Prompt」 | 不显示 |
| 无数据、无触发标签 | 轻量「+ Add Prompt」一行小字（现状样式） | 不显示 |

- **数据优先于标签**：有 prompt 数据的素材不管有没有触发标签都显示（旧数据不消失）。
- **点开任一入口** = 展开编辑区 + 自动补打**默认触发标签**（定义：`prompt_trigger=true` 中 `created_at` 最早的一个；一个都没有则自动创建 `type='user'` 标签 `AI` 并置 `prompt_trigger=true`——不用 system 类型，system 是全局种子标签（user_id NULL），按用户自动建会破坏 `UNIQUE(name,type,user_id)` 的 scope 约定）。
- 展开/折叠状态记内存（组件 state），不持久化。

## 5. 后端改动

### 5.1 png_prompt_extractor — 补负面提取

现状 `_SETTINGS_LINE_RE` / `Negative prompt:` 作为截断标记只取正向。改为返回三元组：

```python
def extract_prompt(path) -> Optional[ExtractedPrompt]:
    # ExtractedPrompt = (positive: str, negative: str | None, raw: str)
```

- A1111 `parameters`：`Negative prompt:` 行起、`Steps: \d` 行止的段落 → negative。
- ComfyUI graph：尽力而为——`CLIPTextEncode` 节点若有 `negative` 语义连接则取；取不到只填正向（不阻塞）。
- `upload_postprocess_workflow`：`gen_prompt_negative` 同 `gen_prompt` 一样**只在为空时写入**（never clobber）。

### 5.2 translate_gen_prompt — 扩展负面

`POST /resources/{id}/gen-prompt/translate` 请求体不变（`target_lang`），行为改为：**正/负两段都翻**（各自源字段非空才翻），一次调用写回
`gen_prompt_zh` + `gen_prompt_negative_zh`（或反向 EN 两列）。

### 5.3 generate_gen_prompt — 不动

图片反推负面词不可靠，Generate 仍只产正向双语。负面留手填/PNG 提取。

### 5.4 tags API

- `GET /tags` 响应加 `prompt_trigger` 字段。
- `PUT /tags/{id}` 允许更新 `prompt_trigger`。
- Settings 页的"+ Add tag"若输入的标签名不存在 → 创建（`type='user'`）并置 `prompt_trigger=true`。

### 5.5 resources API

- `PATCH /resources/{id}`（`ResourceUpdate`）加 `gen_prompt_negative` / `gen_prompt_negative_zh`（同 20000 字符上限）。
- **列表无需改动**：前端列表走 Supabase `select('*')`，prompt 四列天然在 payload 里；卡片角标与悬浮预览直接用行内数据，不加 `has_prompt` 计算字段、不做二次请求。

## 6. 前端改动

### 6.1 ResourceDetailPage（信息面板）

- Prompt 区块**移位**：Tags（EagleTagPicker）之后、Properties 之前。
- 折叠行：`✨ Prompt | <首行截断预览> ›`；无数据时按 §4 规则渲染。
- 展开态：正向框 + Negative 框（红色系弱化边框）、共用 EN/中 toggle、Generate / Translate / Copy / 收起（^）操作行、右下 Send to Canvas（Phase 2 前隐藏或禁用）。
- 点开自动打标：调用现有 addResourceTag，失败不阻塞展开（toast 提示）。
- Copy 语义：正/负各自 Copy；顶部 Copy 复制当前语言正向（保持现状习惯）。

### 6.2 FileCard（网格卡片）

- 行内 prompt 四列任一非空 → 缩略图右下角角标（半透明胶囊 `✨ Prompt`）。
- hover 角标（非整卡）→ 弹层：正向 3 行截断 + 负面 2 行截断 + Copy。数据就在行内，无需额外请求。
- 列表视图（list layout）暂不加角标（信息密度低收益，二期看）。

### 6.3 Settings → Library

- 新增 "Prompt Trigger Tags" 卡片：chips 列出 `prompt_trigger=true` 的标签，`✕` 取消（置 false），"+ Add tag" 选择已有标签或新建。
- 文案英文（UI 语言规范），i18n key `settings.promptTriggerTags.*`。

### 6.4 i18n

`resources.infoPanel.*` 新增：`negativePrompt`、`sendToCanvas`、`promptCollapsed` 等 key，en/zh 两份。

## 7. 画板（Phase 2）

### 7.1 Library 按钮 + AssetPromptPicker

- `PromptNodeView` 工具栏加 Library 按钮（icon: library）。
- 弹层：搜索框 + 触发标签 filter chips + EN/中 toggle + 缩略图列表（`GET /resources/prompts`）。
- 选中一条 → 通过 `factories.ts` 创建：
  - MediaNode：`items=[{url: 素材图 URL, name: filename}]`（复用现有 MediaNodeData，点击缩略图 → OutputLightbox）
  - PromptNode：`text = 当前语言正向`；负面文本放 PromptNodeData 新增可选字段 `negative_text`（生成管线支持 negative 的 provider 才消费，其余忽略——实现时在 genSlots/generationRunner 里对齐）
  - Connection：media → prompt（i2i ref 语义，沿用现有连线类型）

### 7.2 Send to Canvas（素材侧反向入口）

- 展开态按钮 → 画板选择器（该素材所属项目的 canvases；无项目归属时禁用 + tooltip 说明）。
- 选定后调 canvas 节点 API 插入同样的节点对，然后跳转画板并聚焦新节点。

### 7.3 右键菜单入口（触发标签就地开关）

- EagleTagPicker 标签行 context menu 加 "Show Prompt Panel" 勾选项（写 `prompt_trigger`）。

## 8. 阶段划分

| 阶段 | 内容 | 交付 |
|------|------|------|
| **Phase 1（本期 PR）** | mig 384/385、extractor 负面、translate 扩展、面板移位+折叠+自动打标+负面框、卡片角标+悬浮预览、Settings 触发标签、`/resources/prompts` + `has_prompt` | 一个 feature PR |
| **Phase 2** | 画板 Library picker、节点对工厂、Send to Canvas、右键菜单开关、PromptNodeData.negative_text 管线对齐 | 独立 PR |

## 9. 测试

- `png_prompt_extractor`：A1111 含负面样例、无负面样例、ComfyUI graph 样例、恶意超长 chunk（沿用现有 bound 测试风格）。
- `translate_gen_prompt`：正负都有 / 只有正向 / 目标语已存在（覆盖写）三例。
- 面板组件测试：折叠→点开展开 + addResourceTag 被调用；显隐规则三分支。
- FileCard：角标渲染（四列任一非空）+ hover 弹层。
- tags API：`prompt_trigger` 读写。
- Prompt 库查询（frontend service）：`or` 过滤 + 标签 join 过滤单测。

## 10. 非目标（明确不做）

- 词条级中英词库（OPS 范式）——独立功能，另行立项。
- 生成参数（Steps/Sampler/CFG/Seed/Model）解析入库——future：`resources.generation_params jsonb`，本期 extractor 不解析 settings 行。
- 下载详情侧（MediaCard / DownloadDetailPage）的 prompt 区块——本期只动上传/导入素材的 ResourceDetailPage。
- prompt 版本历史（Langfuse 范式）。
- 列表视图角标。
