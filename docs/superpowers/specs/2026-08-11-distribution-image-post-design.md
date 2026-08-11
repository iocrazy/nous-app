# 图集发布（Image Post）设计

> 基础设计见 [`2026-08-04-distribution-session-channel-design.md`](./2026-08-04-distribution-session-channel-design.md)（下称「基础设计」），
> 待办来源见 [`2026-08-09-distribution-gap-closure-plan.md`](./2026-08-09-distribution-gap-closure-plan.md) 的 **P2-1 步骤 2** 与 **P2-2**。
>
> **写作口径**：每条断言标注来源。
> `代码 file:line` = 读过的代码；`[实测 日期]` = 在真实页面/生产库验过；
> **`[TO-VERIFY]`** = 还没验，实现期必须实测填入。
> **本文不接受把猜测写成事实**——写作当时抖音图文发布页的 DOM 本项目从未打开过，
> 凡与它有关的数字、文案、语义一律 `[TO-VERIFY]`。
>
> **2026-08-11 更新**：T0 勘探已完成，那一页现在打开过了。V1–V17 的逐条结论、
> 每条的精确匹配数、以及**哪四条没能测出来和为什么**，都在 **§3.4**。
> 本轮的口径没有放松：测不出来的条目标注为「未能测出 + 原因」，一条都没有用
> 推测填上 —— 这张表是 T3/T4 的输入，填错了下游全歪。

---

## 0. 范围与已拍板边界

用户已拍板，本文不重新讨论：

| 项 | 决定 |
|---|---|
| 平台顺序 | **抖音一期、小红书二期**。规格两期都写，实现先做抖音端到端 |
| 功能对齐 | 对齐视频通道：多图上传+排序、标题/正文、封面语义、自主声明、定时发布、可见性 |
| 配乐 | **用平台默认，不做自选**（降低首期风控面） |
| 能力声明 | 改为**后端下发**，撤掉前端表 `frontend/components/Distribution/capabilities.ts` |
| 小红书一期附带 | 二期包含「短信绑定链首次验证」——库里从没有过小红书账号，这条链一次没跑通（gap-closure 计划 L449-457） |

**范围外**：配乐自选、B 站图文、official/h5 通道的图集（该通道从未验证过，见 P2-3）。

图文合集**不再是待定项**：[实测 2026-08-11]（§3.4 V14）合集控件在图文页存在
（`添加合集` / `不选择合集` 各 exact=1），按原定口径复用现有 degrade 逻辑。

---

## 1. 现状取证：三条链各自停在哪

### 1.1 能力声明链（三层，当前一致地「拒绝」）

真相自下而上，每层只能声明下层已实现的（`capabilities.ts:11-22` 原文）：

| 层 | 文件:行 | 当前值 |
|---|---|---|
| 浏览器（唯一真正发东西的层） | `browser/app/publish.py:56` | `SUPPORTED_CONTENT_TYPES = ("video",)` |
| 后端 profile | `backend/app/services/distribution/session_adapter.py:268` | douyin `content_types=frozenset({"video"})` |
| 前端表 | `frontend/components/Distribution/capabilities.ts:24` | `IMAGE_POST_PLATFORMS = new Set<string>()`（空集） |

三层现在**一致**（P2-1 步骤 1 已止血）。代价是前端 Images tab 恒置灰
（`PublishPage.tsx:303-308` 的 `imagesSupported`，`:753-767` 的 disabled + 提示）。

**这个止血方案有两个结构性缺陷，本次要连根拔掉**：

1. **前端表是第三处独立声明**。它靠人工与另两处保持同步，注释里写死了「必须与后端同一个 PR 落地」（`capabilities.ts:20-22`、`session_adapter.py:265-267`）——**靠纪律，不靠机制**。P2-1 那次翻车的病根正是「三处声明各自为政」，止血时又留下了同族的第三处。
2. **浏览器侧的声明是全局的，能力却是按平台的**。`SUPPORTED_CONTENT_TYPES` 是模块级常量（`publish.py:56`），而 publisher 是按平台注册的（`douyin_publish.py:1542`）。一旦抖音支持图集就要把它改成 `("video","images")`，于是**任何注册了 publisher 的平台**都会在 `validate_intent`（`publish.py:175-180`）处放行图集，哪怕它一行图集代码都没写。这是下一次「宣称不存在的能力」的完美温床。

### 1.2 素材链（图集其实已经能走到浏览器门口）

| 环节 | 文件:行 | 事实 |
|---|---|---|
| 前端提交 | `PublishPage.tsx:668-669`、`frontend/types.ts:2522-2523` | 只发 `content_type` + `resource_ids: string[]`，**没有 media 数组**；顺序 = 点选顺序 |
| 强制 broadcast | `PublishPage.tsx:331` + `:676-678` | 图集恒 `distribution_mode='broadcast'`（一篇笔记发给每个账号，不轮转）。注意这是**分发模式**，不是可见性——`visibility` 与图集无任何联动（`:194`） |
| 入口 schema | `backend/app/schemas/distribution_publish.py:114-135` | images 只有两条：`resource_ids` 非空、`≤ MAX_IMAGES`（`:46` = 35） |
| 表结构 | `backend/app/models/distribution.py:182-184` | `publish_tasks.resource_ids` JSONB 数组，**顺序即数组顺序** |
| URL 解析 | `backend/app/repositories/publish_tasks_repository.py:504-545` | 对象存储 → Supabase Storage 签名 URL（TTL 3600s）；文件系统 → HMAC 签名 `/media/` URL。official/h5/session 三通道共用单一入口（基础设计 §8.5 已实测容器内可达） |
| intent 组装 | `backend/app/workflows/publish_distribution.py:335-341` | images 分支已写好：`PublishMedia(kind="image", url=…, filename=…)` 逐张；`_resolve_image_urls`（`:163-182`）any-fail-all-fail，docstring 明写「`resource_ids` 的顺序就是图集顺序，永不重排」 |
| 递给浏览器 | `backend/app/services/distribution/browser_client.py:837-842` | `{platform, storage_state, environment, intent}`，intent 含 `media` 数组 |

**结论：backend → browser 的图集素材通道已经是通的**，缺的只有 ①profile 放行 ②浏览器侧接住。这大幅缩小了本次工作量——不需要新表、不需要新的签名逻辑。

### 1.3 浏览器侧（真正的缺口）

| 位置 | 文件:行 | 为什么挡住图集 |
|---|---|---|
| 内容类型门 | `publish.py:175-180` | `content_type not in ("video",)` → `unsupported_content_type` |
| 中立层校验 | `publish.py:189-198` | 硬性要求「恰好一个 kind=video 的 media」，图集必然撞 `missing_video` |
| 待下载清单 | `publish.py:235-241` | `assets_to_stage` 硬编码 `[(VIDEO_ROLE, videos[0])]` + 可选 cover |
| 暂存产物 | `assets.py:221-235` | `stage_assets` 返回 `Mapping[str, StagedAsset]`，**role 是 dict 的 key** —— N 张图需要 N 个不同的 key |
| 上传 | `douyin_publish.py:689-699` | `job.assets[VIDEO_ROLE]` 单文件 `set_input_files` |
| 封面 | `douyin_publish.py:841-944` | 抖音视频封面是**独立上传的第五个文件**（弹窗里第 4 个隐藏 input 中的 `.nth(1)`）；图集封面语义完全不同 |
| 页面地理 | `douyin_publish.py:96-108` | `UPLOAD_URL`（`douyin.py:26`）、`EDITOR_PATHS` 两套灰度路径、`MANAGE_PATH_FRAGMENT` 全是视频发布页的 |

### 1.4 回读链（P1-3）对图文的隐式假设

`browser/app/platforms/douyin_verify.py` 的匹配锚点是**标题文案**，这一层与内容类型无关，是好的。但三处写死了 video 形状：

| 常量 | 文件:行 | 值 |
|---|---|---|
| item id 正则 | `douyin_verify.py:92` | `/video/(\d{6,32})` |
| 公开 URL 模板 | `douyin_verify.py:88` | `https://www.douyin.com/video/{item_id}` |
| 卡内链接选择器 | `douyin_verify.py:422` | `a[href*="/video/"]` |

以及状态文案表（`:174-196`：`未通过`/`审核中`/`定时中`/`已发布` 等），
**是在视频发布语境下整理的**。[实测 2026-08-11]（§3.4 V17）：图文卡与视频卡
在同一张作品表里混排、**状态用同一批词**（读到的真实卡状态只有 `已发布`），
所以这张表零改动。图文卡的区别在别处：前缀是 `N张` 而非时长，指标是
`划走率 / 文案展开率 / 平均浏览图片数`。

好消息：失败方向是安全的。卡片选择器是 class 前缀匹配（`:123-128`），
读的是整卡 `inner_text()`（`:465`），所以即便图文卡的链接是 `/note/`，
**标题匹配仍然成立，只是 `published_url` 回填不到**。而 `published_url`
本来就是「顺带」——`publish_readback.py:32-46` 记录 [实测 2026-08-08]：
作品卡既没有 href 也没有 id 属性，列表 XHR 也不返回，三处都查过。

---

## 2. 设计决策

### D1 能力声明改为「单一真相 + 机制守卫」，而不是「三处同步 + 纪律」

**决定**：

1. 浏览器侧新增**无依赖**模块 `browser/app/capabilities.py`，**按平台**声明内容类型：
   ```python
   # 只用标准库。backend 的守卫测试会按文件路径直接加载它，
   # 加任何 playwright / pydantic import 都会让那个守卫失效。
   PLATFORM_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
       "douyin": ("video",),          # T3 完成时改为 ("video", "images")
   }
   ```
   `publish.py` 的 `SUPPORTED_CONTENT_TYPES`（`:56`）退役，`validate_intent`
   改为按 `job.platform` 查表。**这修的是 §1.1 缺陷 2**：一个只注册了视频
   publisher 的平台，不会因为别的平台支持图集而被放行。

2. 后端 `SESSION_PLATFORM_PROFILES`（`session_adapter.py:252-306`）**保持为后端唯一真相**，新增下发端点：
   ```
   GET /api/v1/distribution/capabilities
   → { "platforms": { "douyin": PlatformCapability, ... } }
   ```
   `PlatformCapability` 直接由 `PlatformSessionProfile`（`:227-249`）投影，
   **不允许手写第二份**。字段：`supports_publishing` / `content_types` /
   `video_extensions` / `image_extensions` / `min_images` / `max_images` /
   `max_title_len` / `max_topics` / `supports_scheduling`（= `schedule_min_lead is not None`）/
   `schedule_min_lead_seconds` / `schedule_max_ahead_seconds` /
   `self_declarations` / `supports_collection`。

   `frozenset` 投影成 JSON array 时**必须排序**（`sorted(...)`），否则响应体
   每次进程重启都变，缓存与快照测试全部不稳定。

3. **前端 `capabilities.ts` 整体删除**，`supportsImagePosts` 的调用点
   （`PublishPage.tsx:303-308`）改为读后端下发值。

4. **CI 守卫**（这一条是 D1 的重点，没有它 D1 只是换了个地方放同样的问题）：
   新增 `backend/tests/test_capability_matches_browser.py`，用
   `importlib.util.spec_from_file_location` 按路径加载 `browser/app/capabilities.py`
   （因为它无依赖，backend venv 里也能加载），断言：

   ```
   对每个 supports_publishing=True 的 profile：
       profile.content_types ⊆ PLATFORM_CONTENT_TYPES[platform]
   ```

   即**后端永远不得声明浏览器没实现的内容类型**。反向不禁止（浏览器先实现、
   后端后放行，是安全方向）。这把「必须同一个 PR」从注释变成红 CI。

   ⚠️ 该测试跑在 backend venv（Python 3.13）里加载 browser 的文件（写给 3.12），
   所以 `capabilities.py` **不得用任何 3.13-only 语法**。测试文件里写明这条。

**不选的方案**：
- *后端启动时调 browser `/capabilities` 拉取* —— 把一个静态事实变成运行时网络依赖，漂移在生产才暴露，而 CI 守卫在 PR 就拦住。
- *共享 JSON manifest* —— 两侧都要写解析代码，且 JSON 没有类型；而 `capabilities.py` 本身就是 Python 常量，browser 侧零解析成本。

### D2 多图的 asset 角色：`image:{index}`，顺序显式而非依赖 dict 插入序

`PublishJob.assets` 是 `Mapping[str, StagedAsset]`（`publish.py:107`），role 是 key。
CPython 的 dict 保序，`stage_assets` 也按 `items` 顺序插入（`assets.py:232-234`），
所以「靠插入序」能跑通——**但图集顺序是用户看得见的产品语义，不该建立在一个
语言实现细节上**，而且任何一次 `dict(...)` 重建、过滤、合并都会悄悄破坏它。

**决定**：

- `publish.py` 新增 `IMAGE_ROLE_PREFIX = "image"`，role 命名 `f"image:{i}"`（`i` 从 0 起，十进制无补零）
- 新增**纯函数** `ordered_image_assets(assets) -> tuple[StagedAsset, ...]`：
  提取所有 `image:` 前缀 role，按数字后缀排序，**索引有缺口或重复即 raise**
  （缺口意味着上游组装出错，静默按剩下的发是发出一个用户没编排过的图集）
- `assets_to_stage`（`publish.py:235-241`）按 `content_type` 分支：
  - `video` → `[(VIDEO_ROLE, video)] (+ cover)`
  - `images` → `[(f"image:{i}", item) for i, item in enumerate(images)]`
- `COVER_ROLE` 在 images 分支**不产生**（见 D4）

排序逻辑是纯函数 → 单测覆盖「乱序 key、缺口、重复、空、单张」五种，不需要浏览器。
这符合本仓既有风格（`douyin_publish.py:272-620` 整段都是可测的纯判定）。

### D3 校验前移：图集的所有可拒绝项都在 backend 入口一次查表拒掉

对齐 P2-1a 的「拒绝发生在最前面」与基础设计 §7.7。三道门，各管各的：

| 门 | 位置 | 管什么 | 用户看到 |
|---|---|---|---|
| ①Pydantic | `schemas/distribution_publish.py:114-135` | 与平台无关的形状：`resource_ids` 非空、数量上下界、`content_type` 合法 | 提交即 422，表单上红字 |
| ②profile 门 | `session_adapter.py:551-588` `validate_publish_intent` | 平台相关：`content_types` / 扩展名 / `min_images` / `max_images` / 标题长度 | 提交即拒（见下） |
| ③browser 门 | `publish.py:validate_intent` | 兜底 + 平台自有规则（定时窗口、自主声明词表） | 任务行 failed + 类型化 reason |

**关键改动**：②当前只在 workflow 里跑（`publish_distribution.py:542-544`），
用户提交后要等到异步任务才看到失败。**要把它前移到
`distribution_router.py::create_task`（`:715-785`）**，在 `_authorize_account`
之后、`create_task` 之前，对每个目标账号的平台跑一次 `validate_publish_intent`
的**纯形状部分**（不需要 session_state），任一不过 → 422 + 类型化 reason。

这直接解决 §1.2 里那个隐患：`decide_channel`（`publish_distribution.py:60-76`）
会把非 session 账号降级到 h5，于是同一个图集批次可能一半 `pending_share`、
一半 `failed`——前移之后，混选就在提交时被拒，不会产生半成功批次。

**②的数值必须来自 profile，不是常量**：`MAX_IMAGES = 35`
（`distribution_publish.py:46`）是**猜的**——它自己的注释写着「H5 分享文档没
明说上限，这里沿用 app 已知的图集上限」。它保留为①的中立硬顶（防御性），
真正的平台上限由 `profile.max_images` 承担。

[实测 2026-08-11]（§3.4 V2）：**35 是对的** —— 上传页原文
`最多支持上传35张图片，图片格式不支持gif格式`（exact=1）。那个"猜的"数字碰巧
猜准了，但它此前的状态是「无人验证的常量」，现在才是事实。
`min_images` = **1**（只传 1 张，编辑器完整渲染，无「至少 N 张」提示）。
另测得单张 **50MB** 上限（`图片文件大小不超过50MB`，exact=1）—— 这是
`asset_max_bytes`(2GB) 兜不住的一档，值得单列。

### D4 封面语义：图集不接受独立封面，直到实测证明它有

抖音视频的封面是弹窗里独立上传的第五个文件（`douyin_publish.py:873-890`，
含那条 [实测] 血泪：4 个隐藏 input，`[0][1]` 是 AI 参考图，用 `.first`
会「传成功但没封面」）。

前端已经把图集封面定义成「首图即封面」（`PublishPage.tsx:861-868` 的
`coverImagesMode` 文案、`:1332-1334` 的 `coverImagesNote`，i18n
`en.json:464-465`），且 images 模式下 `CoverPicker` 收到 `sources={[]}`
只渲染一句提示（`CoverPicker.tsx:331-337`）。

**决定**：
- backend 门②新增：`content_type == "images"` 且 `intent.cover is not None`
  → 拒绝，reason `cover_not_supported_for_images`
- browser 侧 images 分支不产生 `COVER_ROLE`，`_set_cover`（`douyin_publish.py:841`）
  在图集流程里**不调用**
[实测 2026-08-11]（§3.4 V8）：图文页**有**封面控件，但它和视频那个不是一回事 ——
文案是 `封面设置` / `选择一张图片作为封面` / `编辑封面`（各 exact=1），而视频版的
`设置封面` 在图文页 **exact=0**。也就是说封面是**从已上传的图片里挑一张**，
不是视频那种"弹窗里独立上传的第五个文件"。

**这让 D4 的结论更强而不是被推翻**：图集确实不接受独立 cover 素材，
`cover_not_supported_for_images` 照旧拒绝，浏览器侧照旧不产生 `COVER_ROLE`。

**仍未测出**：默认封面是不是首图、能不能选非首图 —— 要点开封面弹窗，
而勘探端点按设计没有点击能力（§3.4 末尾有否定性验证：弹窗内容压根不在 DOM 里）。
所以本期继续按「首图即封面」实现（前端 i18n 已落地）；若 T3 实现期发现默认不是首图，
那是一个独立的后续任务。

### D5 回读：链接模式参数化，状态词表实测后再动

**决定**（最小改动，因为失败方向已经是安全的，见 §1.4）：

- ~~`douyin_verify.py:88/92/422` 三处从常量改为候选序列 `WORK_LINK_PATTERNS`~~
  —— **[实测 2026-08-11] 撤销这一条**（§3.4 V17b）。作品管理页上
  **`<a>` 元素总数 = 0**（`a` 0、`a[href*="/video/"]` 0、`a[href*="/note/"]` 0、
  `a[target="_blank"]` 0）。问题不是"图文用了 `/note/` 而我们只认 `/video/`"，
  是**这一页根本没有锚点**，`:422` 那个选择器对视频卡同样是死的。
  把死选择器参数化成一串死选择器，只会让人以为回填 URL 有希望。
  T5 应当做的是：要么删掉 `:422` 那条读取并在注释里写明理由（与
  `publish_readback.py:32-46` 的 [实测 2026-08-08] 同一结论），要么留着但别
  为它加候选项。`published_url` 依旧是 null，这是平台的事实，不是我们的缺口。
- 状态文案表（`:174-196`）**不动** —— [实测 2026-08-11] 图文卡与视频卡同词
  （§3.4 V17）。⚠️ 只覆盖到 `已发布` 这一种真实卡状态：账号里当前没有
  审核中 / 定时中 / 未通过 的作品，那三个词的匹配数来自顶部筛选 tab 而非卡片。
  所以"同词"这个结论对其余状态仍是**推断**，T7 发一条定时图文时可以顺带证实
- `judge_readback`（`:308-412`）的判定顺序不动——它已经把「读不到卡」
  和「卡在但没匹配」区分开了（`:350-356` `list_unreadable` → INCONCLUSIVE），
  这正是图文场景最需要的保守性

### D6 前端：置灰条件改为「后端说了算」，其余复用

- `imagesSupported`（`PublishPage.tsx:303-308`）改为
  `targets.every(a => caps[a.platform]?.content_types.includes('images'))`，
  能力后端下发后**自动解除置灰**，前端不需要跟着发版
- ⚠️ 现存问题一并修：`:307` 的 `targets.length > 0` 让「一个账号都没连」
  与「平台不支持」共用同一条文案（`distribution.publish.imagesUnsupported`）。
  拆成两条 i18n key，`noAccountsForImages` 是新的
- 排序 UI：当前**没有**拖拽/上下移，顺序 = 点选顺序，只有角标序号
  （`:821-827`）。本期**加上下移按钮**（不做拖拽——拖拽在移动端与
  可访问性上都要额外工作，而序号角标已经把顺序说清楚了）
- gallery 展开（`:456-505`）、内联上传（`:507-565`，并发 3）、
  强制 broadcast（`:331`/`:676-678`）**全部复用，不改**
- 顺手修 picker 里写死 "videos" 的四处文案：`:1391` / `:1411` / `:1541` / `:1031`

### D7 小红书二期：先把绑定链跑通，再谈 publisher

`session_adapter.py:290-291` 的注释原文：

> 下面这些 content_types / 扩展名是**占位**，等真正实现发布时要对着平台实测填准；在 supports_publishing=False 的前提下它们不会被用到。

所以 `xiaohongshu` 的 `content_types={"video","images"}`（`:294`）、
`image_extensions={.jpg,.jpeg,.png,.webp}`（`:296`）**当前全是占位值，不是事实**。
D1 的下发端点会把它们暴露给前端——所以 **`supports_publishing=False` 的平台，
下发时必须把 `content_types` 等能力字段置空并带上 `is_placeholder: true`**，
否则「后端下发」会把占位值升格成看起来权威的 API 响应，这正是我们要消灭的病。

小红书侧的确定事实：

| 事实 | 出处 |
|---|---|
| 创作平台**没有扫码登录**，只有短信/密码 | `xiaohongshu.py:129-143` [实测 2026-08-08]：大图 0 个、canvas 0 个、含「扫码/二维码/QR」可点元素 0 个 |
| 登录页文案精确匹配 | `:146-149` [实测 2026-08-08]：`短信登录` / `发送验证码` / **`登 录`（中间有空格，`登录` 精确匹配是 0）** |
| 验证码输入框 | `:155-160` [实测 2026-08-08]：页面 5 个 input，`placeholder="验证码"` 是目标 |
| 身份 cookie | `:194-211` **`[GUESS] UNVERIFIED`** —— `IDENTITY_COOKIE = "userId"`，从没有账号完成过绑定，没人读过真实 cookie jar |
| profile 选择器 | `:166-182` **`[GUESS] UNVERIFIED`** |
| 短信链后端 | 已存在：`browser_client.py:1083-1115` `submit_login_sms` / `login_sessions.py:221-271` `submit_sms` / `login.py:243-263` `submit_sms_code` |

**二期第一件事不是写 publisher，是用一个真账号跑通绑定并校准
`IDENTITY_COOKIE`**——这是 P0-1「身份键降级 = 多一个账号」那条教训的直接应用：
`userId` 若不存在，绑定应类型化失败（`identity_unresolved`），而不是造新行。
校准口径 `xiaohongshu.py:29-34` 已写死：绑一次、dump cookie 名、
**确认值跨两次登录稳定**。

### D8 风控：本期只加一条真实自动化路径，节奏克制写进代码而非文档

- 图集上传**一次 `set_input_files` 传 N 个文件即可** —— [实测 2026-08-11]
  （§3.4 V5）：上传页 `input[type="file"][multiple]` 匹配 1 个，且一次交 3 个
  文件后编辑器显示「已添加3张图片」。取一次传完：更少的交互 = 更少的行为特征。
  （逐张传的退路不需要了，也就不需要新旋钮）
- 账号级串行锁（基础设计 §7.5）已存在且与内容类型无关，图集自动继承
- 单账号期**不阻塞**：现有唯一抖音账号，共用出口 IP 不构成关联信号
- **多账号之前必须先做 P2-4（每账号环境隔离）**——`account_environments`
  表建了但 0 行、无读取方接进 `build_context_kwargs`。本文只声明这条边界，
  不展开设计（P2-4 有自己的条目）
- 勘探阶段（T0）的额外约束见 §3

---

## 3. T0：抖音图文页只读勘探计划

### 3.1 为什么不在本次会话直接勘探

评估结论：**不在本次做，作为实现期第一个任务**。理由是具体的，不是保守：

1. **没有现成的勘探工具**。`browser/app/main.py` 只有 9 个端点
   （`:123` healthz、`:145` validate、`:209` publish、`:290` verify-publish、
   `:380`/`:435`/`:472`/`:494`/`:532` login 系列），**没有任何「打开这个 URL
   并 dump DOM」的能力**。做勘探必须先写代码——那本身就是实现工作，属于 PR，
   不属于写规格的会话。
2. **需要明文 `session_state`**。基础设计 §7.6 规定它只允许存在于 nous-browser
   进程内存，禁止落盘/进日志。为了勘探临时写个脚本把它从库里解出来，正是
   那条纪律要防的事。
3. **图文页大概率与视频页同构**：视频发布页要先传一个 1 秒测试视频才渲染表单
   （基础设计 §7.4.0 末段）。图文页很可能同理要先传图——**那是对真实账号的
   写操作**，必须在账号级串行锁内进行，而临时脚本拿不到那把锁，与真实发布
   撞车的后果是把账号踢下线（§7.5 的原话）。

所以 T0 的产出是**一个可复用的只读勘探端点**，它同时也是小红书二期校准的工具
（`xiaohongshu.py:29-34` 那套「绑一次、数匹配数」的流程现在只能靠人肉）。

### 3.2 勘探端点设计

```
POST /session/inspect   （internal token，与其他端点同 dependency）
body: { platform, storage_state, environment,
        url,                        # 只允许该平台 CREATOR_HOSTS 内的地址
        seed_files: [MediaItem],    # 可选，为渲染表单而上传的探针文件
        text_probes: [str],         # 要数精确匹配数的中文文案
        selector_probes: [str] }
→ { url_after, texts: {probe: count}, selectors: {sel: count},
    body_text_excerpt, input_summary: [...] }
```

**硬约束（写进代码，不是写进 runbook）**：

- **端点内不存在任何点击提交/发布的路径**。它只 `goto` / `set_input_files` /
  数匹配数 / 读文本。「发布」这个动作在这个模块里没有实现，所以不可能被误触发
- `url` 白名单校验：必须落在该平台的 `CREATOR_HOSTS` 内（`douyin.py:30` /
  `xiaohongshu.py:54`），拒绝任意 URL——否则这是一个拿着用户 cookie 的 SSRF
- 走与发布相同的账号级串行锁
- 响应经 `redaction.scrub`（`browser/app/redaction.py`）；不返回完整 HTML，
  只返回受控摘要（文案计数 / 选择器计数 / 截断的可见文本）
- 探针图片**用英文命名、自动生成**（对齐 CLAUDE.md 测试数据规范）：
  ```bash
  ffmpeg -f lavfi -i color=c=gray:s=1080x1440 -frames:v 1 probe-image-1.jpg
  ```
  尺寸 1080×1440（3:4）是图文常见比例；生成 3 张，命名 `probe-image-{1,2,3}.jpg`
- 勘探结束显式调 close，凭证不落盘

### 3.3 必须测出来的清单（这些是 `[TO-VERIFY]` 的全部来源）

> 本节保留为**当初打算怎么测**的记录（方法论仍然有效，小红书二期照抄）。
> **结论看 §3.4**。

| # | 问题 | 怎么测 | 落到哪 |
|---|---|---|---|
| V1 | 图文发布页的 URL 是什么 | 从 `creator.douyin.com` 创作首页找入口，记录跳转后的 `path`；确认是否也有灰度双路径（视频有两套，`douyin_publish.py:102-105`） | `douyin_publish` 新增 `IMAGE_UPLOAD_URL` / `IMAGE_EDITOR_PATHS` |
| V2 | 图片数量上下限 | 页面文案（通常写「最多 N 张」）+ 尝试只传 1 张看是否放行 | `profile.max_images` / **新字段 `min_images`** |
| V3 | 接受的图片格式 | 页面文案 / file input 的 `accept` 属性 | `profile.image_extensions`（当前 `.jpg/.jpeg/.png`，`session_adapter.py:273`，未验） |
| V4 | 单张大小上限 | 页面文案 | `profile` 新字段或沿用 `asset_max_bytes` |
| V5 | 一次能否多选上传 | file input 有无 `multiple` 属性 | D8 的上传节奏 |
| V6 | 上传完成/失败的可见标记 | 对照视频的 `重新上传` / `上传失败`（`douyin_publish.py:115-116`） | `judge_upload_state` 的图集版 |
| V7 | 排序怎么表达 | 是否有拖拽区、有无序号、上传顺序是否即展示顺序 | 若上传顺序即展示顺序 → 零额外工作；否则要写排序步骤 |
| V8 | 封面语义 | 有无「设置封面」入口；首图是否默认封面；能否选非首图 | D4（当前按「首图即封面」实现） |
| V9 | 标题长度上限 | 输入框 placeholder / maxlength；视频是 30（`douyin_publish.py:203`） | 图集版 `TITLE_LIMIT` |
| V10 | 自主声明控件是否存在、是否同六项 | 数 `请选择自主声明` 的精确匹配数 + 弹窗内六项（`douyin_publish.py:225-232`） | 复用或分叉 |
| V11 | 定时发布控件是否存在、窗口是否同 2h–14d | 数 `定时发布` / `立即发布` 精确匹配数；读窗口文案 | `profile.schedule_*` 是否需分内容类型 |
| V12 | 可见性控件（谁可以看）是否同三项 | 数 `公开`/`好友可见`/`仅自己可见` | `VISIBILITY_LABELS`（`douyin_publish.py:169-173`）复用与否 |
| V13 | 保存权限（允许/不允许）是否存在 | 数 `允许`/`不允许`，**必须 `exact=True`**——「允许」是「不允许」的子串（`douyin_publish.py:187-189`） | `DOWNLOAD_LABELS` 复用与否 |
| V14 | 合集控件是否存在 | 数 `添加合集` | 有则复用 degrade 逻辑（`douyin_publish.py:1221`），无则本期不做 |
| V15 | 配乐控件的默认态 | 只看，不动（用户已拍板用平台默认） | 确认「不碰它」不会导致发布被拦 |
| V16 | 发布成功后跳到哪 | 对照视频的 `/content/manage`（`douyin_publish.py:108`） | `judge_publish_outcome` 复用与否 |
| V17 | 作品管理页里图文卡长什么样 | 用一条**已存在的**图文作品（若无，本条推迟到 T7 端到端之后）看整卡 `inner_text` 与 href | D5 的状态词表与链接模式 |

**每条都要记录「精确匹配数」**，不是「找到了」。基础设计 §7.4.0 的口径：
不等于 1 就是错的。

### 3.4 实测结果 [实测 2026-08-11]

勘探方式：`POST /session/inspect`（PR #1791）经 `session_inspect.inspect_account_page`
调起，账号 **HEYGO**（`337271352171182`，用户拍板的小号），全程持账号级串行锁，
共 7 次页面读取。探针图为 ffmpeg 生成的 1080×1440 纯色 jpg（`probe-image-{1,2,3}.jpg`），
经对象存储签发内网 URL 供浏览器容器拉取，勘探结束已从存储删除。**全程未点发布**。

下表「计数」列是 `exact` 精确匹配数（括号内为 `substring`，仅在两者不同时标注）。

| # | 结论 | 计数 / 证据 | 落到哪 |
|---|---|---|---|
| **V1** | 上传页 `https://creator.douyin.com/creator-micro/content/upload?default-tab=3`（`url_after` 未跳转）；传图后 SPA 跳到编辑页 `https://creator.douyin.com/creator-micro/content/post/image?default-tab=3&enter_from=publish_page&media_type=image&type=new` | 三次带图勘探（1/2/3 张）落点完全一致 | `IMAGE_UPLOAD_URL` = 上传页；`IMAGE_EDITOR_PATHS` 匹配 `/content/post/image` |
| **V1b** | **未观察到灰度双路径** —— 视频有 `/content/publish` 与 `/content/post/video` 两套，图文三次都只落 `/content/post/image` | 3/3 | ⚠️ 三次同一账号同一天，**不足以证明不存在第二条灰度路径**。T3 仍应按候选列表轮询，与视频同构 |
| **V2** | 上限 **35**：`最多支持上传35张图片，图片格式不支持gif格式` 1。下限 **1**：只传 1 张，编辑器完整渲染、无「至少 N 张」提示（`至少` 0） | 1 / 1 | `max_images=35`（**证实了 `distribution_publish.py:46` 那个自承是猜的数**）；`min_images=1` |
| **V3** | file input `accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif"`；页面文案 `推荐jpg、jpeg、png、webp格式，不支持gif格式` 1 | 1 | 当前 profile 的 `.jpg/.jpeg/.png` 是**真子集**，安全但偏窄。可扩到 `.webp/.bmp`；`.tif` 只在 accept 里、文案未提，不建议 |
| **V4** | `图片文件大小不超过50MB` 1；另有比例建议 `不建议宽高比例超过 1:2，推荐图片宽高比例：3:4、4:3` 1 | 1 / 1 | 50MB/张。远小于 `asset_max_bytes`(2GB)，值得单列 |
| **V5** | **能一次多选**：`input[type="file"][multiple]` 1，且一次 `set_input_files` 传 3 个文件后编辑器显示「已添加3张图片」 | 1 | D8 取「一次传 N 个」，无需逐张 |
| **V6** | 完成标记是 `已添加{n}张图片`（1/2/3 张各验过一次，均 substring=1、exact=0——节点文本含其它内容）与 `清空并重新上传` 1、`继续添加` 1。`上传失败` 0 | 见左 | ⚠️ **`重新上传` exact=0 / substring=1** —— 它是 `清空并重新上传` 的子串。图文版判定必须 `exact=True`，照搬视频的子串匹配会误判 |
| **V7** | **未能测出**。编辑器有 `编辑图片` 1，但顺序是图形化的缩略图列表，勘探端点只回计数、读不出顺序 | — | T3 需在实现时验证（发 3 张不同色探针图，看平台上的先后） |
| **V8** | 控件**存在但语义与视频不同**：`封面设置` 1、`选择一张图片作为封面` 1、`编辑封面` 1；视频版文案 `设置封面` **0** | 1/1/1，`设置封面` 0 | 封面是**从已上传图片里选**，不是视频那种独立上传的第五个文件。D4「不接受独立 cover 素材」的结论**成立且更强**。⚠️ 「默认是不是首图」「能否选非首图」未能测出——要开弹窗，见下方说明 |
| **V9** | 编辑页有两个计数器：`0/20` 1 与 `0 / 1000` 1；标题 input `placeholder="添加作品标题"`（`maxlength` 属性为 null），描述是 contenteditable `data-placeholder="添加作品描述..."` | 1 / 1 | ⚠️ **计数器与字段的绑定关系没有直接读到**，是按排除法定的（1000 只可能属于描述）→ 标题上限 **20**（视频是 30）。T3 落地前建议用一次真实填写复核 |
| **V10** | 入口存在且同文案：`请选择自主声明` 1。**六个选项未能测出** | 1 | 见下方「未能测出的四条」 |
| **V11** | 两个选项都在且同文案：`立即发布` 1、`定时发布` 1。**2h–14d 窗口未能测出** | 1 / 1 | 见下方 |
| **V12** | 三项完全相同：`公开` 1、`好友可见` 1、`仅自己可见` 1 | 1/1/1 | `VISIBILITY_LABELS`（`douyin_publish.py:169-173`）**可直接复用** |
| **V13** | 存在且同文案：`允许` exact 1 / **substring 2**、`不允许` exact 1 | 见左 | `DOWNLOAD_LABELS` 可复用。**「允许」是「不允许」子串这件事在图文页实测复现**（substring 比 exact 多 1），`exact=True` 不可省 |
| **V14** | 合集控件**存在**：`添加合集` 1、`不选择合集` 1 | 1 / 1 | 可复用 `_set_collection`（`douyin_publish.py:1221`）的 degrade 逻辑 → Q3 有了事实依据 |
| **V15** | `选择音乐` exact **2**（区块标题 + 按钮各一），旁注 `点击添加合适作品风格音乐`；预览区显示 `HEYGO创作的原声` | 2 | 默认即「原声」，不碰它不会阻塞。**注意 exact=2**——T3 若要定位这个控件不能假设唯一 |
| **V16** | **未能测出** —— 需要真的点发布 | — | 留给 T7 端到端时观察 |
| **V17** | 作品管理页（`/creator-micro/content/manage`）**已有图文作品**，与视频卡同表混排：图文卡前缀 `N张`（实测有 4/6/11/5 张四条），指标是 `划走率 / 文案展开率 / 平均浏览图片数`；视频卡前缀是时长（`00:28`）+ `完播率 / 2秒跳出率`。**状态文案与视频同词**：`已发布` exact 13（12 张卡 + 顶部筛选 tab 各 1）、`审核中` 1、`未通过` 1（这两个是筛选 tab，当前无该状态的卡）、`定时中` 0、`已下架` 0 | 见左 | `_STATE_MARKERS`（`douyin_verify.py:174-196`）**图文卡同词，零改动**。⚠️ 只覆盖到 `已发布` 这一种真实卡状态 |
| **V17b** | **整页 `<a>` 元素数 = 0**（`a` 0、`a[href*="/video/"]` 0、`a[href*="/note/"]` 0、`a[target="_blank"]` 0） | 0 | **D5 的「链接模式参数化」拿不到任何东西**：不是图文用了 `/note/`，而是这个页面根本没有锚点。`douyin_verify.py:422` 的 `a[href*="/video/"]` 对视频卡同样是死的 —— 与 `publish_readback.py:32-46` 的 [实测 2026-08-08] 结论一致 |

**未能测出的四条，以及为什么**（不猜，留给下游）：

| # | 缺什么 | 原因 |
|---|---|---|
| V7 | 图片顺序如何呈现 | 端点只回计数，读不出缩略图顺序 |
| V8 后半 | 默认封面是不是首图 / 能否选非首图 | 要点开封面弹窗 |
| V10 后半 | 六项自主声明文案 | 要点开声明弹窗 |
| V11 后半 | 定时窗口是否同 2h–14d | 要先选中「定时发布」才渲染时间控件 |

后三条我做过一次**否定性验证**，不是没试：在编辑页直接数六个声明文案、
弹窗标题 `对作品内容添加声明`、`确定`、`2小时`、`14天`、`定时时间`
—— **全部 exact=0 且 substring=0**，`.semi-modal-content` 0、`.semi-select-option` 0、
`.semi-portal` 3 但均不可见。**结论：这些内容不是「渲染了但隐藏」，而是压根没进 DOM，
必须点击才会挂载。** 而勘探端点按设计没有点击能力（§3.2 的硬约束，有逐字检查该模块
的测试守着）。要补这几条，只能等 T3 写图文驱动时顺带记录，或另开一个**能点击但仍不
提交**的工具——后者会打破「模块内不存在提交路径」这条可机器验证的保证，不建议。

**副产物（视频页基线，同日同账号实测）**：视频上传页 file input
`accept="video/x-flv,video/mp4,...,.m4"`、`multiple` **false**、`input_total` 1；
导航栏 `发布视频` 1 / `发布图文` 1 —— 两条链的入口在同一页并列。

---

## 4. 数据模型

**不需要 migration。** 理由逐条：

- 图集顺序 → `publish_tasks.resource_ids` JSONB 数组已承载（`models/distribution.py:182-184`）
- 内容类型 → `publish_tasks.content_type` 已有（`:179-181`）
- 封面 → 图集不用（D4）
- 能力声明 → 是代码常量，不进库
- `min_images` / `max_images` → `PlatformSessionProfile` 的字段，不是表列

⚠️ 如果实现期发现需要 migration（例如 V8 证明图文有独立封面且要存第三个 cover
列），**取号前必须 `git fetch origin master`**——worktree 存活期间 master 会前进。
本文撰写时 `supabase/migrations/` 最大号是 **419**（`419_episode_owner.sql`）。

---

## 5. 任务拆分

每个任务可独立派发。**验收现象必须是用户看得见的那一层**（gap-closure 计划
L485-490 的第一条纪律），**反向验证**是「证明这个守卫真的会拦」。

### 依赖与冲突

```
T0 (勘探端点+实测)  ─┬─────────────→ T3 (douyin 图集 publisher)
                     └─────────────→ T5 (回读兼容)
T1 (能力后端化)     ─┬─────────────→ T4 (backend 校验前移)
                     └─────────────→ T6 (前端图集 UI)
T2 (browser 中立层多图) ────────────→ T3
T3 + T4 + T5 + T6  ─────────────────→ T7 (端到端 + 声明翻转)
T8 (小红书绑定链)  ─────────────────→ T9 (小红书 publisher)
```

- **T0 / T1 / T2 三者互不依赖，可并行**（T0 动 `browser/app/main.py` +
  新模块；T1 动 `session_adapter.py` + router + 前端；T2 动 `publish.py`/`assets.py`）
- **T1 与 T4 与 T6 三者都碰 `session_adapter.py` / `PublishPage.tsx`，必须串行**
- **T3 与 T5 都碰 `browser/app/platforms/douyin_*.py`，但是不同文件**
  （`douyin_publish.py` vs `douyin_verify.py`），可并行
- **T7 必须最后**，且它包含「把 `capabilities.py` 与 profile 同时加 `images`」
  这一步——D1 的 CI 守卫保证这两处必须同 PR

---

### T0 — 只读勘探端点 + 抖音图文页实测 ✅ 已完成（PR #1791 + 本次文档更新）

**做什么**：按 §3.2 实现 `POST /session/inspect`；按 §3.3 跑一轮实测；
把 V1–V17 的结果写回本文（把 `[TO-VERIFY]` 换成 `[实测 日期]`）。

**用户可见验收**：无 UI 变化。产出是本文 §3.4，13 条测出、4 条明确标注未能测出。

**反向验证**（三条都做了）：
1. ✅ 越界 URL 返回类型化拒绝且**未发起访问**：`https://example.com/` →
   400 `reason=url_not_allowed`；另外三种也各拒一次（`http://` 降级、
   userinfo 夹带 `https://creator.douyin.com@evil.example/`、子域
   `https://www.douyin.com/`）。用例把 `run_inspect` 换成会抛异常的地雷，
   证明的是"没去访问"，不只是"返回了 400"
2. ✅ `grep -n "发布\|publish\|click.*确定" browser/app/inspect.py` → 无输出、
   exit 1。且已固化成测试（逐字读该文件，含注释），不再是要记得跑的 grep
3. ✅ 锁竞争实测（时间线，账号 337271352171182）：
   `13:59:27` 勘探取到锁（attempt 1/1）→ `13:59:38` 勘探式再取（attempts=1）
   **立刻让开**（waited 0.0s，`account_busy`）→ `14:00:16–14:00:19` 发布式取锁
   （attempts=10 / retry 3s，与 `publish_distribution` 同参）**重试排队 27.2s
   全程未并发进入**，budget 用尽后放弃 → `14:00:34` 勘探释放 →
   `14:00:37` 发布式**首次尝试即拿到**（attempt 1/10）。
   收尾核对 `pg_locks(advisory)=0`、`idle in transaction=0`，无泄漏。
   ⚠️ 口径说明：第 3 条测的是 `account_session_lock` 这一层（发布 step 真正
   调用的那个 API、同一组默认参数），**没有真的发一条作品** —— 本任务纪律
   禁止点发布。DOM 那半留给 T7。

**风险**：这是本期唯一碰真实账号的只读操作。全程未点发布，探针图片为 ffmpeg
生成的纯色图（1080×1440），经对象存储签发内网 URL，勘探结束已从存储删除。

---

### T1 — 能力声明后端化 + CI 守卫 + 前端表退役

**做什么**：D1 全部四条。

- 新建 `browser/app/capabilities.py`（无依赖），`publish.py:56` 的
  `SUPPORTED_CONTENT_TYPES` 退役，`validate_intent` 改按平台查表
- 新增 `PlatformCapability` schema + `GET /api/v1/distribution/capabilities`
- `supports_publishing=False` 的平台，能力字段置空 + `is_placeholder: true`（D7）
- 删除 `frontend/components/Distribution/capabilities.ts`，
  `PublishPage.tsx:303-308` 改读接口；拆分「无账号」与「不支持」两条文案
- 新增 `backend/tests/test_capability_matches_browser.py`

**用户可见验收**：Images tab 仍然置灰（因为下层还没实现），但**提示文案
现在区分了「你还没连账号」和「已连的平台不支持图集」**；DevTools 里能看到
`/capabilities` 响应，`douyin.content_types = ["video"]`。

**反向验证**：
1. 在本地把 `session_adapter.py` 的 douyin `content_types` 手改成
   `{"video","images"}` 而不动 `browser/app/capabilities.py`，
   跑 `uv run pytest backend/tests/test_capability_matches_browser.py`
   → **必须红**。改回后必须绿。（这一步不做，守卫就是装饰品）
2. `frontend/components/Distribution/PublishPage.imagesGate.test.tsx` 改造后
   仍然断言「点击置灰的 Images tab 不会切过去」
3. `grep -rn "IMAGE_POST_PLATFORMS\|supportsImagePosts" frontend/` → 0 命中

**冲突**：动 `session_adapter.py`、`PublishPage.tsx`、`distribution_router.py`。
与 T4 / T6 串行。

---

### T2 — browser 中立层：多图 asset 角色与 intent 校验

**做什么**：D2 全部。不碰任何 DOM、不碰 `douyin_publish.py`。

- `publish.py`：`IMAGE_ROLE_PREFIX`、`ordered_image_assets()`（纯函数）、
  `assets_to_stage` 按 content_type 分支、`validate_intent` 的 images 分支
  （数量上下界 / 扩展名走 `IMAGE_EXTENSIONS`（`assets.py:46`）/ 标题非空 /
  拒绝 cover）
- 单测：`browser/tests/test_publish_units.py` 扩充

**用户可见验收**：无 UI 变化（能力还没放行）。

**反向验证**：
1. 构造 role 有缺口的 assets（`image:0` + `image:2`）→ `ordered_image_assets`
   必须 raise，不得静默返回 2 张
2. 构造 `content_type="images"` 且带 `cover` 的 intent →
   `validate_intent` 返回 `cover_not_supported_for_images`
3. 构造 `content_type="images"` 送给一个只声明 `("video",)` 的平台 →
   仍然 `unsupported_content_type`（证明 D1 的按平台查表生效）

---

### T3 — 抖音图集 publisher

**依赖 T0（DOM 实测）+ T2（asset 角色）。**

**做什么**：在 `douyin_publish.py` 新增图集驱动路径。

- 新增 `_drive_images()`，与 `_drive()`（`:1422-1443`）并列；`publish()`
  按 `job.intent.content_type` 分流
- 复用（若 T0 证明控件相同）：`_fill_form` 的标题/正文/话题、
  `_set_self_declaration`（`:1105`）、`_apply_options`（`:1041`）、
  `_set_schedule`（`:1270`）、`_confirm_publish`（`:1358`）
- 新写：图集上传 + 完成判定 + 顺序确认
- **不调用** `_set_cover`（D4）
- 失败语义沿用既有非对称原则：声明/可见性/定时 **失败即拒发**
  （`:1041-1049` 与 `:1108-1124` 的原文论证），合集 **降级但上报**

**用户可见验收**：一次真实发布后，抖音 App 上出现一条图文作品，
图片顺序与发布页点选顺序一致，标题/正文/话题/可见性/自主声明全部正确。

**反向验证**：
1. 把可见性设成 `private` 并临时改坏可见性 selector → 必须**拒发**
   （reason `visibility_control_missing`），而不是公开发出去
2. 发一条 3 张图的，把中间那张的顺序号在 payload 里对调 → 平台上的顺序
   随之对调（证明顺序是我们控制的，不是巧合）
3. 传一个损坏的图片 URL → 类型化 `asset_unavailable`，**且不留下半成品草稿**

---

### T4 — backend 校验前移

**依赖 T1（profile 字段）。**

**做什么**：D3。把 `validate_publish_intent` 的纯形状部分前移到
`distribution_router.py::create_task`（`:715-785`）；`profile` 新增
`min_images`；`max_images` 填入实测值（T0 的 V2）。

**用户可见验收**：在发布页选 36 张图（或超过实测上限）点 Publish →
**立刻**红字报错，不产生任务行、不进队列。混选 session 账号与非 session 账号
发图集 → 立刻被拒，而不是产生一半成功一半失败的批次。

**反向验证**：
1. 提交后查 `publish_tasks` → **没有新行**（证明是真的前移了，不是先建后失败）
2. 临时把 `min_images` 设成 2，提交 1 张 → 被拒；设回 1 → 放行
3. 确认 workflow 里那道门（`publish_distribution.py:542-544`）**没有被删**——
   前移是加一道，不是搬一道

---

### T5 — 回读兼容图文作品

**依赖 T0 的 V17。**

**做什么**：D5。链接模式参数化；状态词表按实测补充（不替换）。

**用户可见验收**：发一条定时图文，到点后 Records 页该行从「已排期」
变为「已发布」（而不是卡在 pending 直到 5 次重试耗尽变 abandoned）。

**反向验证**：
1. 把标题改成一个页面上不存在的串 → verdict 必须是 `not_live`/`not_found`，
   证明匹配是真的在比对而不是恒真
2. 把卡片选择器临时改坏 → 必须是 `list_unreadable` → INCONCLUSIVE，
   **不得**变成 `not_live`（这是 `douyin_verify.py:42-65` 明写的安全方向）
3. 视频作品的回读回归测试仍全绿（`browser/tests/test_douyin_verify.py`）

---

### T6 — 前端图集模式收尾

**依赖 T1。**

**做什么**：D6 剩余部分。

- 上/下移排序按钮（复用 `:821-827` 的序号角标位置）
- 修 picker 写死 "videos" 的四处：`:1391` / `:1411` / `:1541` / `:1031`
- i18n 新 key 补 `en.json` + `zh.json`（现状：`distribution.publish` 下
  en/zh 各 174 key 完全对齐、零未译，**不要打破这个**）

**用户可见验收**：图集模式下每张缩略图有上移/下移，点击后序号与预览首图
同步变化；picker 里的文案在图集模式下说的是 images 不是 videos。

**反向验证**：
1. 首张的「上移」与末张的「下移」必须禁用（而不是静默无效）
2. 调整顺序后提交，`createPublishTask` 收到的 `resource_ids` 顺序 = UI 顺序
3. `node -e` 或测试断言 en.json 与 zh.json 的 `distribution.publish` key 集合仍相等

---

### T7 — 端到端验收 + 能力声明翻转

**依赖 T3 + T4 + T5 + T6。**

**做什么**：在**同一个 PR**里把 `browser/app/capabilities.py` 的 douyin 改成
`("video","images")`、`session_adapter.py:268` 加回 `"images"`。
CI 守卫（T1）保证这两处不能只改一处。

**用户可见验收**（缺一不可）：
1. Images tab **自动解除置灰**（前端未发版——证明能力真的是后端下发的）
2. 选 3 张图 → 填标题/正文/话题 → 选「好友可见」→ 选自主声明「内容由AI生成」
   → 定时到 3 小时后 → Publish → 任务行「已排期」
3. 到点后抖音 App 上出现该图文，三张图顺序正确、可见性为好友可见、
   声明已带上；Records 页该行变「已发布」

**反向验证**：
1. 发布后**在抖音 App 上确认**（不是在我们的 Records 页确认）——
   gap-closure 计划 L485-488 的纪律：验到用户看得见的那一层
2. 把 `capabilities.py` 改回 `("video",)` 而不动 profile → CI 红
3. 视频发布端到端跑一次回归，确认没被图集分支影响

**⚠️ 真账号操作**：发出去的是真实作品。用「仅自己可见」跑第一次，
确认全链路后再用好友可见/公开跑第二次。清理时**平台侧要人工删**——
gap-closure 计划 L461 记着上次遗留的 6 个私密作品至今需人工处理。

---

### T8 — 小红书绑定链首次验证（短信）

**独立，需用户实时配合（收验证码）。不依赖前面任何任务。**

**做什么**：用真账号跑通 `POST /session/login/start` →
`sms_required` → 用户输码 → `POST /session/login/{id}/sms` → SUCCESS →
落 `social_accounts` 行。**同时校准 `IDENTITY_COOKIE`**（D7）：
dump cookie 名，确认 `userId` 存在且**跨两次登录稳定**；不稳定就换，
换不到就让绑定类型化失败（`identity_unresolved`）而不是造新行。

顺带用 T0 的勘探端点校准 `PROFILE_TEXT_SELECTORS` /
`PROFILE_ATTR_SELECTORS`（`xiaohongshu.py:166-192`，当前全是 `[GUESS]`）。

**用户可见验收**：Accounts 页出现一张小红书账号卡，**昵称和头像都是对的**
（不是一串 id、不是空白）。断开重连一次，仍然是**同一行**（`updated_at` 递增，
不是新增一行）。

**反向验证**：
1. 故意输错验证码 → UI 显示「验证码错误」，与「请输入验证码」区分开
   （这条链 2026-08-10 刚修过，见 commit `90fb846b`）
2. 连续绑定 3 次 → `social_accounts` 始终 1 行（P0-1 的验收口径）
3. 绑定后 24 小时再看会话巡检，确认 `validate_session` 判它有效
   （`xiaohongshu.py:113-116`）——`LOGIN_TEXT_MARKERS`（`:61-66`）
   当前是 `[COPY] UNVERIFIED`，这一步是它的第一次真实检验

---

### T9 — 小红书 publisher（视频 + 图集）

**依赖 T8。**

**做什么**：

1. 先把 profile 的占位值换成实测值（`session_adapter.py:292-298`）：
   `content_types` / `video_extensions` / `image_extensions` / `max_images` /
   `min_images` / `max_title_len`，并去掉 `is_placeholder`
2. 实现 publisher。⚠️ **基础设计 §6.1 明确：小红书是「档位 2」——浏览器
   只当签名机（`window._webmsxyw` 算 `x-s`/`x-t`），上传走裸 HTTP，不是 DOM
   序列**。选它作第二个平台的全部意义就是验证抽象没有假设「发布 == DOM 操作」。
   若最后写成了 DOM 自动化，说明这个验证没做——要在 PR 里明确说明为什么改主意
3. `supports_publishing=True` + `capabilities.py` 加平台（同 PR，CI 守卫保证）

**用户可见验收**：小红书上出现一条图文笔记，图片顺序正确。

**反向验证**：
1. 翻 `supports_publishing` 之前，发布请求必须被**两层**拦下
   （backend `:697-712` + browser 没注册 publisher）——先证明两层都在，再翻
2. 抖音发布回归全绿（证明没有为了小红书把中立层焊死）

---

## 6. `[TO-VERIFY]` 清单（实现期必须实测填入）

**V1–V17 的勘探已完成 [实测 2026-08-11]，逐条结论与计数见 §3.4。** 下表只留状态。

| 编号 | 内容 | 状态 |
|---|---|---|
| V1 | 图文发布页 URL / 是否灰度双路径 | ✅ 上传页 `?default-tab=3`、编辑页 `/content/post/image`；⚠️ 灰度第二路径**未观察到 ≠ 不存在**（3/3 同一落点，样本不足） |
| V2 | 图片数量上下限 | ✅ max=35（证实）、min=1；另测得单张 50MB |
| V3 | 接受的图片格式 | ✅ accept 含 png/jpeg/jpg/bmp/webp/tif，文案不支持 gif；现 profile 是真子集 |
| V4 | 单张大小上限 | ✅ 50MB |
| V5 | 一次能否多选上传 | ✅ `multiple`=1，一次传 3 张已验 |
| V6 | 上传完成/失败标记文案 | ✅ `已添加{n}张图片` + `清空并重新上传`；⚠️ `重新上传` 是其子串，必须 exact |
| V7 | 排序如何表达 | ❌ **未能测出**（端点只回计数，读不出缩略图顺序）→ T3 实现期验 |
| V8 | 封面语义（首图？可选？） | 🟡 控件存在且是「从已上传图片里选」；默认/可选性**未能测出**（要点弹窗） |
| V9 | 标题长度上限 | 🟡 读到 `0/20` 与 `0 / 1000` 两个计数器；**字段绑定按排除法**定为标题 20 |
| V10 | 自主声明控件与六项是否相同 | 🟡 入口同文案（exact=1）；**六项未能测出**（弹窗内容不在 DOM，需点击） |
| V11 | 定时窗口是否同 2h–14d | 🟡 `立即发布`/`定时发布` 各 1；**窗口未能测出**（要先选中才渲染） |
| V12 | 可见性三项是否相同 | ✅ 完全相同，可直接复用 |
| V13 | 保存权限 允许/不允许 是否存在 | ✅ 存在且同文案；子串陷阱在图文页实测复现 |
| V14 | 合集控件是否存在 | ✅ 存在 |
| V15 | 配乐默认态是否阻塞发布 | ✅ 默认原声，不碰即可；⚠️ `选择音乐` exact=**2**，定位不能假设唯一 |
| V16 | 发布成功跳转路径 | ❌ **未能测出** —— 要真的点发布，本次纪律禁止 → T7 |
| V17 | 图文作品卡的状态文案与链接形态 | ✅ 状态同词（仅覆盖到 `已发布`）；**全页 0 个 `<a>`** → D5 的链接参数化撤销 |
| X1 | 小红书 `IDENTITY_COOKIE` | `"userId"`，`[GUESS] UNVERIFIED`（`xiaohongshu.py:194-211`） | T8 |
| X2 | 小红书 profile 选择器 | 全部 `[GUESS]`（`xiaohongshu.py:166-192`） | T8 |
| X3 | 小红书 `LOGIN_TEXT_MARKERS` | `[COPY] UNVERIFIED`（`xiaohongshu.py:57-66`） | T8 |
| X4 | 小红书 profile 的 content_types / 扩展名 | **占位**（`session_adapter.py:290-298` 原文自承） | T9 |

---

## 7. 风险与开放问题

### 7.1 实现期解决（不需要用户拍板）

| # | 风险 | 处置 |
|---|---|---|
| R1 | 图文页 DOM 与视频页差异大到无法复用任何步骤 | T0 就会暴露；预案是 `_drive_images` 完全独立，只共享纯判定函数。工作量上升但不改架构 |
| R2 | 一次传 N 张时部分失败 | 沿用 `_await_upload_complete`（`douyin_publish.py:791-838`）的「失败先于完成判定」+ 有界重试；**中途失败必须类型化并带上是第几张**，不得半途 silent（CLAUDE.md「触发路径必须类型化失败回显」） |
| R3 | 素材签名 URL TTL 1 小时（`publish_tasks_repository.py:536`）对 N 张图不够 | 现状视频单文件也用同一 TTL 且已跑通；N 张图总时长若逼近 1h，改为**逐张临取 URL** 而不是提前批量签发 |
| R4 | 前端删除 `capabilities.ts` 后接口未返回时的空窗 | 加载中 Images tab 保持置灰（安全方向），不要默认放行 |
| R5 | `/capabilities` 端点的 `frozenset` 序列化不稳定 | D1 已规定 `sorted()`；加快照测试 |
| R6 | T7 在真账号上发出的作品需要人工清理 | 第一次用「仅自己可见」；清理清单写进 PR 描述（上次的 6 个遗留至今未清） |

### 7.2 需要用户拍板

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| ~~**Q1**~~ | ~~T0 勘探要用哪个账号~~ | — | **已拍板并执行**：小号 **HEYGO**（`337271352171182`）。2026-08-11 的 7 次勘探全部用它，MioPoo 一次没碰 |
| **Q2** | V8 实测：图文**有**封面控件，但是「从已上传图片里选」而非独立上传 | (a) 本期接上「可选封面图」(b) 本期仍按「首图即封面」，另开任务 | 仍倾向 (b)，且实测**没有削弱**这个建议：D4 拒绝独立 cover 素材的结论照旧成立。但「默认是不是首图」未能测出（要点弹窗），所以 (b) 带一个已知未知 —— T3 实现期若发现默认不是首图，需要一个后续任务 |
| ~~**Q3**~~ | ~~V14 若图文页**有**合集控件~~ | — | **实测有**（`添加合集` / `不选择合集` 各 exact=1）→ 按原建议 (a) 复用 degrade 逻辑。⚠️ 合集本身至今没验证过（账号里没有合集，gap-closure L453），degrade 路径因此仍是唯一被走到的分支 |
| **Q4** | T7 第一次真实发布用哪种可见性 | (a) 仅自己可见 (b) 好友可见 (c) 公开 | 倾向 (a) 先跑通、(b) 再验一次。(c) 留给 A5「公开发布验证」条目 |
| **Q5** | 小红书二期什么时候开工 | (a) 抖音 T7 验收后立刻 (b) 等 P2-4 环境隔离做完 | 倾向 (a)。T8 只是**绑定**，不产生第二个并发发布账号，不触发 P2-4 的前置条件；T9 真的开始发布前再评估 |
| **Q6** | 排序 UI 只做上下移够不够 | (a) 上下移 (b) 拖拽 | 倾向 (a)。序号角标已把顺序说清楚，拖拽的可访问性与移动端成本不小 |

---

## 8. 范围外（YAGNI）

- **配乐自选**：用户已拍板用平台默认
- **图文的 official / h5 通道**：P2-3 记着该通道两个账号 `access_token` 均为空、
  从没走通过，且 Client Secret 待用户重置。`publish_distribution.py:212-213`
  当前对 official + images 直接 raise，保持不变
- **B 站图文**：`supports_publishing=False`，两层拦截，不动
- **多账号并发发图集**：必须先做 P2-4（`account_environments` 建表 0 行、
  无读取方），只声明边界不展开
- **从 issue 反向发起图集发布**：P2-5 的范畴
- **图集的 `one_to_one` 分发**：`_validate_content`（`distribution_publish.py:120-128`）
  显式排除 images，注释写明「一篇笔记带全部图片，广播给每个账号，永不拆分」——
  这是对的，不动
