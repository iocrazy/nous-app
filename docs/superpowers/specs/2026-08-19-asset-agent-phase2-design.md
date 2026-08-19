# 素材→agent 二期：帧模式 + Task Center 归因 + 步骤流程 — 设计（2026-08-19）

> 用户真机验收后的三条反馈（截图为证）：① agent 的 mode='frames' 未实现（回复"关键帧提取接口
> 暂未开放"）；② Task Center 的 agent 行看不出"哪个 agent 执行了什么"；③ 手动转录/取帧等任务
> 不像 Parse 链那样显示步骤流程圆点。
> 侦察结论（详见本次会话侦察报告，锚点已核对 origin/master）：三件都是补洞不是搭基建。

## F1 — ResourceFetch mode='frames' 真实现

**现状**：resource_fetch_tool.py:271-274 是桩（返回 error 文案），但 prompt_composer.py:659 的系统
提示词**已经对模型宣传了 frames**——模型会调、必吃错。而图片回传的全部基建已存在且只认
ResourceFetch：工具返回 `{"content":[{"type":"image_url","url":"data:...","mime":...}]}` →
agent_runner 的 image promotion（:805-838 / :1492-1505，vision_capable 时把图提升为 user 消息
image part，非 vision 时 _IMG_OMITTED_NOTE 降级）→ adapter 透传。image/* 分支 (:161-196) 就是先例。

**实现**：
1. frames 分支：定位视频本地文件（file_path 阶梯口径——真相可能在 parsed_media，参考
   file_path-ladder 经验；文件不在本地/不可 materialize → typed error "video file not
   available for frame extraction"，不猜）。
2. 取帧复用 `video_frame_extractor.seek_frame_cmd`（唯一 ffmpeg argv 构造器，确定性已被
   test_cover_frames_same_frame.py 钉住）+ 均匀采样（首尾各去 5%，同 :256-266 口径）。
3. 预算：默认 6 帧（与 chat 附件链 MAX_VIDEO_FRAMES_PER_ATTACHMENT=6 同源，抽共享常量或引用），
   预览宽度对齐 chat 附件链 extract_frames 的现行宽度；单张 JPEG 有字节上限（防 history_image_replay
   预算被撑爆——评估该重放 budget 并在 spec 实现时写明数字）。
4. 支持可选参数 frames=N（≤12，与 cover 上限同源）；tool schema mode 无 enum，无需改 schema。
5. 转录/摘要三态分支不受影响；frames 对 image 资源直接走既有 image 分支语义（或 typed 提示用
   默认 mode）。
6. 同步在工具调用内完成（秒级 ffmpeg），不建 task_tracking 任务。⚠️ 每帧提取失败要 typed error，
   禁止吞。非 vision 模型下走既有 _IMG_OMITTED_NOTE 链路（验证文案对用户可理解）。

## F2 — Task Center agent 归因

**现状**：agent 行来自 `agent_runs` 前端合并（useAgentRunTasks.ts），SELECT 没取 `agent_id`
（列 NOT NULL 就在 DB 里）；agent slug/name 需 join ai_agents；taskTypeBg 没给 'agent' 配色。

**实现**（纯前端）：
1. SELECT 增 `agent_id` + PostgREST 嵌套 `ai_agents(slug,display_name)`（FK 已存在；核实嵌套
   select 在该 anon/RLS 下可读，读不到则退化为 slug 缓存映射——以实测为准）。
2. TaskCenterRow：agent 行显示 agent 名 badge（语义色 agent token），title 仍为 input_summary，
   subtitle 完成态 output_summary（现状保留）。"哪个 agent 执行了什么" = badge(agent 名) +
   title(用户请求) + subtitle(结果摘要)。
3. taskTypeBg 给 'agent' 配色（agent 语义 token）。

## F3 — 手动任务的步骤流程（flow_id 补全）

**现状**："N/N steps" 圆点是同 flow_id 的多行 task_tracking 客户端分组（flowGrouping.ts），
URL 解析链有 flow_id；手动 /transcribe（ai_router.py:296/322 两行：extract_audio→ai_transcription）
与 cover_frames（distribution_router.py:1518）建行没传 flow_id → 孤立单行。

**实现**（后端，路线 C 纪律内）：
1. 手动转录端点：建 `manager.create_flow(name=f"Transcribe {filename}")`，extract_audio 与
   （链式产生的）ai_transcription 行都挂同一 flow_id——链式行建行点在 workflow 完成后的
   dispatch 处，flow_id 要通过 metadata/参数传递到位（侦察 download_helpers 的链式建行怎么拿
   flow_id，照既有 parse 链的传递方式）。音轨已在盘直接建 ai_transcription 的路径：单行也挂
   flow（单步 flow 渲染正常）。
2. cover_frames：建 flow（"Cover frames: {filename}"）单步挂入，获得与其他任务一致的流程呈现。
3. 摘要手动端点（ai_router 532 等）此期不动（记 backlog——等转录链验证 UI 呈现再推广）。
4. 顺手：distribution_cover.py:32 num_frames 描述纠偏（"Each becomes a resource row" 已失实）。

## 范围外
- agent 触发的任务在 task_tracking 行上 stamp agent_id（RunRecorder._link_task 已有机制，
  但 ensureResourceProcessed 走 HTTP 端点无 run 上下文——记 backlog）
- ai_summary/ai_extract 手动端点 flow 化；agent_runs 的 skill_slugs_used 展示
- 帧的持久化/复用 cover_frames 候选（候选是纯临时 base64，不复用）

## 生产验收（可证伪）
1. @ 一个已下载视频问"画面里有什么"（或明确要求 frames）→ agent 调 ResourceFetch frames →
   vision 模型基于真实画面回答（对照：回答内容与视频画面实际吻合，不是从文件名猜）
2. 非 vision 模型同样调用 → 得到可理解的降级说明，不是裸错误
3. Task Center 的 agent 行出现 agent 名 badge
4. 手动点 Transcribe（无音轨视频）→ Task Center 出现 2 步流程圆点（extract_audio → transcription）
5. Cover frames 提取 → 流程卡呈现
