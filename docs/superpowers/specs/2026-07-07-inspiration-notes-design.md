# 灵感库重设计 — memos 式灵感笔记 + 日历 + 多格式附件(Design Spec)

- 日期:2026-07-07
- 状态:已通过用户设计评审(mockup v6 定稿,artifact c897f3b8;六轮迭代记录见下)
- 参考软件:[usememos/memos](https://github.com/usememos/memos)(本地 `/Volumes/program/project-code/github-repos/memos`,**MIT 协议**——思路与代码均可合法借用,无 clean-room 约束)

## 1. 背景与定位

现有 `TopicInspirationPage`(`/inspiration` 灵感库)是纯"外部热点消费"页面:热点 timeline + 分类/来源过滤 + For You + FloatingParse。用户需要它进化成**灵感笔记为主、热点为辅**的创作前置工作台:看到热点有感触 → 一键存成带引用的笔记;平时随手快记(文字 + 任意格式文件);按日历回看"那天我想了什么 + 那天在热什么"。

**用户已拍板的决策**(brainstorm 逐题确认):

| 决策点 | 结论 |
|--------|------|
| 页面结构 | 笔记为主,热点为辅(顶栏 tab 切换主区,Notes ⇄ Hotspots) |
| 日历形态 | 热力图 + 月历**两个都要**,但收敛为侧栏单面板角上切换;**全页只有一个日历,笔记和热点共用** |
| 附件存储 | **不管大小全进 Supabase Storage**(自托管落 NAS 卷);单文件上限 admin 可配(默认 500MB)防呆;**必须可换后端**(读写包在后端 adapter 后面,DB 只存中立元数据) |
| 标签 | inline `#tag`,**独立体系**(不入资源库 tags 表);**热点也要 tags**,侧栏 Tags 面板两 tab 共用 |
| 热点闭环 | 引用卡闭环 + 旧能力全保留(FloatingParse/For You/来源健康只换位置不删) |
| 编辑器 | 纯 textarea + Markdown 渲染(react-markdown 已有),不用 TipTap |
| Parse URL | **页面全局按钮**,顶栏右侧统一位置,两 tab 一致 |

## 2. UI/UX 定稿(mockup v6)

设计稿 artifact:`c897f3b8-a64f-4691-92ba-e0c669bbef8f`(v1→v6 迭代:v2 日历入侧栏角切换+抽屉、v3 日期全页联动、v4 抽屉内日历、v5 废抽屉改顶栏 tab+单日历、v6 Parse 全局+热点 tags)。

### 2.1 页面骨架(桌面)

```
┌─ 左导航 rail(现有 AppLayout,不动)
├─ 顶栏 island:标题 | [Notes ⇄ Hotspots] seg | 过滤 chips(#tag ×、Jul 7 ×)| 搜索框 | Parse URL(全局)
├─ 主列(随 tab 切换)          ├─ 右侧栏(292px)
│  Notes tab:                  │  Activity 面板(角上 热力图 ⇄ 迷你月历 切换,localStorage 记忆)
│    composer(置顶快记框)     │  Tags 面板(Notes→笔记 #tags;Hotspots→热点标签)
│    按日分组笔记 timeline      │  Hotspots Top3 面板(仅 Notes tab;行内 + 存灵感;All hotspots → 切 tab)
│  Hotspots tab:               │  Notes 面板(仅 Hotspots tab;mini 快记输入框 + 最近 2-3 条笔记 + Open in Notes →)
│    头行:All/For You/Saved + 来源健康 chip
│    左列当日排名列表 + 右侧详情面板(热度走势/摘要/标签/原链)
│    底部动作条:Save as note / Parse / Not interested / Open source
```

**侧栏对称原则**:Notes tab 右下是 Hotspots 面板,Hotspots tab 右下是 Notes 面板(mini composer 直接可存,不必切 tab)——在哪个 tab 都能完成"看"与"记"两件事。

### 2.2 核心交互契约

1. **日期是全页共享状态**:点热力图格/月历某天 → 顶栏出现 `Jul 7 ×` chip;笔记流、Hotspots 侧栏面板(标题变 `Hotspots · Jul 7`)、Hotspots tab 列表三处同步。清除 chip 全部回"今天"。热点系统已按日键控(`getHotspotDates`),此联动零新后端。
2. **一个日历组件**:侧栏 Activity 面板 = 热力图模式(16 周密度格,点格过滤)⇄ 月历模式(密度圆点、今天描边、选中实心、月份翻页)。两模式驱动同一个日期状态。
3. **composer**:placeholder 引导;inline `#tag` 输入时自动补全(取用户已有标签前缀匹配);粘贴图片直接上传;拖放任意文件;⌘Enter 保存。
4. **笔记卡**:时间戳 + `···` 菜单(edit/pin/delete);`#tag` 渲染为 accent chip 可点击过滤。
4b. **富 Markdown(对齐 memos 渲染栈)**:react-markdown + remark-gfm(表格/删除线/autolink/任务清单)+ remark-breaks(已有)基础上,新增 **highlight.js 代码块高亮**、**remark-math + rehype-katex 数学公式**、**任务清单 checkbox 可交互**(点击切换 → PATCH 回写 content_md 对应行,memos 招牌体验)。渲染统一走 DOMPurify/rehype-sanitize 消毒。
5. **附件渲染分型**:image/* → 缩略图网格(点开 lightbox);audio/* → 内嵌播放器 pill;video/* → poster 缩略图(点开播放);其余(pdf/office/任意)→ 类型徽标 chip(文件名+扩展名+大小,点击下载/预览)。
6. **热点引用卡**(refcard):左 accent 竖线卡,含来源徽标、标题、热度、`Open detail →`(切到 Hotspots tab 并选中该条)。由"存灵感"动作生成,用户想法写卡上方正文。
7. **Save as note 闭环**:Hotspots tab 底部主按钮/侧栏行内 `+` → 切回 Notes tab,composer 预填引用卡 + 热点标签(inline #tags),光标落正文首行。
8. **热点 tags**:热点数据的 category/keywords 映射为标签;详情面板标题下 chips;侧栏 Tags 面板在 Hotspots tab 显示热点标签聚合,点击 = 过滤 chip。
9. **搜索**:一个框同时查笔记(content ILIKE + tags)和热点(现有 query 通路),按当前 tab 作用。
10. **移动端**:单列;侧栏面板折叠为 feed 上方横滑条;Hotspots tab 列表在上详情在下。
11. **铁律遵守**:零 emoji 图标(lucide);导航不消失;禁 zinc(用 ink/island token);UI 全英文 + i18n key;测试数据英文。

## 3. 数据模型(新迁移,三位数顺延)

### 3.1 `inspiration_notes`

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK | Snowflake |
| user_id | UUID NOT NULL | 归属用户(个人笔记,不做团队 scope——YAGNI,后续要共享再加) |
| content_md | TEXT NOT NULL DEFAULT '' | Markdown 正文 |
| tags | TEXT[] NOT NULL DEFAULT '{}' | 从正文解析出的 inline #tags(**后端**保存时解析,保证一致;GIN 索引) |
| ref_hotspot | JSONB NULL | 热点引用快照 `{hotspot_id, title, source, heat, url, captured_at}`(快照而非 FK——热点行会老化清理,笔记引用必须自持) |
| pinned | BOOLEAN NOT NULL DEFAULT false | 置顶 |
| note_date | DATE NOT NULL | 归属日(日历/热力图聚合键);**后端按 Asia/Shanghai 从创建时刻计算**,不用 DB CURRENT_DATE(服务器 UTC 会把晚上 8 点后的笔记记到"明天") |
| created_at / updated_at | TIMESTAMPTZ | 常规 |
| deleted_at | TIMESTAMPTZ NULL | 软删 |

索引:`(user_id, note_date DESC)`、`(user_id, pinned)`、`GIN(tags)`。RLS:owner-only(参照现有表模板,含 service_role 通道)。

### 3.2 `inspiration_attachments`

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK | Snowflake |
| note_id | BIGINT NOT NULL FK → inspiration_notes ON DELETE CASCADE | |
| user_id | UUID NOT NULL | 冗余,便于 RLS 与配额 |
| storage_backend | VARCHAR NOT NULL DEFAULT 'supabase' | **adapter 键**,换后端时新行写新值,旧行照读 |
| bucket | VARCHAR NOT NULL | 新建 `inspiration` bucket(与 chat-media 隔离,便于独立配额/清理策略) |
| path | TEXT NOT NULL | `{yyyy}/{mm}/{dd}/{uuid}/{filename}` **日期分桶铁律** |
| mime | VARCHAR NOT NULL | 渲染分型依据 |
| size_bytes | BIGINT NOT NULL | 上限校验 + 配额统计 |
| original_name | TEXT NOT NULL | 展示名 |
| created_at | TIMESTAMPTZ | |

### 3.3 `inspiration_api_tokens`(外部录入 PAT)

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK | Snowflake |
| user_id | UUID NOT NULL | 归属 |
| name | VARCHAR NOT NULL | 用途备注("iOS Shortcut" 等) |
| token_hash | VARCHAR NOT NULL | SHA-256,明文只在创建时返回一次(遵守 secret-at-rest 口径) |
| last_used_at | TIMESTAMPTZ NULL | 审计 |
| created_at / revoked_at | TIMESTAMPTZ | 撤销即失效 |

### 3.4 配置(走 DB,遵守 env→DB 铁律)

`system_settings` 增键 `inspiration.max_attachment_mb`(默认 500,admin 可调)。

## 4. 后端(Router → Service → Repository,ORM 2.0 口径)

`/api/v1/inspiration` 前缀,新 router 挂 `main.py`:

| 端点 | 方法 | 说明 |
|------|------|------|
| `/notes` | GET | 列表:`?date=&tag=&q=&limit=&before_id=`(keyset 分页,服务端过滤——铁律) |
| `/notes` | POST | 创建(content_md + 可选 ref_hotspot);后端解析 #tags |
| `/notes/{id}` | PATCH | 编辑 content_md(重解析 tags)/ pinned |
| `/notes/{id}` | DELETE | 软删 |
| `/notes/activity` | GET | `?from=&to=` → `[{date, count}]`(热力图/月历密度,一条 GROUP BY) |
| `/notes/tags` | GET | 用户标签聚合 `[{tag, count}]`(composer 补全 + 侧栏面板) |
| `/attachments/upload` | POST | multipart;校验 mime 白名单宽松 + size ≤ 配置上限;写 storage(adapter)+ 插行;返回元数据 |
| `/attachments/{id}` | GET | 302/流式返回(经 adapter 签名 URL);owner 校验 |
| `/attachments/{id}` | DELETE | 删行 + best-effort 删对象(失败仅告警,不阻断) |
| `/tokens` | GET/POST | PAT 管理:列表(不含明文)/ 创建(明文仅此一次返回) |
| `/tokens/{id}` | DELETE | 撤销(置 revoked_at) |

**外部录入(用户明确要求)**:`POST /notes` 与 `POST /attachments/upload` 同时接受两种认证——现有 Supabase JWT(页面用)与 `Authorization: Bearer <PAT>`(外部脚本/快捷指令/bot 用)。PAT 校验:SHA-256 比对 + revoked/last_used 更新;scope 固定为 inspiration 读写,不放大到其他模块。PAT 在 Settings → API Tokens 面板管理(生成时弹一次明文+复制按钮)。外部调用示例写进端点 docstring:`curl -X POST .../api/v1/inspiration/notes -H "Authorization: Bearer mhk_..." -d '{"content_md":"idea #tag"}'`。

**StorageAdapter 边界**(§1 用户明确要求可扩展):`app/services/inspiration_storage.py` 定义 `put(path, stream, mime) / sign_get(path) / delete(path)` 协议,首个实现 `SupabaseStorageAdapter`(复用 chat-media 通道的客户端);`storage_backend` 列选 adapter。前端**永不**直连 Supabase Storage。

**热点标签**:首版契约定死 `tags = [category]`(hotspots 响应已含 category,service 层映射为标签形态);聚合端点 `/topics/hotspot-tags?date=` 返回当日标签计数。关键词级细标签留待后续迭代,**不为此加爬虫改动**(YAGNI)。

**错误处理**:全走 app 统一 envelope;上传超限 413 + 明确文案;storage 故障 502 + application_logs 记录;笔记不存在/越权一律 404(不泄露存在性,同 sessions 模式)。

## 5. 前端

```
frontend/pages/InspirationPage.tsx        # 重写(替换 TopicInspirationPage 内容,路由不变)
frontend/components/Inspiration/
  Composer.tsx            # 快记框:textarea + tag 补全 + 粘贴/拖放上传 + ⌘Enter
  NoteCard.tsx            # 笔记卡:MD 渲染 + tags + 附件分型 + refcard + ··· 菜单
  NoteTimeline.tsx        # 按日分组虚拟化列表(@tanstack/react-virtual 已有)
  ActivityPanel.tsx       # 热力图 ⇄ 迷你月历(单组件双模式,localStorage 记忆)
  TagsPanel.tsx           # 双 tab 语义的标签聚合面板
  HotspotsSidePanel.tsx   # Notes tab 的 Top3 + 存灵感
  NotesSidePanel.tsx      # Hotspots tab 的 mini composer + 最近笔记(对称面板)
  HotspotsWorkspace.tsx   # Hotspots tab:列表+详情双栏(内容迁自 HotspotCard/HotspotInfoPanel)
  AttachmentView.tsx      # image/audio/video/file 四型渲染
  attachmentUpload.ts     # 上传封装(进度/失败重试一次/上限前端预校验)
  noteTags.ts             # #tag 解析(与后端同一正则契约,纯函数可测)
frontend/services/inspirationService.ts   # API 层
```

- **复用不重写**:FloatingParse、For You 兴趣编辑、SourceHealthBadge、hotspotRanking 原样迁移挂载;`topicService.ts` 不动只扩。
- **feature flag**:`VITE_FEATURE_INSPIRATION_NOTES`(默认 false)。off = 现页原样;on = 新 InspirationPage。旧组件文件保留到 flag 拆除。
- i18n:`inspiration.*` 命名空间,en/zh 同步补。

## 6. 测试

- 后端:notes CRUD/过滤/keyset、#tag 解析契约、activity 聚合、attachment 上限/越权 404/软删链、adapter 协议 mock 测;lint gate(black/isort/flake8)。
- 前端:noteTags 纯函数、Composer 交互(补全/粘贴上传 mock)、AttachmentView 分型渲染、ActivityPanel 日期状态联动(vitest,现有 219 文件套路)。
- E2E/真机:done 前 Vercel preview 真实视觉过一遍(**feedback_ui_early_visual_ux_pass 铁律**),对照 mockup v6 检查主题/对齐/空态。

## 7. 明确不做(YAGNI)

- 团队/共享笔记、评论、协同
- 笔记全文向量搜索(现有 ILIKE 够首版)
- 大文件转存资源库的自动迁移
- memos 的 webhook/公开分享/RSS
- 热点爬虫侧为 tags 做任何新抓取

## 8. 交付切分(实施计划的骨架)

1. **P1 数据+后端**:迁移、repo、service(含 StorageAdapter)、router、测试 —— flag 无关,纯新增
2. **P2 前端 Notes 主体**:InspirationPage 骨架 + composer + timeline + 附件 + 日历面板(flag-dark)
3. **P3 热点融合**:Hotspots tab、双侧栏对称面板(Hotspots Top3 / Notes mini)、存灵感闭环、热点 tags、全局 Parse
4. **P4 富格式+外部 API+GO-LIVE**:代码高亮/KaTeX/交互 checkbox、PAT 管理面板与双认证端点、移动端、i18n 校对、真机视觉过、开 flag

每个 P 一个 PR,合入即 ship(flag-dark),遵守 epic 流程(SDD + 机审前置 + 发布硬闸)。
