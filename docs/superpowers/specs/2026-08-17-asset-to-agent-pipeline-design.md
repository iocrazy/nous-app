# 素材 → agent 处理链路升级 — 设计（2026-08-17）

> 用户实测 @ 功能后的四条反馈（截图齐全）：①@ 未处理视频 → ResourceFetch 三连
> "not available"；②@ 选择器无缩略图、信息太少；③要右键"发送给 agent"；
> ④输入框要有素材芯片。两个关键决策已拍板：**附上时自动补处理**（异步，走任务
> 中心）；右键发到**当前浮窗选中的 agent**（未选则默认 Analyze）。
> 侦察事实 21 条见分支根 `RECON.md`（工作副本，不 commit）。

## 0. 现状核心落差（侦察实证）

- ResourceFetch 只读已生成的 `resource_summaries`/`resource_transcripts`，空即
  "not available"，**没有任何一方触发处理**（RECON#1）；且 handler/resolver/prompt
  三层都拿不到 `transcript_status`，无法区分"没跑过"和"正在跑"（#1/#2/#3）。
- 触发端点幂等（重复触发 200 不重复扣分，#5）——自动触发是安全的。
- `/resources/search` 的 `thumbnail_url` 硬编码 None（#7），而 `/cover` 端点无鉴权
  可直接 `<img src>`（#8）——picker 加缩略图只差把字段填上。
- globalChatStore 已有 `chatRequest`/`pendingQuote` 两条一次性通道范式（nonce +
  consume + rAF 等挂载），右键发送需要第三条"预置附件"通道（#14/#20）。
- 右键菜单处 `item.resource` 已含全部状态字段（#15）——无需额外请求即可判断缺啥。

## 1. 改动清单

### F1 — 附上未处理素材时自动补处理（决策①）

**前端（触发方）**，两个入口共用一个 helper `ensureResourceProcessed(resource)`：
- 判定：video/audio 且 `transcript_status` 不是 completed → 调
  `triggerTranscriptionByResource`（幂等，#5）；`transcript_status=completed` 而
  `summary_status` 不是 completed → 调 `triggerSummaryByResource`。
  转录完成后（任务终态监听）自动补摘要——链式"缺什么补什么"。
- 入口 A：@ 选择器选中时（picker 结果需带状态字段，见 F2 后端）。
- 入口 B：右键发送时（状态字段现成，#15）。
- 任务进度：照 RECON#19 既有范式（task_type + resource_id + created_at 倒序）从
  TaskManager 读；⚠️ **必须做 optional 化**（照 `useOptionalToast` 的形状加
  `useOptionalTaskManager`）——浮窗也挂在 ScriptEditor 路由，直接 `useTaskManager()`
  会 throw（#18）。Provider 缺席时芯片不显示进度（优雅降级），不 crash。
- 顺手修 #4：`aiService.ts` 触发函数的返回类型与后端真实返回体对齐（后端不返 task_id）。

**后端（告知方）**——让 agent 当轮说人话而不是三连失败：
- `resolve_resource_refs` meta 增补 `transcript_status`/`summary_status`（#2）。
- `<available_resources>` 渲染带上状态（#3），如 `status=transcript:processing`。
- ResourceFetch video/audio 分支查不到内容时，读 `resources` 状态列区分返回：
  `processing` → `"transcript is being generated; ask the user to retry shortly"`；
  `none/failed` → 维持现文案。**不在工具里触发处理**（决策①选的是前端触发；
  agent 自主触发消费型任务涉及权限设计，明确范围外）。

### F2 — @ 选择器富化（反馈②）

**后端** `/resources/search`：
- repo SELECT 增补 `thumbnail_path`/`cover_image_path`/`media_id`/
  `transcript_status`/`summary_status`（#7）；
- router 按 `buildThumbnailSrc` 同款判定（thumbnail_path || cover_image_path ||
  media_id || image mime）填 `thumbnail_url`（相对路径 `/api/v1/resources/{id}/cover`），
  响应增补两个状态字段；
- ⚠️ 该接口面向用户但只返回自己可见的资源，缩略图走无鉴权 cover 端点（#8 已有约定），
  不新增泄漏面；不加凭据类字段。

**前端** `ResourcePickerSuggestion`：
- 行左侧 `w-7 h-7` 占位换成 `<img src={thumbnail_url}>`（无则回退 lucide 图标）；
- 行内加处理状态小徽标（未转录的视频给 warn 色小点 + tooltip，语义色 token）；
- 上限 20 → 50（改 `useResourceSearch.ts:35` 的 limit，后端上限本就 50，#10），
  列表容器加 max-h + 滚动（吸取 AgentSelector 裁剪教训）。

### F3 — 右键"Send to Agent"（反馈③，决策②）

- `globalChatStore` 新增第三条一次性通道（照 `PendingQuote` 形状，#14）：
  `pendingResource { resourceId, name, kind, mime, scope, nonce }` +
  `sendResourceToChat()`（set open:true + nonce 递增）+ `consumePendingResource()`。
- `useContextMenuItems` file 分支加 `Send to Agent`（lucide `Bot` 图标，放在
  Generate Prompt 附近，无权限门控——只是发进聊天）；onClick：
  `ensureResourceProcessed(item.resource)` + `sendResourceToChat({...})`。
- `AIChatPanel` 消费：照 pendingQuote 的 rAF 重试范式（#20）等 composer 挂载后
  `insertResourceRef(item)`；**不切换 agent**（用当前选中的；`selectedAgentSlug`
  为空时先 `setSelectedAgentSlug('analyze')` 再插入）。
  ⚠️ 消费处还要处理 `composerDisabled`（无 activeSessionId 时 ChatInput disabled，
  AIChatPanel.tsx:395-397）——沿用 pendingQuote 已趟过的同一条路径。

### F4 — 素材芯片升级（反馈④）

- `ResourceChipView`（tiptap 内联芯片）：加缩略图 `<img>`（cover URL，无则图标）；
  indigo 旧色相 → 语义色 token（CLAUDE.md K1 纪律，#12 点名）；
- 芯片显示处理状态：`processing` 时 warn 色小 spinner/点 + tooltip
  （数据来自 F1 的 optional task 监听 + 插入时快照的状态）。
- 历史气泡仍不渲染 resource_ref（#13 现状保留，记 backlog——改它涉及消息体样式面）。

## 2. 范围外

- agent 自主触发处理任务（权限设计，另立项）。
- picker 键盘上下键选择（#11 既有缺口）、搜索 notes/tags（#21）、audio/pdf tabs（#9）、
  frames/pdf 模式实现——全记 backlog。
- 历史气泡渲染 resource_ref 芯片（#13）。
- summarize 无-transcript 分支不建 task 行（#6）——F1 的链式触发绕开了该分支
  （总是先确保 transcript 再调 summarize），不改后端该分支。
- 转录/摘要端点返回 task_id（依赖面大；F1 用 #19 范式不需要它）。

## 3. 验证

- 后端：resolver meta 带状态、available_resources 渲染带状态、ResourceFetch
  processing 文案分支、search 响应 thumbnail_url/状态字段（含"不加凭据列"tripwire）。
- 前端：picker 行显示缩略图与状态徽标；`ensureResourceProcessed` 各分支
  （未转录→触发转录；转录完→触发摘要；全齐→不触发；幂等重复调用安全）；
  store 三通道互不干扰；右键项插入芯片；芯片状态渲染；useOptionalTaskManager
  在无 Provider 时不 throw。
- 生产验收（可证伪）：右键一个**未处理**视频发送给 Analyze →
  ①任务中心出现转录任务；②芯片显示处理中；③agent 当轮回复"正在处理"而非三连
  not available；④处理完成后重问 → agent 能读到转录/摘要内容。
