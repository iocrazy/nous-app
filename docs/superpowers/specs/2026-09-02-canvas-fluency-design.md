# 画布流畅感 — 设计（Wave 1 性能 + Wave 2 预览层）

**日期**：2026-09-02 · **状态**：用户已授权「你来定」· **范围**：`frontend/features/canvas-core`、`canvas-kit`、`backend/app/api/generated_media_router.py`、`app/services/library/`

## 1. 起因与证据

用户：「画布还没有 IC 流畅」。做法：从 IC `smart-canvas.js` 抽**行为规格**（非代码，IC 非商用许可），审计 nous 同维度，控制器逐条独立核实，并真栈实测。

已核实的主因（按感知影响）：

| # | nous 现状（file:line） | 实测 / 后果 |
|---|---|---|
| 1 | `/cover` 直接 `_serve_media_row` 吐原文件；生成图**从不**生成缩略图（`ThumbnailService` 320px 只服务上传资源，`register_generated_media` 不触发） | 用户 24 节点画布：**12 图 15 MB，均 1.3 MB / 1672×941**；解码 ≈75 MB 位图，每次缩放重传 GPU |
| 2 | `.react-flow__viewport { transition: transform .05s }`（`index.css:1551`）；受控 `viewport=`（`CanvasSurface.tsx:616`） | 每帧拖影 + 每帧两次 React commit |
| 3 | `panOnScroll`/`zoomOnScroll` 未传（`canvas-kit/CanvasEngine.tsx:753-800`） | 触控板双指滚动 = 缩放 |
| 4 | `toReactFlowNodes(nodes).map(spread)` 每变更整数组重建（`CanvasSurface.tsx:187-194`）；节点视图订阅整个 `nodes` 并逐节点重算全图 map（`PromptNodeView.tsx:224-255`，:226 重复订阅） | 拖一个节点全部重渲染 |
| 5 | 每卡 `backdrop-filter: blur(18px)` + 56px 阴影（`index.css:1218-1226`）；工具条常驻挂载再叠一层 blur（`OutputNodeToolbar.tsx:86-90`） | 合成器每帧重模糊所有可见卡 |
| 6 | `setNodesDragTick` 每 tick `markDirty()`；`flushViewportDirty` 每帧 `markDirty()`（`canvasCoreStore.ts:956-985, 474-483`） | 交互期间计时器/store 写风暴 |
| 7 | 占位格与 `<img>` 无固有尺寸、无 `loading`/`decoding`（`OutputNodeView.tsx:620-677`） | 图落地布局跳 + dimensions 重渲染 |

IC 对应的行为：服务端预览为默认 src、近视口且 zoom≥0.86 才换原图且换前 `decode()`；指数级 zoom-to-cursor 单 transform 无 transition；拖拽绕过渲染器、拖拽期 body class 关 hover/transition；按请求比例定形的骨架格。**IC 自己也没有触控板平移**——第 3 条我们能做得比它好。

## 2. 目标 / 非目标

**目标**
1. 画布节点**永远不加载原图**：生成图有服务端预览层，节点只取预览；原图只给灯箱/编辑器/画笔。
2. 平移缩放跟手：无 transition；触控板双指 = 平移，捏合/⌘+滚轮 = 缩放。
3. 拖一个节点不重渲染其它节点；拖拽/平移期间不做模糊与阴影。
4. 交互期间不写 store 不臂计时器；只在 dragEnd / moveEnd 落一次。
5. 图落地零布局跳动：占位与图片按已知 `gen.ratio` 定固有尺寸。
6. 可量化验收：同一张画布前后对比**图片总载荷**与**拖拽期间节点重渲染次数**。

**非目标**
- Wave 3 交互流（`Z` 总览飞入、宽容连线、节点右键菜单、composer 锚定）——另立。
- 关闭 `onlyRenderVisibleElements`——预览层落地后再按大画布实测决定，本期不动。
- 视频封面（`/cover` 对视频今天就是 404，不变）。
- 历史生成图**不批量回填**：预览按需生成（首次请求）并写回，自愈。

## 3. 设计

### 3.1 预览层（后端）
- 预览键约定：原图键 `sb://<bucket>/<key>` → 预览 `sb://<bucket>/<key>.preview.webp`（同桶同前缀，靠命名派生，**不加表不加列**）。
- 规格：最长边 **1024px**，WebP `quality=82, method=4`（Pillow，容器已验 `features.check('webp') == True`）。1024 足够画布节点在 2× 屏上任何常用缩放；灯箱/编辑器不用它。
- `GET /{id}/cover`：先 `exists(preview_key)` → 有则吐预览；无则读原图 → 生成 → `put_bytes` 写回 → 吐预览。**任何一步失败都回退吐原图**（今天的行为），只 log 不 500。并发首次请求各自生成、幂等覆盖，可接受。
- `GET /{id}/cover?full=1`：吐原图。**安全姿态与今天完全一致**（`/cover` 今天就是无鉴权吐原图；带鉴权的 `/file` 不动）。
- 文件系统后端（无对象存储的 dev）：预览写到派生目录（沿 `derived_paths` 习惯），同样按需。

### 3.2 前端取图
- `mediaSrc(url)` 不变（节点用预览）；新增 `fullResSrc(url)` = `mediaSrc(url) + '?full=1'`（仅对 `/cover` URL 生效，其它原样）。
- 消费方切换：`OutputLightbox`（含 compare）、`UnifiedImageEditor`/`PaintCanvas` 的底图、`mediaEditBridge` 导出 → `fullResSrc`。节点、缩略、`@` 提及网格 → 保持 `mediaSrc`。
- `<img>`：`loading="lazy" decoding="async"`；容器按 `gen.ratio` 设 `aspect-ratio`（占位与真图同一个盒），无比例时回退 `1 / 1`。

### 3.3 平移缩放
- 删除 `.react-flow__viewport` 的 transition。
- `CanvasEngine`：`panOnScroll` `zoomOnPinch` `zoomOnScroll={false}`（RF：滚轮平移，⌘/Ctrl+滚轮与捏合缩放）；`panOnDrag={[1]}` 保留中键、左键留给选择/拖节点——**保留现状 `[0,1]`**，本期不改左键语义。
- 受控 viewport 改为**非受控 + `onMoveEnd` 持久化**：`defaultViewport` 来自 store，`onMove` 不再写 store；`TopNodeBar` 等只读 viewport 的消费方改订 `useViewport()`（RF hook）或 moveEnd 快照。

### 3.4 渲染成本
- `toReactFlowNodes`：按节点对象引用缓存（`WeakMap<CanvasNode, RFNode>`），未变的节点返回**同一引用**；`selected` 变化只重建受影响节点。
- 节点视图 `React.memo`（registry 处统一包）；`PromptNodeView` 去掉重复订阅（:226），全图派生（`resolveSourceUrls`/`isChainTail`）改为按 `promptId` 的选择器 + `useMemo` 依赖（`connections` 与相关节点 id 集合），不再每帧对整图建 map。
- 拖拽/平移期间：`document.body.classList.toggle('mh-canvas-interacting')`（onNodeDragStart/Stop、onMoveStart/End）；CSS 下 `.mh-canvas-interacting .canvas-node { backdrop-filter: none; box-shadow: var(--mh-node-shadow-flat); transition: none }`，hover 工具条不显示。
- 工具条按需挂载：仅 `pinned || hovered` 时渲染（hover 状态由节点根元素 `onMouseEnter/Leave` 维护），不再 opacity 隐藏常驻。

### 3.5 自动保存
- `setNodesDragTick`：只 `set({nodes})`，**不** `markDirty`；drag end 的 `setNodes` 负责 dirty。
- 视口：只在 `onMoveEnd` 写一次 store + dirty；删除 `flushViewportDirty` 的每帧调用。

## 4. 验收（可证伪）
1. **载荷**：同一张画布（`337004651010097`，12 图）`/cover` 总字节 15 MB → **< 2.5 MB**；预览 `Content-Type: image/webp`，最长边 ≤1024；`?full=1` 仍为原 PNG 字节数不变。
2. **重渲染**：vitest 用 render-count spy 断言：拖动一个节点 N 个 tick，其它节点视图渲染次数为 0。
3. **手感**：`.react-flow__viewport` 无 `transition`（CSS 断言）；`CanvasEngine` 传 `panOnScroll`/`zoomOnPinch`（props 断言）。
4. **交互期不写**：拖拽 10 tick / 平移 10 帧，`revision` 不变，`scheduleSave` 调用 0 次；结束后各恰好 1 次。
5. **零跳动**：占位格与落地图片同一 `aspect-ratio`；测试断言容器样式来自 `gen.ratio`。
6. **回退可证伪**：对象存储 `put_bytes` 抛错时 `/cover` 仍 200 且返回原 PNG 字节（不是 500）。
7. **真栈**：部署后对同一画布重跑载荷测量，并 `npm run e2e:prod` 走查。

## 5. 决策记录
| 决策 | 选了 | 备选 | 理由 |
|---|---|---|---|
| 预览尺寸 | 1024px WebP，节点**永不**换原图 | IC 式 zoom≥0.86 换原图 + decode 双去抖 | 1024 已覆盖画布任何缩放；省掉整套 LOD 切换与闪烁处理 |
| 预览生成时机 | 首次 `/cover` 按需 + 写回 | 注册时同步生成 / 批量回填 | 自愈、覆盖历史行、不阻塞生成路径；代价首击慢一次 |
| 原图入口 | `/cover?full=1` 无鉴权 | 灯箱走带鉴权 `/file` + blob | 与今天 `/cover` 吐原图的安全姿态**完全一致**，零鉴权改动 |
| viewport | 非受控 + moveEnd 持久化 | 保留受控但节流 | 受控每帧两次 commit 是结构性成本 |
| 视口裁剪 | 本期保留 | 关闭 | 预览落地后按大画布实测再定 |
| 触控板 | `panOnScroll`（超过 IC） | 照 IC 不做 | 用户群是 Mac 触控板 |
