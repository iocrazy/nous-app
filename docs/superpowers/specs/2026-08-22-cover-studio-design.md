# 封面工作室（Cover Studio）—— 设计与地面真值

2026-08-22。分支 `feat/cover-studio`，worktree `.worktrees/feat-cover-studio`。
设计稿 v3（已定）：<https://claude.ai/code/artifact/2cc195f1-e52f-4680-b3da-60a1fef4462c>

本文件分两部分：**已核实的事实**（每条都是当场跑命令查的，不是回忆）和**设计决策**。
下一段会话拿到它就不必重新调研。

---

## 一、已核实的事实

### 1.1 `style_templates` 不是模板库，它是 `skills` 的空壳前身

线上存在、**0 行**、只有 `prompt_content`（纯文字），没有任何图片列。

来历查清了：mig 113 建 `style_templates` → mig 114 `RENAME TO skills`。但 113 是
`CREATE TABLE IF NOT EXISTS` 且**在改名之后被重放过**，于是原样复活了一个空壳，
从此与 `skills` 并存（`schema_baseline.sql:7476` 与 `:7331`）。后端至今留着与之配套、
但**零生产调用方**的 model / repository / schemas，router 是个 301 跳转桩。

→ 不要复用，不要"修好它"。样图模板库是新表（mig 435）。

### 1.2 ★ 参考图必须是 `generated_media` 行 —— 这条决定了整个数据模型

`canvas_generation.py:136-149` 把 `params.source_urls` 每一项交给
`generated_media_service.generated_media_local_path()`，而后者只认这一个正则：

```
/generated-media/(\d+)/(?:cover|stream|file)$
```

不匹配 → 返回 `None` → **静默丢弃**（`:507-510`）。除 codex 会记一条 warning 外
**没有任何信号**。

后果不是报错，是"模板选了、也生成了、图里没有它"，而且查不出为什么 —— 与
`reference-empty-output-is-not-a-negative-result` 同族。

→ 封面工作室那个 9 张的参考池（视频帧 + 模板样图 + 人物参考），**每一张都必须
先成为 `generated_media` 行**。现成入口两个，都已存在：

| 来路 | 端点 | 前端封装 |
|---|---|---|
| 直接上传 | `POST /api/v1/generated-media/import`（50MB） | `importCanvasMedia(file, canvasId, nodeId)` |
| 从素材库选 | `POST /api/v1/generated-media/import-from-resource` | `importResourceAsCanvasMedia(resourceId)` |

`import` 的 docstring 自己就写了这条约束：*"The canvas generation bridge can only
read durable /generated-media/ URLs, so uploads must land in the same store as
generations."*

### 1.3 ★ codex 的 `--size` 根本不被采纳（**2026-08-23 更正，此前本节写错了**）

| catalog `name` | provider / model | enabled | owner | 3:4 实际落到 |
|---|---|---|---|---|
| `codex-image` | codex / gpt-5.4 | ✓ | 用户本人 | **1024×1536 = 2:3，不是 3:4** |
| `jimeng-cli-image` | jimeng-cli / 5.0 | ✓ | 用户本人 | `3:4` 真 3:4 |
| `mediahub-doubao-seedream-t2i` | doubao / `doubao-seedream-3-0-t2i-250415` | ✓ | 平台 | 864×1152 真 3:4 |

⚠️ **本节原先照抄了 `codex_cli.py:59-60` 的注释**（"The model supports exactly
three sizes (square / landscape / portrait); every catalog aspect maps to the
nearest."）并据此断言"codex 出不了真 3:4"。**那条注释是错的，我的转述也是错的。**
2026-08-23 实测推翻：

| 探针 | 结果 |
|---|---|
| `--size 999x999` | CLI 回 "must use width and height values that are **multiples of 16**" —— 不是"只有三种尺寸" |
| `--size 8192x10912` | "maximum edge of **3840px**" —— 有上界，但远不止三档 |
| `--size 1056x1408` + 中性 prompt | `ok:true`，产物 **1536×1024 横版** |
| `--size 1024x1536`（规范竖版）+ 中性 prompt | `ok:true`，产物**仍是 1536×1024 横版** |
| `--size 1024x1536` + **竖版语义 prompt** | 产物 **1086×1448 = 正好 0.7500 = 真 3:4** |

结论：**`--size` 在这条链路上不被采纳，尺寸由 prompt 决定**；而 codex **能**出真
3:4 —— 上面那张就是。（机理推测：这个 provider 走的是 ChatGPT 私有后端
`chatgpt.com/backend-api/codex/responses`，不是标准 images API，size 参数在那条路
上没有落点。见 `docs/runbook/codex-image.md`。）

⚠️ **这是一个已上线的静默缺陷，影响所有 codex 出图**，不只封面。生产库里仅有的
4 张 codex 图，**没有一张符合请求的比例**：

| generated_media id | 请求 | 实际 | 比值 |
|---|---|---|---|
| 340306475426428 | 16:9 | 1184×1328 | 0.89 **竖版** |
| 340306472342133 | 16:9 | 1147×1371 | 0.84 **竖版** |
| 340305777021402 | 16:9 | 1199×1312 | 0.91 **竖版** |
| 340107793822620 | 1:1 | 1122×1402 | 0.80 |

三张要横版 16:9 全部回了竖版，四张尺寸各不相同、无一落在那三个"规范尺寸"上 ——
`_ASPECT_TO_SIZE` 这张表在这条链路上是**装饰性的**。

**修法方向**（未实施）：codex 分支要把画幅写进 prompt，而不是指望 `--size`；并且
产出后应校验实际尺寸，与请求不符要能报出来，而不是像现在这样 `ok:true` 了事。

⚠️ `mediahub-doubao-seedream-t2i` **没有任何迁移**，只是 prod 上 admin 手插的行 ——
全新库 / CI 上不存在。

### 1.4 ★ 封面 skill 在 RLS 下对它自己的 owner 都不可见

`skills` 的 SELECT 策略是 `is_public = true OR team_id IN get_user_team_ids(auth.uid())`
—— **没有 `created_by = auth.uid()` 分支**。而 `viral-video-cover` 是
`team_id=NULL, project_id=NULL, is_public=false`，两个条件都不满足。

实测（以 `authenticated` 角色 + owner 的 jwt sub 开事务查）：

```
visible_as_owner=0
```

→ 前端**绝不能**走 `supabase.from('skills')` 直读，会拿到空列表且**不报错**。
必须走后端 `GET /api/v1/ai-library/skills/{slug}`（服务端 service-role，
`list_accessible` 里才有 `created_by=user` 分支）。

### 1.5 ★ 做成独立页面会丢掉整张发布表单

`PublishPage.tsx` 有 **~75 个 `useState`**（第 528-944 行），**没有任何持久化**：
无 `useParams` / `useLocation` / `useSearchParams` / `sessionStorage` / `localStorage`。
路由是 `/team/:teamId/distribution/publish`（`router.tsx:289`）。

→ 用户填完标题/描述/话题/账号/音乐/定时后跳去 `/cover-studio`，再点
"Back to publish" 回来，**全部清空**。设计稿头部那句 "For clip-a.mp4 · topic … ·
publishing to iocrazy" 也没有任何现成传递通道。

→ 结论见 §2.4。

### 1.6 封面的落点是 `resources`，而抽帧候选是临时物

- `publish_tasks.cover_vertical_resource_id` / `cover_horizontal_resource_id`
  都是 BIGINT → `resources.id`（mig 356）。发布时**竖版优先、横版兜底、只用一张**
  （`publish_distribution.py:353-356`），`publish_gate.py:166` 也只要求二者有其一。
  → AI 封面只产 3:4 竖版可行；但**不能**对一张 3:4 图中心裁 4:3（会把脸裁没）。
- 抽帧候选**不落库**：预览图以 base64 随 `task_tracking.metadata` 送前端
  （2026-08-18 改，因为曾污染用户文件夹）。成品封面才是真 `resources` 行。
- 「同一秒数重抽 = 同一帧」**已实测**（跨 mp4/mkv/mov/webm、稀疏关键帧+B帧、VFR，
  三次逐字节相同 JPEG）。单点 builder `video_frame_extractor.seek_frame_cmd:86`。
- 已有 `_reextract_frame_from_storage`（任意时间点重抽）—— 正是「手动截」要的。

### 1.7 那张占位卡片

`PublishPage.tsx:2638-2654`。三重失活：`aria-disabled` + `preventDefault` +
CSS `pointer-events:none`（`distribution-v4.css:369`）。`#cover-studio` 是死锚点。
被删的三张渐变假候选留下孤儿 CSS：`.cover-cands` / `.cand`（`:370-371`）、
`.cover-slot.is-soon`（`:333-336`）。i18n key 已存在（`en/zh.json:472-476`）。

### 1.8 可直接复用、不要重造的现成件

| 要做的东西 | 用现成的 |
|---|---|
| 播放器 + 可拖时间轴 + **已截帧的圆点** | `VideoPlayer.tsx`，`commentMarkers` 就是那些圆点，另有逐帧步进 |
| 素材库图片选择器 | `chat/ResourcePicker.tsx`（加 `types:['image']` 过滤即可） |
| 拖拽上传 | `hooks/useComposerDropzone.ts:30`，`rootProps` 摊到任意容器 |
| 「模态里传图并创建一个实体」的先例 | `GalleryUploadDialog.tsx` |
| 模态 / 卡片 / 下拉 | `components/ui/primitives.tsx`：`UiModal:699`、`UiPanel:149`（就是 Card）、`UiSelect:218` |
| 开关 / 滑块 / Tabs | **没有**共享组件，分别抄 `PermissionsSection.tsx` 的 `role="switch"`、裸 `<input type="range">`、`AILibraryTabs.tsx` |

配色只能用语义 token（`ink-*` + `{ok|warn|danger|info|agent}` 三件套），有
`index.css.test.ts` 盯着；图标只用 lucide-react，**UI 禁 emoji**。

### 1.9 其他会绊人的细节

- 图片读 `params["ratio"]`，视频读 `params["aspect"]` —— 名字不一致，写错**静默变正方形**。
- `POST /canvases/{id}/generations` 的 `count` 被 clamp 到 1..8；阶段一要 `count=1`（一张 2×2 网格）。
- 9 张上限在 `canvas_generation.py:141` 与 `codex_cli.py:222` 各钉一次，前端无限制。
- `/generated-media/{id}/cover` **无鉴权**（按 snowflake 世界可读，供裸 `<img>`）。
- `resources` 表**没有** `metadata` 列（插了会 PGRST204 500）。
- promote 是**另存一份拷贝**，不是移动；删 generation 不动已 promote 的库资产。
- codex：`--background opaque` 必需（`auto` 会绿底渗色）；远程 URL 参考图被丢弃只吃本地文件；
  `auth.json` 必须可写（只读挂载是静默断裂）。
- ⚠️ `seed_loader._upsert_skill` 对用户导入的 skill **没有 owner 守卫**
  （`get_by_slug` 是裸 `WHERE slug=`）。当前安全（种子目录只有三个 `script-*`），
  但将来加同名种子目录会静默覆盖用户那一行并删掉其 `references/`。

---

## 二、设计决策

### 2.1 已由用户拍板（照做，不再讨论）

风格**单选**；参考帧**手动截**（播放器 + 时间轴 + 抓帧）；选中封面后**回填发布页 +
同时存入素材库**（两件事都要在界面上写出来）；模板图来源**素材库选 + 直接上传两者都要**；
**两阶段**生成（一张 2×2 网格 4 草案 → 选编号 → 二次精修）；人物参考图**从自己视频截**；
只管**竖版 3:4**，横版灰掉并说明原因；视频帧与模板样图**共用同一个 9 张池子**。

### 2.2 模板表锚在 `generated_media`，不是 `resources`

理由见 §1.2。另白拿三件事：缩略图可裸 `<img>`（`/cover` 无鉴权）；内容寻址天然去重；
两条来路都有现成后端。`source_resource_id` 只做溯源，**刻意不加 FK**（素材被删/移走
不该让模板消失）。

### 2.3 删除是硬删除，外键是 RESTRICT

三种组合都试过，只有这一种没有隐藏状态：

| 方案 | 后果 |
|---|---|
| `ON DELETE CASCADE` | 用户在画布里删一张图，模板库里对应模板**无声消失** |
| 不加 FK | 模板指向不存在的行 = 永远加载失败的破图 |
| RESTRICT + **软**删除 | 归档行仍握着外键 → 用户几个月后删那张图被拒，理由里点名一个他**已经删掉、界面上再也看不见**的模板 |
| **RESTRICT + 硬删除**（采用） | 删除被引用的图被 DB 拒绝，由 API 层翻译成类型化 409 并点名模板 |

软删除通常换来的两样东西在这里都不成立：① 没有任何表引用 `cover_templates.id`
（生成记录存的是 URL 不是模板 id），硬删不会让任何记录指向虚空；② `usage_count`
也保不住（删掉再重加同一张图本来就是新一行、从 0 计数）。

那个 409 用 `ConflictError`（`app/core/exceptions.py`）而不是
`HTTPException(detail={...})`：共用 handler 把 AppError 渲染成
`{error: message, code, details}`，而 dict 型 detail 会被塞进 `details` 并把
`error` 留成通用的 "Request failed" —— 用户读到的就是后者，那正是这条分支要避免的。

### 2.4 ⚠️ 建议：封面工作室做成发布页内的**全屏浮层**，不是新路由

理由见 §1.5：新路由会丢掉 75 个 `useState`。浮层视觉与设计稿完全一致
（"Back to publish" = 关闭浮层），上下文天然拿得到，表单天然不丢。
**尚未实现，等确认。**

### 2.5 ~~未决：3:4 与 codex 的冲突~~ → 已消解（2026-08-23）

**这一节原先的前提是错的。** 它建立在"codex 物理上出不了 3:4"之上，而 §1.3 的实测
证明 codex **能**出真 3:4（实测 1086×1448 = 0.7500）。原来列的三个选项（换默认模型 /
后处理裁切 / 接受 2:3）**全部作废**，不需要再拍板。

真正要做的是另一件事，而且它比原来那个问题更严重：**`--size` 不被采纳，画幅必须写进
prompt**。这对封面工作室是好消息 —— skill 的 prompt 骨架本来就通篇在说 3:4，只要把
画幅要求确实带进 prompt 就行，不用换模型、不用后处理。

⚠️ 但要连带修 `_ASPECT_TO_SIZE` 那条链路的静默失败（§1.3 末尾），否则"用户选了比例、
系统装作照做了"这件事会一直存在。

### 2.6 ⚠️ 小标签开关：设计稿的说明文字写反了

两份原文确实矛盾（这一点设计稿是对的）：
- `SKILL.md` 第 4 步：*"Do not add small platform-style labels, recording marks,
  search UI, badges, or corner tags unless the user explicitly asks for them."*
- `cover-grammar.md` 标题即「v1 深色人物版，**小标签仍允许**」，正文：
  *"可用小标签增强平台感：如 "101 Shorts""REC""搜索框" 等，但必须做成通用原创元素"*

但设计稿那句 **"Off follows the newer file"** 是错的：**允许**的是 grammar，
**禁止**的是 SKILL.md。默认关 = 跟随 SKILL.md。实现时文案要改成
"Off follows SKILL.md, which forbids them"，别照抄设计稿。

---

## 三、本波已落地（模板库）

| 文件 | 内容 |
|---|---|
| `supabase/migrations/435_cover_templates.sql` | 新表，决策理由写在文件头 |
| `backend/app/models/cover_templates.py` | ORM 模型（+ 注册进 `models/__init__.py`） |
| `backend/app/repositories/cover_templates_repository.py` | 数据访问；`names_blocking_media()` 是删图前的引用检查 |
| `backend/app/schemas/cover_template.py` | Pydantic（id 一律 str） |
| `backend/app/api/cover_templates_router.py` | 5 个端点（+ 注册进 `api/__init__.py`） |
| `backend/app/api/generated_media_router.py` | `delete_generation` 增类型化 409 |
| `frontend/services/coverTemplateService.ts` | 客户端 + 类型化失败类 |
| `frontend/features/canvas-core/smart/mediaImport.ts` + `types.ts` | 把后端一直在返回、前端从没读过的 `id` 接上 |

### 第二波（模板库 UI）

| 文件 | 内容 |
|---|---|
| `frontend/components/Distribution/CoverStudio/cover-studio.css` | 作用域样式，token 全部复用 `index.css` |
| `.../CoverTemplateGrid.tsx` | 「Templates」卡片：网格 + 用量 + 删除 + Add 磁贴 |
| `.../AddCoverTemplateModal.tsx` | 两条来路合一：上传（拖拽/选文件）+ 素材库选 + 命名 |
| `frontend/services/distributionService.ts` | 拆出 `listLibraryMediaOrThrow`（见下） |
| `frontend/public/locales/{en,zh}.json` | `distribution.coverStudio.*`，22 个 key，两侧结构一致 |

**刻意做成容器无关**：封面工作室最终是路由还是浮层（§2.4）尚未拍板，这两个组件
不该被那个决定作废。

**`listLibraryMediaOrThrow` 为什么要拆**：`listLibraryMedia` 出错时 catch 掉返回
`[]`，于是"加载失败"和"你没有图片"在调用方眼里完全一样。发布页容忍这一点（空网格
在那里读作"没东西可选"），但模板库必须把两者分开说，所以新增一个会抛错的孪生导出，
既有调用方行为不变。

**组件里发现并修掉的一个 silent no-op**：删除模板失败后会重新拉列表，而
`load()` 在入口清掉错误标志 —— 于是卡片自己回来了、**没有任何解释**。现在删除失败
有独立的状态与文案（"这个模板没能移除 —— 它还在"），且在 reload **之后**置位。

### 第三波（9 张参考池）

| 文件 | 内容 |
|---|---|
| `.../coverReferences.ts` | 池子的规则（纯函数，可单独测）：九张上限、去重、人物槽**替换而非追加**、`source_urls` 顺序 |
| `.../CoverReferencePool.tsx` | 「References — n / 9」卡片 |

三条不显然的规则，都钉了测试：

1. **九张是共用的**，视频帧与样图不分两份预算 —— 模型看到的是一个扁平数组，分两份
   会让用户把两边填满、然后**悄悄丢掉溢出的那些**。
2. **人物槽是替换，不是追加**。「再截一张更好的自己」不该吃掉第二个槽位，所以即使
   池子满了也允许换人物。
3. **被拒绝的添加必须说明是哪一种拒绝**（满了 / 重复）。按了「添加」却什么都没发生，
   是这个仓库反复重学的那类故障。

渲染侧另外两条：人物槽**没有删除按钮**（它由风格保留，给个 ✕ 等于邀请用户制造风格
自己称之为 blocker 的状态）；满九张时 Add 磁贴**置灰而不是消失**（消失会让用户到处
找它），同时计数变红。

### 第四波（手动抓帧的后端）

`POST /api/v1/distribution/covers/grab-frame` —— 与 `/covers/select` **共用抽帧那
一半**，只有落点不同：

    select      重抽 → 居中裁 3:4 + 4:3 → 两个 resources 行（成品封面）
    grab-frame  重抽 →   不裁          → 一个 generated_media 行（参考图）

落点不能对调（理由见 §1.2）。**不裁**也是刻意的：这一帧说的是"我片子里有什么"
（人物长相、场景），裁掉边缘只丢信息；构图由模型按 3:4 重新生成。

⚠️ **顺带修掉一个仓库级的既有缺陷**：非有限浮点会把它自己的 422 变成 500。
FastAPI 解析请求体用 `json.loads`，它**接受**裸的 `NaN` / `Infinity` 记号；pydantic
正确拒绝后，错误体里原样带着那个 `input`，而 Starlette 用 `allow_nan=False` 渲染
JSON —— 序列化当场抛异常，调用方拿到一个不知所云的 500。
`app/core/exceptions.py` 的 handler 现在会递归清洗非有限值（转成文本而不是丢弃，
"输入超出范围"却不说输入是什么是更糟的错误）。
这跟该 handler 里 `jsonable_encoder` 那句注释记的是**同一类 bug 的另一个变体**。
`/distribution/covers/select` 的 `timestamp_seconds`（同样是 `float, ge=0`）从上线
起就有这个洞。

另一条已钉成测试的事实：`inf >= 0` **为真**，所以 `ge=0` 拦不住 `+Infinity` ——
约束浮点的 schema 不足以保护下游，凡是要把这个数交给子进程/文件系统的地方都得自己
`math.isfinite`（grab-frame 有）。

### 第五波（两阶段 prompt + 生成入口）

| 文件 | 内容 |
|---|---|
| `backend/app/services/distribution/cover_prompt.py` | skill 两个骨架的原样落地 + 小标签开关 |
| `POST /api/v1/distribution/covers/generate` | 派一次封面生成，回传 task_id **与 prompt 原文** |

**复用现有出图引擎，只换入口**：`/canvases/{id}/generations` 要 canvas_id 且按画布
鉴权，封面工作室没有画布；而 `canvas_generation_workflow` 的 `canvas_id` 本来就是
Optional，所以直接以 None 起它，不为了满足一个路由前缀去造隐藏画布。进度轮询复用
`GET /canvases/generations/{task_id}`（读 task_tracking、按用户闸门，与画布无关）。

**prompt 由服务端组装并原样回给前端** —— 设计稿有一块「What was sent to the model」
要显示它。让前端自己拼一份"应该一样"的字符串，是两份必然漂移的真相。

三处刻意：
- 骨架里 `Draft 1: [headline A]…` 那四行**故意不填**（那是模型的活），但同时明确
  要求「你自己想四个方向」——不填占位符不能变成没人负责想。
- **小标签开关打开时，阶段二 Safety 句里夹带的同一条禁令必须一起摘掉**。只改前面
  那句，prompt 里会同时出现"允许"和"no small platform-style labels"，模型收到自相
  矛盾的指令，而用户看到的只是"开关没生效"。这条最容易漏，专门钉了测试。
- 超过 9 张**响亮拒绝**（schema 422），服务端不再 `[:9]` 静默截断 —— 后者会把
  「我明明选了 11 张」变成一声不吭扔掉两张，而且是同一个数字的第三份副本。

### 第六波（浮层外壳 —— 全部拼装完成）

| 文件 | 内容 |
|---|---|
| `.../CoverFrameGrabber.tsx` | 抓帧卡：原生 `<video>` + 时间轴 + 已截帧圆点 |
| `.../CoverDrafts.tsx` | Drafts 卡：**一张图四个象限**、步骤条、「发给模型的原文」 |
| `.../CoverStudioOverlay.tsx` | 浮层外壳：拼装 + 两阶段驱动 + Apply（promote → 回填） |
| `services/coverStudioService.ts` | 两阶段派工 + 等结果（复用 `pollGeneration`） |
| `PublishPage.tsx` | 死占位卡（"Coming in D4"）→ 真按钮 + 渲染浮层 |
| `CoverPicker.tsx` | `CoverPair.horizontal` 放宽为可缺省（AI 封面只产 3:4） |

拼装层的关键约定（各有测试钉住）：
- **抓的是时间戳不是像素**：服务端回源文件按全画质重读那一帧；抓之前先暂停（播放中
  时钟会在点击与读取之间前进，用户抓到的必须是他看到的那一帧）。
- **人物参考来自勾选后的下一次抓取**，抓完自动取消勾选（留着会让之后每次抓取都变成
  一次没被要求的换人）。
- **阶段二只发 人物 + 网格** —— 阶段二的 prompt 只提到这两张图；把帧和模板也重发，
  是把参考位花在 prompt 从没提过的图上。
- **Apply 回传的是 promote 后的 resources id**，不是 generated_media id ——
  `publish_tasks.cover_vertical_resource_id` 引用的是 resources 行，发 gen id 会在
  发布时 404。promote 失败时浮层**不关**（关了看起来和成功一模一样）。
- tsconfig 没开 strict → 判别联合在 else 分支收窄不掉 `ok: true` 成员，必须用
  `=== false` 字面量比较（`CoverStudioOverlay` 里有注释）。

**全部六波完成。** 剩余为真机验收（走查 npm run e2e:prod 范式）与发布。

## 补记 2026-08-26：模板库 = 素材库里的系统文件夹（migration 441，取代 435）

用户问的三件事——「模板库是哪里？我上传创建的『封面』文件夹能不能保护起来？工作室里上传的是不是也默认存到那里？」——答案统一成一个模型：

- **模板库就是素材库里的一个文件夹**。`folders.system_key='cover_templates'`（新列，同 scope 内活着的只能有一个，部分唯一索引 `ux_folders_scope_system_key`）。首次访问时先按 key 找；找不到就**认领**用户已有的顶层「封面 / 封面模板 / Covers / Cover Templates」文件夹（最老的那个）；再没有就新建 `Covers`。只认顶层——某个项目下嵌套的「封面」不是模板库。
- **`cover_templates` 表删掉**。模板 = 文件夹里的图片资源，id 就是 resource id；只留 `cover_template_usage(scope_id, resource_id)` 记「用过 N 次」做排序。
- **系统文件夹受保护**：重命名 / 移动 / 放入回收站 / 永久删除一律 409 `system_folder`（`resources_folders_router._refuse_if_system`）。只改 icon / color 这类外观仍允许。前端右键菜单与批量删除对 `is_system` 文件夹不再提供这些动作，显示「System folder — cannot be renamed, moved or trashed」。
- **工作室里的两条添加路径都落进这个文件夹**：上传 = `uploadResource(file, scope, folder_id)`；从素材库挑 = `link-existing`（只加一层归属，不复制不搬家）。「Save as template」= promote 一次得到 resource → link 进文件夹；同一张图「Use this cover」不再二次 promote（overlay 记住 `promotedId`）。
- **模型侧的绕路只在进池那一刻发生**：`resolveCoverTemplateReference(resource_id)` 走 `/generated-media/import-from-resource` 拿 durable ref，因为出图桥只认 `/api/v1/generated-media/{id}/…`。AVIF/HEIC/TIFF/BMP 在 import 时转成 PNG（上游只收 jpeg/png/gif/webp）。

可证伪的证据（本地）：一次性库对全链迁移 `drift 8/8`；真库集成测试 `tests/integration/test_cover_template_folder_db.py` 5 条（认领 / 幂等 / 只认顶层 / 唯一索引拦第二个 / 回收站不占坑）；后端全量 9597 过；前端 5692 过。

## 补记 2026-08-26（二）：工作室内部按 v4.4 设计稿重排

设计稿 artifact `e7cbad0b…`（Main / Drafts / Final / Landscape 四块画板）落成代码，三栏 260 / 1fr / 220：

- **顶部两个 tab**：「设置竖封面 3:4」「设置横封面 4:3」。横封面 **不走 AI**（这套风格只做 3:4），只做帧裁切或上传，左栏直说这一点。
- **左栏 = 进模型的东西**：提示词框（标题固定一行 + 创作者自由文本，后端新字段 `instructions`，挂在 Safety 句之后并明说不覆盖安全规则）；参考图池（新增「上传」直接进池）；生成按钮 + 阻断原因；**「AI 生成的封面」轮次历史**——每一轮的网格/成品都留着，点缩略图回到那一轮，不再花额度。
- **中栏 = 舞台**：步骤条（选帧·设人物 → 四草案 → 定稿）；idle 时是视频帧 + **居中裁切引导框**（不可拖：`covers/select` 是服务端居中裁，画一个能拖的框等于承诺一个没人理的偏移）+ 「用这个裁切当竖/横封面」（不走 AI，复用 `covers/select`）+ 「上传封面」；出草案后是 2×2 网格；定稿后是成品 + 发给模型的原文。
- **右栏**：竖/横封面预览、模型与风格（模型下拉、风格下拉——目前只有 `viral-video-cover` 一项，文案如实写「目前只有一种」、小标签开关）、当前步骤的动作（完成 / 存为模板 / 换一个草案）。
- `CoverPair` 两个槽位都可选：工作室一次只填当前 tab 的槽，发布页按补丁合并（`setCovers(prev => ({...prev, ...patch}))`）。

有意没做：可拖动裁切框（服务端不支持偏移）；横封面 AI；多风格（skill 只有一个）。

## 补记 2026-08-26（三）：用户要的"有意没做"三项全部做了

- **可拖动裁切框**：`covers/select` 新增 `focus_x/focus_y`（归一化 0..1，框中心），`center_crop_region` 以它为锚点、到边界钳住；缺省 0.5/0.5 = 老的居中，一个字节不变。前端裁切框在视频画面（object-fit contain 的实际区域）内按画幅算尺寸，可拖、可用方向键微调（每步 5%），换源视频时归中。
- **横封面走 AI**：`covers/generate` 新增 `aspect: '3:4'|'4:3'`；prompt 两阶段的画幅词随之变，4:3 用**横版构图句**（大标题占一侧、人物占另一侧，明说"不是竖版转过来"）而不是把竖版语法硬塞进横框；`params.ratio` 跟着走。两个 tab 的轮次历史各自独立；「完成」按该轮的画幅回填对应槽位。
- **多风格 = 封面 skill**：`GET /covers/styles` 列出内置风格 + 用户可见的 `category='cover'` 的 skill（可见性借 AI Library 的口径）。非内置风格走**通用骨架驱动**（`cover_styles.build_from_skill`）：从 SKILL.md 抠 `## Prompt Skeleton` / `## Four-Draft Preview Prompt Skeleton` 的围栏文本，填 `[topic]`/`[headline]`，其余占位行整行丢弃并明说"细节你定"，按画幅换词，小标签开关打开时摘掉骨架里的禁令（空白宽容匹配）。缺骨架的 skill 生成时 422 点名，不静默退回内置。`requires_person` 由正文是否点名 character reference 决定，前端据此决定人物槽和阻断。

地面真值：生产库里用户导入的 `viral-video-cover` 行 `category='cover'`，正文两段骨架都在 ```text 围栏里 —— 通用驱动的抽取规则就是照它定的。

## 补记 2026-08-27：三处用户反馈的收口

- **模型下拉跟随 Settings 平台模型卡**（#2027）：`/canvases/generation-models` 服务端套用户的总开关 + 逐模型黑名单（`platform_model_visibility`）。故意不套管理员治理总开关——它默认关、库读不到也判关，接进下拉会让抖动变成"下拉空了"；成本闸门在派发层。
- **参考图与模板库并成一张卡；工作室里直接选视频**（#2029）：上半「发给模型的图 N/9」、下半「模板库 · 点一张加入」；舞台没视频时列出素材库视频，选中回填到发布页已选视频。
- **发布页封面区只剩两个磁贴**（本次）：竖 3:4 / 横 4:3 都是按钮，各自打开工作室并落到对应 tab（`initialOrientation`）；「从视频帧生成封面」卡与「AI 封面 · Canvas」卡删除（它们只是"打开工作室"的另两种说法）；`CoverPicker` 及其抽帧候选流程整体移除（后端 `covers/extract` 端点保留）；已填的磁贴可单独清除；状态文案分竖/横。

## 补记 2026-08-27（二）：舞台与文案的第二轮收口

- **修一个真 bug**：`POST /generated-media/import-from-resource` 在 `request_scope` 外读 `resources`（`UnscopedQueryError`），所以工作室里每一张模板都"没能准备成参考图"。端点已包进调用者自己的 scope；回归测试钉住"仓库调用时 scope 必须已开"。
- **人物参考独立成卡**：不再是九格里的一格；从舞台「设为人物」按钮设置；参考图池的额度显示为 9 减去人物。
- **舞台像抖音那样**：视频下方是抽帧缩略图条（复用 `covers/extract` + Realtime，`useCoverFrameCandidates`），条本身就是进度轨，带当前帧游标；四个动作一行（抓这一帧 / 设为人物 / 用这个裁切当封面 / 上传）。
- **描述性文字全部收进 (?)**（`HelpTip`，原生 title）：参考图/模板库/文件夹说明、抓帧说明、模型来源、风格描述、小标签为什么、预览提示。
- **没选视频不进工作室**：发布页两个封面磁贴置灰，点击 toast「先在上方选一个视频」（工作室内选视频的路径保留为兜底）。
