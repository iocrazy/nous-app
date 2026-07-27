# 画布打磨 Batch 2 — P2 剩余 + P3 批量(2026-07-12)

> 上承 `2026-07-11-canvas-polish-checklist.md`。P0×7 + P1×13 + P2-1/P2-6 已全部上线
> (#1225→#1250,prod v0.25.238)。本批 = P2-3 / P2-7 / P2-8 / P2-9 + P3 十项(合并为
> 2 个批量 PR),共 **6 个 PR**。
> 工作目录:`.worktrees/feature-canvas-phase0-entry`(或按项新开 worktree)。
> 参考源:`/Volumes/program/project-code/github-repos/Infinite-Canvas`
> (js = `static/js/smart-canvas.js`,css = `static/css/smart-canvas.css`)。

## 每项 PR 统一节奏(验收标准模板)

1. 新分支 from `origin/master`(先 `git fetch origin` 无 refspec,防 stale tracking ref)
2. TDD:先写红测试(vitest 从 `frontend/` 跑),再实现转绿
3. `cd frontend && npm run lint`(rules-of-hooks 是 CI 阻塞项)
4. 相关 e2e 本地跑(playwright,先 kill 占用 4173 的进程;e2e **不在 CI**,必须本地跑)
5. 新 UI 必须 probe 模式真机截图目测(e2e stub,`frontend/e2e-artifacts/probe-*.png`)
6. 临 push 前 **重新核 master 的 package.json 版本取下一空位** bump(并行 session 撞车前科 #591;当前 master=0.25.238,以 push 时实况为准)
7. push → PR → CI 绿 → squash merge(merge 与 CI 等待**分两次调用**或 `--auto`,勿同批)

---

## 一、执行顺序与理由

| 序 | 项 | 规模 | 理由 |
|---|---|---|---|
| 1 | P2-3 图片节点悬浮工具条(小版) | M | 最显眼的操作效率提升;纯前端、零依赖;顺手提取 download util 给 P2-7 复用 |
| 2 | P2-9 Composer 锁粒度按节点 | M | 解锁"多节点并行出图"工作流,价值高;纯前端状态重构,但要回归 P0-4 Stop 语义,放在有整块注意力时做 |
| 3 | P2-8 视频灯箱逐帧 | M | 纯前端、内聚在 OutputLightbox 一个文件;与 1/2 无耦合 |
| 4 | P2-7 下载全部 zip 化 | M | **唯一后端项**:merge 后走 CI→ACR→watchtower 部署链,前端带 fallback 可单 PR 合;放中后段,部署观察期不阻塞前端批次 |
| 5 | P3-A 纯 CSS/token 批量 | S | 随时可插队;放在 P2 之后避免与 P2-3(新增 island 样式)/P2-8(灯箱)在同文件打架 |
| 6 | P3-B 有逻辑的组件打磨批量 | M | 触碰 PromptNodeView/LoopNodeView/TimelineNodeView/OutputLightbox 四处,放最后吃掉前面所有 rebase |

P2-3 / P2-9 / P2-8 三项互相独立,理论上可并行 worktree,但**版本位撞车风险**(读 master 版本再 bump 的前科)+ 都动 `smart/` 目录,建议串行小步快跑(每项 ≤1 天,总 ~4-5 天)。

---

## 二、逐项方案

### PR-1 · P2-3 图片节点悬浮工具条(小版)— M

**现状**:OutputNodeView 的操作全在节点头 chips(Rerun/Expand/Mask/Split,
`frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx:384-428`),预览要点图等
250ms(`queueLightbox` :111-121,`LIGHTBOX_CLICK_DELAY_MS=250` :35),下载只能进灯箱。

**Infinite 参考**:`smartNodeToolbarHtml`(js:7141-7166)七键浮条;CSS
`.smart-node-floating-menu`(css:330-338):节点上方 `top:-38px`,玻璃壳
(radius 12/blur 18/border line),**选中才显**(`.image-node.selected .smart-node-floating-menu{opacity:1}`
css:331),多选隐藏(css:332),按钮 24px 高/10px 字/hover translateY(-1px)。

**我们做小版三键**:Preview(Eye,立即开灯箱——绕过 250ms 延时:`cancelQueuedLightbox()`
+ 直接 `setLightboxIndex(0)`)/ Download(下载当前首图)/ Rerun(复用 `onRegenerate`
:131-133,`canRegenerate` 时才显)。显隐建议 **selected || hover** 双通道(Infinite 是
selected-only;hover 提高可发现性,e2e 断言用 selected 稳定):根 div 加 `group`,
工具条 `opacity-0 group-hover:opacity-100`,selected 时强制显示。

**改动文件**:
- 新 `frontend/features/canvas-core/smart/downloadMedia.ts` — 从 OutputLightbox 提取
  `downloadUrl`/`downloadName`(OutputLightbox.tsx:40-64,fetch→blob→anchor,注释里的
  cross-origin 原因保留)+ 单测
- 新 `frontend/features/canvas-core/smart/nodes/OutputNodeToolbar.tsx` — 浮条组件
  (`canvas-island` 壳,absolute `-top-10 left-1/2 -translate-x-1/2`,按钮全部 `nodrag`)
- `OutputNodeView.tsx` — 根 div(:369-373)加 `group relative`;挂工具条(仅
  `lightboxItems.length>0` 时);OutputLightbox.tsx 改 import downloadMedia
- `frontend/index.css` — 若需浮条渐显过渡(对齐 css:330 `.14s ease`),复用 `--canvas-ease`

**测试**:
- 单测:`OutputNodeToolbar.test.tsx` — 三键渲染/无媒体不渲染/Preview 立即开灯箱(fake
  timers 断言不等 250ms)/Rerun 无 promptId 时隐藏;`downloadMedia.test.ts` 提取后原测试搬家
- e2e:`canvas-media.spec.ts` 加 case(选中输出节点→浮条可见→Preview 开灯箱);probe 截图目测

**风险与坑**:
- 浮条溢出节点顶部,**未选中 hover 时可能被上方相邻节点盖住**(RF 节点 z-index 选中才抬)
  ——与 Infinite 同款限制(它 z-index:18 也只保证节点内),可接受,不为此上 portal
- **千万别用 position:fixed**(portal/fixed 塌缩已知坑:RF 祖先 transform 让 fixed 塌进节点,
  见 OutputLightbox.tsx:271-274 注释);absolute 相对节点即可
- 按钮漏 `nodrag` → 点击变拖节点

### PR-2 · P2-9 Composer running 锁粒度按节点 — M

**现状**:`CanvasComposer.tsx:93` 单一 `running` boolean;`doRunIds`(:209-226)
`if (running) return` 整库拒绝;running 期间 Run/Cascade 整体换成 Stop(:486-510)。
一次 Run 跑 5 分钟视频时整个 composer 冻死。而 Rerun(regenStore,`regenerate.ts:79-99`
`regenKey(canvasId, lockId)`)/Loop(loopRunStore)/Timeline(timelineRunStore)早就是
per-node 锁——composer 是唯一的全局锁尾巴。

**方案**(对齐 Infinite 按节点粒度,复用 regenStore 的 per-key 范式):
- `running: boolean` → 批次表:`batches: Map<batchId, { ids: string[]; stop: { requested: boolean } }>`
  (local state + ref 即可,不必新 zustand store;`stopRequestedRef` 从单 ref 变 per-batch)
- `doRunIds`:**过滤掉已在任何在跑批次里的 promptId**(以及 `run_status` 为 queued/running
  的节点,防同节点双派发);剩余为空才 return;每批独立 `shouldStop`
- 按钮区:Stop 与 Run/Cascade **并存**——有批次在跑时显示 Stop(停**所有**批次,per-node
  stop 二期),Run 对"选中且未在跑"的 prompt 保持可用;Cascade Run 在有 cascade 批次
  在跑时禁用(拓扑序语义冲突),选中 Run 不受限
- `runner` memo(:101-134)的 `shouldStop: () => stopRequestedRef.current` 改为从
  ctx 对应批次读(把 batch stop ref 传进 `runPrompts` options,`runner.ts:145-161`
  已支持 per-call `shouldStop`,不用改 runner)

**改动文件**:
- `CanvasComposer.tsx`(核心)
- `CanvasComposer.test.tsx` / 可能 `CanvasComposer.workflow.test.tsx` — 更新旧"全局锁"断言

**测试**(先红):A 在跑时 Run B 成功派发;同节点重复 Run 被过滤;Stop 停掉两个在跑批次
且未派发的 prompt 状态不动(P0-4 语义回归);Stopping… label 行为。

**风险与坑**:
- **P0-4 回归面**:Stop 的"未派发不动、在飞归 idle"语义必须逐条保住(`runner.ts:122-128`
  stopped→idle 路径有单测,composer 侧要补批次版)
- `handlers` 对象(:192-207)每 render 重建——现状如此,别顺手"优化"成 memo 引入
  stale closure(hooks 守门是 CI 阻塞项)
- 同一 prompt 的 gen_slot 并发写:靠"已在跑节点过滤"从源头防,不要去 genSlots 加锁

### PR-3 · P2-8 视频灯箱逐帧 + 导帧 — M

**现状**:`OutputLightbox.tsx:369-380` 视频 = `controls autoPlay` 死播放器;
`zoomEnabled = kind === 'image'`(:94)→ 滚轮对视频完全无操作;方向键只切图(:242-249)。

**Infinite 参考**:`videoFrameStep`(js:9543-9546,`1/clamp(fps||30,1,120)`)、
`seekPreviewVideoFrames`(js:9548-9557,先 `pause()` 再 `currentTime ± step`,上限
`duration - step/2`)、滚轮绑定(js:16686-16691,preview 模式滚轮先试帧 seek)、
方向键(js:15925-15936,视频优先帧 seek,否则切图)、`exportVideoFrame`
(js:9577-9627:seek 到目标帧 → canvas.drawImage → toBlob PNG;first=0,
last=duration-step/2,导完 first/last 后**归位原时间**)。Infinite 预览视频
**不自动播**(smart-canvas.html:375 无 autoplay)。

**实现要点**:
- 加 `videoRef`;滚轮:`applyWheelZoom` 里 `kind==='video'` 分支改为帧 seek(fps 元数据
  我们没有 → 固定 30fps 步长,与 Infinite 默认一致);方向键:视频时帧 seek 替代 goto
- **autoplay 建议改为不自动播**(对齐 Infinite):逐帧 seek 与自动播放互斥(边播边滚会打架),
  且节点内联 `<video muted preload="metadata">`(OutputNodeView:489-496)已承担"看一眼"
  职责。**此项可翻**——若用户更爱点开即播,只保留"滚轮/方向键时自动 pause"即可
- 导帧三键(First / Current / Last Frame)进灯箱工具栏(video 时替代 Compare 位):
  seek → `canvas.drawImage` → `toBlob('image/png')` → 复用 `downloadMedia.ts` anchor 下载,
  文件名 `{name}-first|current|last-frame.png`(js:9590 同款后缀)。
  **v1 只做下载到本地**;Infinite 是上传后 spawn 画布图片节点,我们 spawn 节点需要
  resource 上传 scope 管道(OutputNodeView 没有 teamId),blob URL 又不可持久化——挂账二期
- `<video>` 加 `crossOrigin="anonymous"`(Infinite html:375 同款;canvas 导帧防 taint)

**改动文件**:
- `OutputLightbox.tsx`(主体);`downloadMedia.ts`(blob 下载复用)
- 新测试 `OutputLightbox.video.test.tsx`

**测试**:
- 单测(jsdom):滚轮 → `video.paused===true` 且 `currentTime` ±1/30;方向键同;
  export current → toBlob 被调、anchor download 属性正确。**jsdom 没有 canvas/video 实现**:
  mock `HTMLCanvasElement.getContext/toBlob`、defineProperty `videoWidth/duration`
- e2e:`canvas-media.spec.ts` 或 probe——真视频滚轮逐帧目测 + 导帧文件落盘断言

**风险与坑**:
- **canvas taint**:prod 媒体走 Vercel rewrite `/api/*` 同源,result_url 是相对路径
  (`canvas_generation.py:153-156`)没问题;但绝对跨域 URL 会让 toBlob 抛 SecurityError
  → try/catch 显示 "Export failed"(靠 crossOrigin+服务端 CORS 才能救,不强求)
- 滚轮必须继续走**非 passive 手动绑定**(:146-151 已有,原因注释保留),别改回 onWheel
- `seeked` 等待要带超时(Infinite `waitForVideoEvent` 2200ms,js:9558-9575),防止坏源挂死
- 灯箱根已有 `nowheel`(:284),帧 seek 不会漏给 RF 缩放——别动这个 class

### PR-4 · P2-7 下载全部 zip 化(后端 + 前端)— M

**现状**:`OutputLightbox.tsx:251-267` `doDownload()` 无参时 for 循环逐张 fetch→blob→click,
N 张弹 N 个下载。

**Infinite 参考**:服务端 `POST /api/canvas-assets/download`(main.py:13595-13650):
items[{url,name}] → 逐个解析为本地文件/远程 fetch → `zipfile.ZipFile(BytesIO)` 打包
→ 重名加 `-2` 后缀 → `Content-Disposition: attachment; filename*=UTF-8''…`;
客户端 `zipDownloadImageItems`(js:6586-6612)fetch→blob→anchor。

**我们的设计**(收紧安全面,**不做任意 URL 远程 fetch——防 SSRF**):
- 后端:`POST /api/v1/canvases/assets/zip`,body `{filename, items:[{url,name}]}`,
  AuthDep 必带。只接受**白名单相对路径**两种:
  `/api/v1/generated-media/{id}/(file|stream|cover)`(gen 结果,`canvas_generation.py:153`
  的 result_url 形态)和 `/api/v1/resources/{id}/file`(crop 派生资源,
  OutputNodeView `buildPreviewUrl` :53-63 形态);其余(绝对 URL/外部域)直接 400
- 逐 id 做 scope 校验(generated-media 走 `_resolve_personal_team_id` +
  `GeneratedMediaRepository`,同 `generated_media_router.py` DELETE 的口径;resources 走
  现有资源访问检查),文件经 `resolve_media_source`/存储层读取,`zipfile` 打包,
  上限 ≤64 items,重名 `-2` 递增(照抄 Infinite main.py:13633-13639 逻辑)
- 路由放 `canvases_router.py` **静态段先于动态段**注册(:100-101 的既有注释约定:
  静态 path 必须先注册,否则被 `/canvases/{canvas_id}` 吞)
- 前端:`doDownload()` 多张分支改为 POST zip → blob → 单 anchor;**响应非 2xx 时
  fallback 回现有逐张循环**(部署链偏斜窗口:前端 Vercel 秒级上线、后端 ACR+watchtower
  分钟级,fallback 保证窗口内不坏)
- 新 `frontend/features/canvas-core/services/` 里加 `downloadZip(...)`(带 auth header,
  参考 parserService `getAuthHeaders` 模式)

**改动文件**:
- `backend/app/api/canvases_router.py`(+schema 放 `backend/app/schemas/canvas.py`)
- 后端测试 `backend/tests/`(直接调函数式单测:合法两种 URL/外部 URL 400/越权 404/重名后缀)
- `frontend/features/canvas-core/smart/nodes/OutputLightbox.tsx` + service + 单测(fetch mock:
  成功走 zip 单次下载;500 时 fallback 逐张)

**测试**:后端 pytest(uv run pytest,先红)+ 前端 vitest;e2e 可选(真下载断言 zip 落盘)。

**风险与坑**:
- **部署链**:merge 后 CI build→ACR→watchtower 自动(纯 `backend/**` 改动,**不动 compose,
  无需 NAS 手工操作**);但要等后端上线后真机验一次 zip(前端 fallback 兜窗口期)
- push 后按惯例等 ACR build 完;后端 lint gate:对改动 .py 跑 black+isort+flake8 再 push
- 别学 Infinite 的远程 URL fetch(它是单机自用;我们多租户必须白名单+scope 校验)
- 大视频打 zip 内存峰值:v1 用 BytesIO + ≤64 上限可接受;流式 zip 挂账

### PR-5 · P3-A 纯 CSS/token 批量 — S

全部集中在 `frontend/index.css` 的 `--canvas-*` 命名空间(:706-1130)+ 一处 tsx 字号:

1. **框选框**:RF 默认蓝框 → 加 `.mh-canvas .react-flow__selection` 与
   `.mh-canvas .react-flow__nodesselection-rect`:`border:1px solid var(--canvas-strong);
   background:color-mix(in srgb, var(--canvas-strong) 8%, transparent); border-radius:10px`
   (Infinite `.selection-box` css:1186-1187,dark 换 10% 白底)
2. **阴影 α**:`--canvas-shadow-island` dark 0.45→0.28(:729);`.mh-node` dark
   0.22→0.28(:779)、light 0.10→0.08(:782)
3. **边宽与流动**:`.mh-canvas .react-flow__edge-path` stroke-width 2.5→1.6(:1021);
   流动动画对齐 Infinite `dash-flow .9s / -13`(css:458,463):新建 mh-scoped keyframes
   (**别直接改 :882 的 `canvas-edge-flow`——先 grep `.canvas-processing-edge__flow`
   确认是否被 storyboard/classic 共用**,共用就新名字);`.mh-edge-active` 的 2.5(:1118)
   降到 2(运行态可略粗于静态)
4. **mh-chip 字号**:10px 强制大写(:805-808)→ 11px、去 text-transform/letter-spacing
   (Infinite select-lite 11px 非大写);prompt 正文 `text-sm`(14px)→ `text-[13px]`
   (PromptNodeView textarea,唯一 tsx 改动)
5. **入场动画**:新 `.mh-pop-in`(opacity 0→1 + translateY(4px)→0,.16s var(--canvas-ease)),
   挂到 DragCreateMenu / WorkflowLibraryPicker / CanvasMentionPicker 根元素

**测试**:CSS 无单测;`canvas-visual.spec.ts` 截图基线**必然要更新**(这就是本 PR 的
e2e 验收);probe 双主题截图逐项目测(尤其 chip 去大写后的节点头排版)。

**风险与坑**:
- mh-chip 改字号影响**所有** smart 节点头(Prompt/Output/Timeline/Loop 的 select 也是
  mh-chip)——42px 头高够不够、select option 溢出,截图逐个节点看
- keyframes 重命名时 grep 全库引用,别断 storyboard 的流动动画
- 纯样式 PR,**严禁夹带逻辑改动**(refactor/feature 分离纪律)

### PR-6 · P3-B 有逻辑的组件打磨批量 — M

1. **Loop 节点头一致性**:`LoopNodeView.tsx:43-45` 自制头(12px semibold/py-1.5/
   text-slate-500 硬编码)→ 标准 `mh-node-head` + `mh-node-title`(11px/800/42px,
   对齐其他四种节点;顺带消灭 zinc 时代 `dark:text-slate-400` 写法——P0-1 同族残留)
2. **ref/mention chip token 化**:`PromptNodeView.tsx:304-309` ref chip
   `bg-indigo-900/40 text-indigo-200` 硬编码(亮色主题下发暗)→
   `rgb(var(--canvas-accent-rgb) / 0.15)` 系;:168-177 elapsed pill 的 indigo 同改;
   grep `indigo` 横扫 `smart/nodes/`(CanvasMentionPicker.tsx 大概率也有)
3. **Retry 整条红面板**:`PromptNodeView.tsx:132-143` 失败时头部一个小 Retry 胶囊 →
   Infinite 式整条恢复面板(节点体底部整宽 rose 面板:错误文案 + Retry 按钮;样式参照
   已 ship 的 RecoverCell,OutputNodeView.tsx:657-682 amber 面板同构)
4. **灯箱 meta 行**:OutputLightbox 工具栏左侧(:291-301)只有计数+分辨率 →
   加 prompt 前 60 字(Infinite `updatePreviewMetaHint` js:9322-9326:
   `分辨率 · prompt片段` 用 `·` 连接)。管道:OutputNodeView 已有
   `promptIdForOutput(id)`(:614)→ store 查 prompt node 的 `data.body` → 新
   `meta?: string` prop 传入
5. **时间线内 Delete 删段**:现在节点内按 Delete 走 RF 全局删整个节点。
   `TimelineNodeView.tsx` 根 div 加 onKeyDown:`Delete/Backspace` 且有 activeId 且
   target 非 input/textarea → `removeSegment`(:269 已有逻辑)+ `stopPropagation()`
   + `preventDefault()`

**测试**:每项一个先红单测(LoopNodeView 头断言 class;chip 断言无 indigo;Retry 面板
role=alert;灯箱 meta 文本;Timeline keydown 删段且不冒泡)。e2e:`canvas-timeline.spec.ts`
加"节点内 Delete 只删段不删节点"case(**关键**:RF 的 delete 监听在 document 层,
React stopPropagation 理论上拦得住,但必须 e2e 实证,jsdom 不可信);probe 截图目测
Loop 头 + 亮暗双主题 chip。

---

## 三、P3 分组方案小结

- **PR-5(P3-A)= 纯 CSS/token**:框选框/阴影 α/边宽+流动/chip 字号/入场动画 —— 一个
  文件为主、一次截图基线更新、零逻辑
- **PR-6(P3-B)= 有逻辑**:Loop 头/chip token 化/Retry 面板/灯箱 meta/时间线 Delete ——
  五个组件各一小刀,每刀有单测
- **合并边(同源到分组多成员合一条)+ 整组跨节点灯箱翻页:继续观望,本批不做**。
  理由:组节点真实进 RF 才刚修好(#1229),使用率数据不足;RF 下做聚合边要自定义
  edge 合流层(多 target 单 path + 命中测试),投入 L 级;等组节点有真实使用信号再评

---

## 四、本批不做(防 scope drift)

- 超分、5 引擎、时间线音频轨 + guide_strength、组节点多图归拢(裁剪勿翻案)
- P2-2 就地生成 composer(defer,动 composer 架构)
- P2-4 灯箱=编辑器七模式合并(维持现状)
- P2-5 灯箱开启手势翻转(维持单击开灯箱/双击裁剪,250ms 延时设计不动;
  P2-3 的 Preview 键已提供"零延时开灯箱"旁路,进一步降低翻案压力)
- P2-8 导帧 spawn 画布节点(需上传 scope 管道,v1 只下载到本地,挂账)
- zip 流式打包 / 远程 URL 打包(v1 BytesIO + 白名单)
- Composer per-node 单独 Stop 按钮(P2-9 v1 Stop=停所有批次)
- 时间线播放头 / WebAudio / 单段重跑(P2-1 二期挂账,非本批)
- Nous 更强项全部不动:对齐辅助线/auto-pan/Task Center/重做/mod+K/@mention 导航/
  Retry=真重跑/灯箱计数

## 五、通用坑速查

- **RF 手势占用**:新交互先对照 checklist 尾部小抄;节点内可点元素必 `nodrag`,
  滚轮区必 `nowheel`(灯箱根已有)
- **portal/fixed 塌缩**:RF 祖先 transform 使 fixed 以节点为 containing block——节点内
  浮层用 absolute,全屏层必须 portal 到 body(OutputLightbox.tsx:271-274 前科注释)
- **并行 session 版本撞车**:push 前重读 origin/master 的 package.json 取下一空位
- **e2e 不在 CI**:每 PR 本地跑对应 spec;新 UI probe 截图,双主题都看(P0-1 亮色前科)
- **CI billing 坑**:runner 不分配时按 playbook 切 public 跑完(P0 批次复发过)
- **merge 纪律**:CI 等待与 merge 分两次调用或 `--auto`;每 PR ship 后立即合,不攒批
