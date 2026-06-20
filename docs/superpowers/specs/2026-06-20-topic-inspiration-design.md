# 话题灵感模块（Topic Inspiration）设计文档

Status: Draft v1 · 2026-06-20
作者: 设计协作（brainstorming + visual companion 迭代）
取代: 现 `pages/ParserPage.tsx`（Media Parser）

---

## 1. 目标与定位

把现有的 **Media Parser**（纯"链接进→媒体出"下载器）重构成一个综合性的 **话题灵感** 模块：

> 自托管 NewsNow 聚合多平台热点 → 特调 Agent 评分/理由/摘要 → 时间线呈现 → 闭环:选中热点一键 **生成脚本(script_ai)** 或 **解析下载(素材库)**。

核心闭环（用户确认 D 全闭环）：**发现热点 → 选中 → 喂 AI 生成内容**(主)，**或解析下载**(次,仅当含媒体)。

> **重要(2026-06-20 修正)**：热点条目**主要是新闻/文本话题**(如"某人加入某公司"这类纯资讯,无可下载媒体),**不一定是 media**。所以:
> - **主闭环 = 生成脚本/内容**(news/topic → 创作灵感),所有条目都有。
> - **解析下载是次要、有条件的**——**仅当**某条热点带**可解析的媒体链接**(如热榜里一条抖音/B站/YouTube 视频)才出"解析下载"按钮。纯文本新闻不显示。

参考：`aihot.virxact.com`（TrendRadar 血统的托管增强版）。**借设计与逻辑,不嵌代码**——TrendRadar 是为 GitHub Actions + 文件输出设计的批处理工具,与 mediahub 栈（DBOS / Supabase / React / AgentRunner）不合。

## 2. 范围

**In scope（纯内容热点）**：
- 多平台热榜 + RSS + 自定义 API 信源（内容类）
- 时间线 / 当前热点（多信源聚合）/ 推荐理由 + AI 摘要 / 分类 / 搜索
- 闭环动作：生成脚本、解析下载、加标签
- 信源管理（Settings）
- 解析能力（吸收旧 parser，合三为一 + 悬浮伸缩）

**Out of scope**：
- 数值数据 API（汇率/金价等）—— 用户明确排除
- 外部 agent runtime 接入（用户明确不要）
- 多渠道推送（已有 Discord;后续可选）

## 3. UI 设计（已经 visual companion 迭代定稿）

### 3.1 铁律（沿用 island redesign）
- **零 emoji** —— 一律 Lucide line icon / 纯文字。绝不用 🔥💡🎬 等彩色 emoji。
- **双主题** —— 全部用 `index.css` 的语义 token（`--ink-*` / `--island` / `--island-2` / `--line` / `--line-strong` / `--content` / `--content-2..4` / `--accent`），它们随 `[data-theme="light"|"dark"]` 自动翻。浅色:island `#fff` / app-bg `#f4f4f6`;深色:island `#15151a` / app-bg `#050507`。**两套主题免费得到,不写死颜色。**
- 主 accent = `--accent` `#6366f1`(indigo);次 accent = amber(`btn-tint-amber`,#b45309 light)。按钮用 `btn-tint-indigo` / `btn-tint-amber`,不用实心重填充。
- 圆角 `--r-lg` 18px;卡片边 `--line-strong`;subtle 内嵌用 `--island-2`。
- D12 功能零增减原则;导航不消失;密度不减。

### 3.2 布局（全局壳内）
全局顶栏（已有:EN/搜索/Task Center/通知/盾/头像）+ **新增 Engine 自检 health 指示**(复用 systemStatus,小圆点/badge,非大卡)。
全局左导航（已有,"话题灵感"取代"Link Parser"入口）。
页内三栏:`[页内信息流(全宽)] [右侧信息小卡]`(信源管理移 Settings,不占页内栏)。

**一整页连续流**（像 Resources 主区,非卡里套卡）:
1. **页头**: 标题"话题灵感" + 副标题 + 分类 chips（全部/模型/产品/行业/论文/技巧）+ 搜索框。底部细分隔线。
2. **当前热点块**: 轻量内嵌（`--island-2` 底）,排名 1–N + 每条"N 个信源 · X 前"。标"多信源热度·随时间消退"。
3. **日期 + 日历**: `日期 ▾` 可点开日历,**有热点的日子高亮**,点选切换该天。
4. **时间线**: 左侧**竖线 + 圆点**(圆点压在竖线上,中心对齐线),时间戳右对齐在线左;**每条热点 = 一张独立小卡**(`--island` + `--line-strong` 边)。卡内:来源 / 标题 / 摘要 / tags / 分数 badge / 推荐理由(绿色左条)。

### 3.3 右侧信息小卡（点击热点出）
像 Resource Info 侧栏。内容:
- 标题
- **精选理由**(amber 框) + **AI 摘要**(indigo 框) —— 特调 Agent 产出
- 动作:**生成脚本 → script_ai** / **解析下载 → 素材库** / **加标签** / **查看原文 ↗**
- PROPERTIES: 来源 / 分数 / 时间 / 信源数

（可选 Phase 后:点"展开"进全详情页:精选理由+AI摘要 顶部突出 + AI翻译/原文切换 + 正文 + 媒体 + 引用 + tags + 原文链接。）

### 3.4 解析（吸收旧 parser）
- **合三为一**: 一个智能输入,自动识别 单链 / 多链批量 / 歌单,下方实时显示检测("检测到:歌单·12 首"),一个 Analyze 按检测路由。
- **悬浮伸缩**: 右下角三态——
  - A 收起 = 小药丸"解析链接"
  - B 输入 = 粘贴 + Analyze + 检测
  - **C 结果 = parse 内容落点**: 媒体预览(标题/时长/封面)+ **AI Processing 开关**(Transcript/Summary/Analyze,保留)+ **Tags**(保留)+ **"保存到 Resources 素材库"**
- 保存后媒体进 **Resources 素材库**;AI 处理走 **Task Center**。话题灵感页保持纯发现。

### 3.5 搬走/隐藏（用户决定）
- **Storage 卡 → Settings**(用量展示)。
- **Engine 状态 → 全局顶栏 health**。
- **下载卡片功能 → 隐藏 UI,代码保留**(feature flag/注释,不物理删);任务进度走已有 Task Center。

## 4. 数据模型（Supabase）

新增表（migration 下一个序号）：

### 4.1 `signal_sources`（信源,Settings 管理）
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint snowflake | PK |
| user_id | uuid | 归属(RLS) |
| kind | text | `newsnow` / `rss` / `http_api` / `custom` |
| name | text | 显示名 |
| config | jsonb | newsnow:`{platform_id}` · rss:`{url}` · http_api:`{url,method,headers,mapping}` |
| enabled | bool | 开关 |
| category | text | 可选分类 |
| health | text | **派生健康**:`ok` / `degraded` / `dead`(见 §6.5),默认 `ok` |
| consecutive_failures | int | **连续失败计数**(成功清零),默认 0 |
| last_error | text | 最近一次失败原因(截断),可空 |
| last_fetched_at | timestamptz | 最近一次抓取尝试,可空 |
| last_ok_at | timestamptz | 最近一次抓取成功,可空 |
| created_at | timestamptz | |

### 4.2 `hotspots`（热点条目,核心）
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint snowflake | PK |
| user_id | uuid | RLS |
| source_id | bigint → signal_sources | 来源 |
| source_label | text | 显示来源(如 "MarkTechPost (RSS)") |
| title | text | 标题 |
| url / origin_url | text | 原文链接 |
| content_original | text | 原文 |
| content_translated | text | 译文(特调 Agent 产) |
| summary / ai_summary | text | **AI 摘要**(特调 Agent 产) |
| reason | text | **推荐理由**(特调 Agent 产) |
| score | numeric | **相关度分 0~1**(特调 Agent 产) |
| tags | text[] | 分类标签 |
| category | text | 一级分类(模型/产品/行业…) |
| topic_group_id | bigint → topic_groups | **跨源聚类归属(多信源)** |
| media_url / cover_url | text | **可空** —— 仅当条目带可解析媒体(视频/音频)才有。**非空 ⇒ 显示"解析下载"按钮**;纯文本新闻为空 |
| captured_at | timestamptz | **抓取时间(时间线排序 + 日历聚合的轴)** |
| rank_timeline | jsonb | 排名涨落历史(可选,趋势用) |
| dedup_key | text | (source_id, normalized_url/title) 去重 |

### 4.3 `topic_groups`（跨源聚类 = 多信源当前热点）
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint snowflake | PK |
| user_id | uuid | |
| label | text | 话题标题(代表条目) |
| source_count | int | **去重信源数** |
| heat | numeric | 热度 = f(source_count, 时间衰减) |
| first_seen / last_seen | timestamptz | |
| embedding | vector | 聚类中心(可选,语义聚类用) |

去重:`hotspots.dedup_key` 唯一索引。时间线 = `ORDER BY captured_at DESC`。日历 = `GROUP BY date(captured_at)`。当前热点 = `topic_groups ORDER BY heat DESC`。

## 5. 信源适配器（Source Adapter）

后端统一接口,把任意来源归一成 `hotspots` 候选条目:

```
fetch(source: SignalSource) -> list[HotspotCandidate]   # {title,url,content,source_label,captured_at,media_url?}
```

实现:
- `newsnow`: 打**自托管 NewsNow** API(`{api_url}/api/s`),传 platform_id。自托管一个轻量容器(同 NAS,免费 MIT)。
- `rss`: feedparser 解析 `{url}`。
- `http_api`: 声明式——`{url, method, headers(放 key), mapping(JSON path → title/url/time)}`,简单 API 纯配置不写码。
- `custom`: 少量奇葩源写小适配器。

## 6. 抓取流水线（DBOS scheduled workflow）

复用 mediahub 现有 DBOS 调度（同 liveness/sweeper 模式,**不引外部 broker**）:

```
@DBOS.scheduled("*/N min")  topic_fetch_workflow:
  for src in enabled signal_sources:
    candidates = adapter.fetch(src)         # 各源适配器
  dedup vs 已存(dedup_key)
  keyword 粗筛(便宜,先砍噪音)               # 省 token
  → 幸存候选交给 特调 Agent(下一节)
  → 写 hotspots + 更新 topic_groups
```

NewsNow 自托管 = 数据引擎;mediahub 只编排 + 评分 + 存储 + 呈现。

### 6.5 信源健康：检测 + 提示（本期范围）

> 范围裁剪(用户定):**本期只做"检测 + 提示"**。自动切换/cookie 复用/NewsNow 升级自动化 = 后面再加。
> 对标 TrendRadar:它只把当次失败 `failed_ids` 塞进推送报告,**不跨 run 持久化**。我们把它持久化成源状态并在 UI/告警暴露,仅此一步,不过度设计。

**检测**(抓取 workflow 内,逐源隔离)：
- `for src in sources` 每个源独立 `try/except`,**一个源失败绝不中断其他源**。
- 判定一次抓取成功/失败:HTTP 非 2xx、超时、NewsNow 返回 `status` 非 `success/cache`、或解析出 0 条 → 记为该源本次 **fail**。
- 结果写回 `signal_sources`:`last_fetched_at` / `last_ok_at` / `last_error`(截断文本)/ `consecutive_failures`(成功清零、失败 +1)/ `health`(派生:`ok` | `degraded`(1≤fail<N) | `dead`(fail≥N,N 默认 3))。
- **不动 `task_tracking`**——这是信源元数据,不是任务;遵守"task_tracking 单一数据源"纪律(信源健康是独立的 `signal_sources` 自有列)。

**提示**：
- **Settings → 信源管理**列表每行显健康 badge:`ok` 灰/`degraded` amber/`dead` 红 + `last_error` + 相对时间。**死源显红,绝不静默产出 0 条**(避开 mediahub 踩过的"以为在跑其实早死"陷阱)。
- 某源从非 dead **翻转**到 `dead`(穿过阈值那一刻,只触发一次,不每轮刷屏)→ 复用现有 **discord-notify** 发告警:源名 + 连续失败次数 + last_error。
- 模块页右上 health 自检指示(已定的 topbar badge)聚合:存在任一 `dead` 源 → 指示降级,点开跳信源管理。

**后面再加(本期不做,记此备忘)**：自动 failover 到备用源 / 下载侧 cookie 失效检测复用(现有 parse_chain B站 `isLogin` 校验)/ NewsNow 容器升级 runbook 自动化。

## 7. 特调 Scoring Agent（核心,复用 AgentRunner）

**这是 mediahub 比 TrendRadar 高级的地方**。TrendRadar 的 AI 是 3 个散装 prompt 调用(筛选打分/整批简报/翻译),无 agent、无 per-item 理由、无跨源聚类。我们用**一个特调 Agent**(AI Library 里建,复用 AgentRunner + Skill + RunRecorder + ai_provider)承担:

### 7.1 Per-item（每条幸存候选,一次产出全部）
- **相关度分** `score` 0~1（按用户兴趣描述,类似 TrendRadar ai_interests,但同一次调用顺带产理由）
- **推荐理由** `reason`（aihot 有、TrendRadar 无:为什么值得看,一句话）
- **AI 摘要** `ai_summary`
- **分类/标签** `category` / `tags`
- （可选）**翻译** `content_translated`

低于 `min_score` 丢弃。**混合**:关键词粗筛在前(workflow 层),只把幸存少量喂 Agent,压 token。

### 7.2 Per-batch（跨源聚类 = 多信源当前热点）
- 对一批 hotspots 做**语义聚类**(用 mediahub 已有 **ModelScope embedding** 基建:embed 标题 → 聚相似 → 一个 cluster 跨 N 个去重 source = 热)。
- 写/更新 `topic_groups`:`source_count` + `heat = f(source_count, 时间衰减)`。
- 这是 **TrendRadar 完全没有、aihot 的核心价值**,自建。

### 7.3 复用约定
- 建一个 AI Library agent(slug 如 `topic-scorer`)+ 一个 scoring skill。
- 走 AgentRunner（prompt_composer 缓存边界 + RunRecorder 遥测 + budget guard）。
- provider 用平台配置(qwen/doubao 等,litellm 不引)。

## 8. 闭环动作

| 动作 | 何时显示 | 实现 |
|------|----------|------|
| **生成脚本**(主) | **所有条目** | 选中热点(标题/摘要/原文/推荐理由)作为输入 → 调 **script_ai agent**(已有)生成脚本/文案。这是"话题灵感"的主价值:新闻/选题 → 创作 |
| **解析下载**(次) | **仅 `media_url IS NOT NULL`** | 媒体链接 → 走现有 **parser**(media_router/parse_chain)→ 存 **Resources 素材库**。纯文本新闻不显示此按钮 |
| **查看原文** | 所有条目 | 跳 `origin_url` |
| **加标签** | 所有条目 | 复用 unifiedTagService |

## 9. Settings：信源管理 + Storage

- 信源管理 UI:列表 + 每条选 `kind`,`http_api` 展开 url/headers/mapping 表单,`rss` 填 url,`newsnow` 选平台 id。关键词规则编辑。**每行显 §6.5 健康 badge(ok/degraded/dead)+ last_error + 相对时间;死源显红。**
- Storage 用量展示(从 Media Parser 搬来)。

## 10. 前端服务/路由

- 新服务 `topicService.ts`（hotspots 列表/按日期/topic_groups/触发解析/生成）。
- 路由复用现 Link Parser 入口,组件重写为 `TopicInspirationPage`（吸收 ParserPage 的解析逻辑到悬浮组件）。
- 全部用 island token,零 emoji,双主题。

## 11. 分期

- **Phase 1（MVP）**：自托管 NewsNow + 信源(newsnow/rss)+ 关键词粗筛 + 时间线页(卡/点/日历)+ 闭环按钮(解析下载/生成脚本)+ 悬浮解析。**先跑通闭环**。
- **Phase 2**：特调 scoring Agent（score+reason+summary 混合 AI）+ http_api 适配器 + 信源管理 UI（含 **§6.5 健康检测 + 提示**:badge/告警/topbar 聚合）+ 右侧信息小卡。
- **Phase 3**：跨源聚类(多信源当前热点,embedding)+ 详情页 + 趋势可视化 + (可选)推送。

## 12. Non-goals / 已决

- 不嵌 TrendRadar 代码（栈不合）。
- 不接外部 agent runtime。
- 数值数据 API 不进。
- 下载卡片代码**保留不删**,仅隐藏。
