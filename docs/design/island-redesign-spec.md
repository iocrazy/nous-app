# Island Redesign — Design Spec & Tokens

> 定稿日期 2026-06-13。交互式 mockup 见 `docs/design/mockups/`（浏览器直接打开，
> 分隔条可拖拽、信息面板可收起）。本文是唯一权威规范；mockup 与本文冲突时以本文为准。

## 0. 决策记录

| # | 决策 | 结论 |
|---|------|------|
| D1 | 整体框架 | **岛式布局**（悬浮圆角面板 + 间隙 + 背景微光），告别贴边分栏 |
| D2 | 全局控件归属 | **全局顶条**（通宽细条，非岛）：语言/任务中心/通知/系统状态/头像。搜索跟内容走（⌘K） |
| D3 | 二级侧栏 | 分组标题（LOCATIONS / SMART FOLDERS）；**管理类（Share Manager / Trash）沉底** + 分隔线 |
| D4 | 图标 | **全局坚决不用 emoji** — 一律 Lucide 线性图标（`lucide-react`） |
| D5 | 主导航 | **任何页面不可消失**；详情类路由自动收缩为 54px 图标栏（hover tooltip，可展开） |
| D6 | 按钮系统 | **方案 B：全 Tinted 浅染**（见 §3），无实底按钮 |
| D7 | 详情面板 | 可拖拽调宽 + 可收起（右缘呼出把手）；信息密度不为"简洁"妥协 |
| D8 | 音频详情 | 整页封面取色氛围；Overview+歌词在左页；右侧=播放列表（future） |
| D9 | 类型徽章 | 详情页类型徽章与网格卡角标同一套图标（video/music/image）+ 语义色 |
| D10 | 返回键 | 「彗尾」签名保留：圆形色环按钮 + 2px 渐隐光带扫过标题下方，hover 发光延伸 |
| D12 | **功能保真（最高准则）** | 改版**只动视觉/布局，功能零增减零遗漏**。每页迁移前先盘点该页全部交互功能形成验收 checklist，迁移后逐项核对；任何功能性改动必须单独立项，不得夹带 |
| D11 | 双主题 | **浅色/深色双主题是 token 化的硬约束**：token 一律语义命名（不带 dark 字眼），值挂 `[data-theme]`；浅色 palette 在 v2 P1 token 化完成后追加（增量 ~2 天），不单独立项 |

## 1. 设计 Token

```css
:root {
  /* surfaces */
  --bg: #08080a;                        /* app 背景（叠 radial 微光） */
  --island: #111114;                    /* 岛面板 */
  --island-2: #17171b;                  /* 岛内嵌入面（输入框/按钮底/卡片 hover） */
  --card: #1d1d22;                      /* 内容卡片 */

  /* lines */
  --line: rgba(255,255,255,.065);       /* 默认描边 */
  --line-strong: rgba(255,255,255,.12); /* 强调描边 / hover */

  /* text（4 档） */
  --text: #e7e7ea; --text-2: #a3a3ad; --text-3: #74747e; --text-4: #55555e;

  /* semantic（RGB 三元组，便于 alpha 合成） */
  --indigo: 99,102,241;   /* 主操作 / active */
  --green: 52,211,153;    /* 成功 / 连接 / Transcript */
  --violet: 167,139,250;  /* AI（Analyze） */
  --amber: 245,158,11;    /* 评分 / Summary / New */
  --red: 248,113,113;     /* 危险 */

  /* geometry（三档） */
  --r-lg: 18px;   /* 岛 */
  --r-md: 12px;   /* 卡片 / 输入 / 统计卡 */
  --r-sm: 9px;    /* 按钮 / chip */
  --gap-frame: 12px;  /* 岛间隙 = frame padding */

  --shadow-island: 0 10px 40px rgba(0,0,0,.45);
  --shadow-card-hover: 0 8px 22px rgba(0,0,0,.4);
}
```

背景微光：`radial-gradient(900px 500px at 75% -10%, rgba(99,102,241,.07), transparent 60%)`
叠在 `--bg` 上。音频详情页改用封面取色（见 §6）。

## 2. 布局框架

```
┌ topbar（通宽细条，全局区：brand ─── 语言·任务·通知·状态·头像）┐
├──────────────┬────────────────────────────────┬───────────────┤
│ nav 岛       │ 工作区岛                        │ 详情岛        │
│ 列表页=200px │ （二级 rail 在岛内，右侧内容）   │ 可拖 / 可收起 │
│ 详情页=54px  │                                │               │
└──────────────┴────────────────────────────────┴───────────────┘
```

- 岛 = `--island` 底 + `--line` 描边 + `--r-lg` 圆角 + `--shadow-island`，间隙 12px。
- **splitter**：岛间 12-14px 命中区，中央 4×44px 圆角竖条；hover/拖拽时 indigo 发光。
  宽度范围按面板定（资源库信息岛 250–480px；详情页信息岛 300–560px）。
- **收起**：面板头部「»」收起；视口右缘出现竖排呼出把手。
- 选中批量操作条：固定底部居中胶囊，`backdrop-filter: blur(14px)`。

## 3. 按钮系统（方案 B · 全 Tinted）

唯一形态：**12% 染色底 + 35% 同色描边 + 亮色文字**。

```css
.btn { background: rgba(var(--c), .12); border: 1px solid rgba(var(--c), .35);
       color: rgb(var(--c-text)); border-radius: var(--r-sm); }
.btn:hover  { background: rgba(var(--c), .2); border-color: rgba(var(--c), .55); }
.btn:active { transform: translateY(1px); }
.btn:disabled { opacity: .4; }
```

| 变体 | --c | 文字色 | 用途 |
|------|-----|--------|------|
| indigo | 99,102,241 | #a5b4fc | 主操作（Share / Save / Upload） |
| green | 52,211,153 | #6ee7b7 | 成功态 / Connected / Transcript |
| violet | 167,139,250 | #c4b5fd | AI 动作（Analyze） |
| amber | 245,158,11 | #fcd34d | Summary / New / 评分 |
| red | 248,113,113 | #fca5a5 | 危险（Delete） |
| neutral | — | --text-2 | 次要（Download / Cancel）：`rgba(255,255,255,.05)` 底 + `--line-strong` 边 |
| ghost | — | --text-3 | 三级（无底无边，hover 出 `--island-2`） |

尺寸：默认 `7px 14px / 12.5px`；small `4.5px 11px / 11.5px`；icon-only 正方形。
音频详情页特例：tinted 色用封面取色 `--tint` 替换 indigo。

## 4. 图标 & 类型语义

- 一律 `lucide-react`，线宽 2，**禁止 emoji**（D4）。
- 类型徽章（24px 圆角方块，网格角标与详情页共用）：
  video=摄像机/indigo · audio=音符/green · image=图框/amber。
- 统计图标语义色：likes=#f87171 · comments=#60a5fa · shares=#34d399 · collects=#fbbf24。

## 5. 页面规格

### 5.1 资源库（mockups/mediahub-resources-redesign-v2.html）
- 二级 rail 收进工作区岛内（200px）：标题 Library → `LOCATIONS`（Downloads/Uploads/Temporary）
  → `SMART FOLDERS ＋` → spacer → 分隔线 → Share Manager / Trash（沉底，D3）。
- 内容头：面包屑（Library / My Uploads）+ H2 + 搜索（⌘K 标识）+ Upload/New。
- 筛选行：segmented 类型切换（All/Images/Video/Audio/Docs）+ 「＋虚线」条件 chip。
- 分组标题带计数（FOLDERS · 4 / FILES · 10）。
- 文件夹卡：folder tab **收进卡片内**（顶部内嵌 8px 圆角块，不外溢）。
- 卡片：hover 上浮 2px + 阴影；选中 indigo 描边 + 光晕。
- 信息岛：click-to-edit（+ Add note / + Add source link / + Add tag 虚线行），
  Properties 双列右对齐 + 右留白，评分并入 Properties 首行。

### 5.2 视频详情（mockups/mediahub-player-redesign.html）
- nav = 54px 图标栏（D5）。stage 岛：头部（彗尾返回键 D10 + 标题/作者 + tinted Share/Download/⋯）
  → 黑场播放区 → 控制条。
- 信息岛 tabs：Overview / Transcript / Analysis + 收起钮。
- Overview 内容序：类型图标徽章+分辨率+ID → 标题 → 时间/时长 → 统计 4 宫格（语义色图标）
  → AI 动作行（Copy/Transcript/Summary/Analyze，tinted）→ Rating → Notes（click-to-edit）
  → Tags（彩色 chip 带 × + 小图标行）→ Platform Tags → Description。

### 5.3 音频详情（mockups/mediahub-audio-redesign.html）
- stage 整体浸入封面取色（D8）：`radial(rgba(--tint,.16)) + linear(rgb(--tint-deep) → 近黑)`。
- 左页双栏：封面侧（封面 + 标题/作者 + 评论·分享·收藏行 + 评分 + 标签）｜
  歌词列（当前句 17px 发光高亮、邻句 50%、远句 28%，顶底 mask 渐隐）。
- 底部播放胶囊：圆形 tint 播放键 + 波形（已播部分 tint 色）+ 时间 + 倍速，CHORUS 标记上浮。
- 右岛 = **Playlist（COMING SOON）**：当前曲目 tint 高亮 + 均衡器动画，队列行=缩略图/标题/作者/时长。
- 取色实现：封面图 canvas 采样主色 → 写入 `--tint` / `--tint-deep`（无封面 fallback indigo）。

## 5.4 双主题（D11）

- token 化时组件**只引用语义变量**（`--island` / `--text-2` / `--line`…），禁止再写 `bg-zinc-*` 等
  调色板类 — 这是 v2 P1 迁移的验收标准（现存 ~5,900 处硬编码即迁移范围）。
- 主题切换：`<html data-theme="light|dark">` + 两份 token 值块；默认跟随系统
  （`prefers-color-scheme`），Settings 里三态切换（System / Light / Dark）。
- 浅色 palette 原则：同一语义层级的明度关系镜像（bg 最浅 → island 次之 → card 最深），
  semantic 色（indigo/green/…）不变，tinted 按钮 alpha 略升（.12 → .15）补偿浅底对比。

## 6. 落地路径

1. **v1 精修 PR**（无框架风险，先行）：5.1 中不依赖岛框架的项 — 侧栏分组/沉底、
   信息面板 click-to-edit + 留白、文件夹卡 tab 修正、卡片 hover 统一、按钮换 tinted。
2. **v2 岛式 epic**（分阶段）：
   P1 token + 全局顶条 + 岛框架（App shell）→ P2 资源库页 → P3 视频详情 → P4 音频详情
   → P5 其余页面（Projects / Issues / AI Library / Settings）逐步迁移。
   每阶段独立 PR，feature flag `VITE_FEATURE_ISLAND_UI` 控制（上线 2 周内删）。
