# 全站配色重构 — 暖纸基底 + 低饱和五色点缀系统

日期:2026-07-29 ｜ 状态:设计稿已过用户评审(mockup: `2026-07-29-project-palette-mockup.html`),范围经用户升级为**全站**(项目、Agent/AI 库、资源库、灵感库等全部模块)。

## 1. 设计定案(mockup 已拍板)

**色彩纪律三条:**
1. **主角只有绿** `#1E7A5B`——主按钮、active 态、选中态、当前节点,全站唯一强调色
2. **四配角同饱和度带**(S≈45%),语义固定:**赭** `#A87B2B`=等待/警示 · **砖红** `#AD5147`=危险/逾期/错误 · **钢蓝** `#46708E`=信息/链接 · **李紫** `#7A5E8F`=AI/Agent 专属;各配 soft 底与描边档
3. **黑色退役为文字色**——不再有黑色 active 胶囊/黑色 chip

**基底(light 主题)**:页面 `#F3F0E9` / 岛面 `#FCFBF8` / 二级面 `#F6F4EE` / 线 `#E5E0D4`/`#D6D0C0` / 文字 `#1C1B18`/`#57544B`/`#8C887B`。
**Dark 主题**:基底不动(现有 ink 暗色),仅点缀色换成五色系统的暗色适配档(同色相,提亮至暗底可读)。

## 2. 实施架构(用户确认走 CSS 复用路线)

全站硬编码色类约 1700 处(indigo 415 / red 321 / amber 275 / emerald 185 / purple+violet 113 / green 73 / rose 81 / blue+sky 98),逐处清扫不可行。Tailwind v4 的 utilities 由 `@theme` 变量解析(`bg-indigo-600` → `--color-indigo-600`),因此:

### 2.1 Token 层(一次生效 1700 处,零组件改动)

`frontend/index.css` `@theme` 内覆写整套色阶,旧色相 → 新语义色:

| 旧色阶(全档 50-950) | 映射到 |
|---|---|
| indigo / violet | **绿**(主角;violet 与 indigo 并轨) |
| purple | **李紫**(Agent) |
| emerald / green | **绿**(与主角并轨,消灭第二种绿) |
| amber(+yellow 如有引用) | **赭** |
| red / rose | **砖红** |
| blue / sky | **钢蓝** |

每个色阶按新主色重生成 50-950 明度档(50/100=soft 底,200/300=描边,500/600/700=实色,900+=深文字),保证常用档位(50/100/300/400/500/600/700)与 mockup 视觉一致。**类名语义暂时失真**(`indigo-600` 渲染为绿)是本方案的已知代价——换取零扫描一次换血;后续新代码用语义 token(见 2.3),旧类名渐进替换不设期限。

### 2.2 基底与主题变量

- `[data-theme="light"]` 块:`--app-bg/--island/--island-2/--card/--line/--line-strong/--content*` 及 light 档 ink 阶 → 暖纸值
- `--color-accent` → 绿;`btn-tint-*` 的 light 覆写(index.css ~:170)换新色相
- dark 主题:同一批点缀色变量给暗色适配值,基底不动

### 2.3 语义 token(新代码的正道)

`@theme` 新增:`--color-ok / --color-warn / --color-danger / --color-info / --color-agent` 各带 `-soft` / `-line` 档 → 生成 `bg-ok-soft`、`text-warn`、`border-danger-line` 等 utilities。CLAUDE.md 加一行:新代码禁用旧色相类名,用语义色。

### 2.4 组件小清扫(theme 层覆盖不到的)

- **黑色 active 胶囊**(约 165 处 `bg-ink-900`/`bg-black` 上下文,实际 active 语义的是其中一部分):视图切换、分页/格式 segmented、tab 等 → 绿 active。按模块分波:项目/工作区 → 资源库+灵感库 → AI 库/聊天/设置/其余
- 个别用中性 ink 阶表达强调的部件(如 EP chip 黑底)→ 中性描边样式
- **tab/segmented vs 菜单行 active 态的实心/soft 判据**(W4 最终审定):approved mockup(`2026-07-29-project-palette-mockup.html`)里 `.tab.on{background:var(--green);color:#fff}` 是实心白字,而不是 soft 底 —— 据此拍板:**tab/segmented 视图切换器(list/grid/kanban/board/preview-code 等互斥模式按钮组)active 态 = 实心模块强调色**(`bg-indigo-600 text-white` 写法,经 `[data-module]` 变量重指向各模块主色,同 W2 的 ProjectsListView Queue/Grid);**菜单/列表行选中态(下拉行、GroupByPicker、WorkspaceSwitcher、侧栏行、文件夹树)= soft 底 + 强调文字**(`bg-[var(--accent-soft)] text-[var(--accent-text)]`)不变。前者是 mockup 直接管辖的控件形态,后者是行选中语义,二者不是同一种视觉物件,不应共用同一 idiom。

### 2.5 验收

- 双主题 e2e 截图全量刷新(现有 spec 的 screenshot 仅存档不 diff,直接重跑)
- 人工走查五大模块 × light/dark;色彩对比度抽查(绿实底白字 ≥4.5:1,soft 底深字 ≥4.5:1)

## 3. 分波

- **W1 token 换血**:@theme 色阶重映射 + 暖纸基底 + 语义 token + btn-tint 覆写。合并即全站变色(视觉主升级)
- **W2 项目模块清扫**:黑 active/EP chip/mockup 对照修(workspace/workflow/editor/projects)
- **W3 资源库 + 灵感库清扫**
- **W4 AI 库/聊天/设置/剩余 + e2e 截图刷新 + 对比度验收**

## 4. 不做

- 类名批量重命名(indigo→ok 等)——渐进,不进本期
- 品牌 logo/插画/缩略图占位色
- 字体/圆角/间距体系(本期只动颜色)

## 5. 模块主色(2026-07-29 追加拍板)

每个模块一个主色,"强调/选中/主按钮"层跟模块走;**语义色全局不变**(危险=砖红、警示=赭、成功=绿,任何模块内含义一致)。

| 模块 | 主色 |
|---|---|
| 项目(projects/workspace/workflow/editor) | 绿 #1E7A5B |
| AI 库 / Agent | 李紫 #7A5E8F |
| 灵感库 / 话题灵感 | 赭 #A87B2B |
| 资源库 / 分发 | 钢蓝 #46708E |
| 待办/分享/积分/设置等小模块 | 默认绿,不单列 |

**机制**:路由壳按当前模块打 `data-module` 属性;CSS 里 `[data-module="x"]` 局部覆写 accent 系色阶变量(indigo/violet/emerald/green 四阶),子树内全部 accent 类自动切换为模块主色 —— 零组件改动。
