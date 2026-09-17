# 给 nous-engine 的实现清单：多模态 Embedding 与 Rerank 端点

日期：2026-09-16 · 来源：nous-app `2026-09-16-video-vector-layers-design.md` · 收件：nous-engine 会话

## 背景一句话

nous-app 要做视频四层向量检索（语义 / 画面帧 / 镜头片段 / 转录）。业务层只认协议、能力声明、向量空间三样东西，换本地模型或网络 API 功能不变。模型权重、vLLM 参数、显卡分配、多模态输入兼容层全部在 nous-engine。

2026-09-15/16 已在 gpupc 临时起过 WeMM-Embedding-2B/4B/9B 与 Qwen3-Embedding-8B 做基准（临时进程已全部停掉，只剩 :39265 的 27B LLM）。结论：文本与图文检索 2B 不输 4B/9B，生产先上 2B；9B 只在片段向量上待验。

## 要实现的四件事

### 1. 多模态 Embedding 端点（网关 `/v1/embeddings`，OpenAI 兼容外形）

请求体在 OpenAI embeddings 之上扩展：

```json
{
  "model": "wemm-embedding-2b",
  "input": [
    {"type": "text", "text": "Instruct: ...\nQuery: 手持跟拍"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    {"type": "video_frames", "frames": [{"image_url": {"url": "data:..."}, "timestamp": 0.0}, ...]},
    {"type": "video_url", "video_url": {"url": "https://..."}}
  ],
  "dimensions": 2048,
  "encoding_format": "float"
}
```

- `input` 接受：单字符串、字符串数组（纯文本批量）、内容项数组（一条向量 = 一个内容项）、内容项数组的数组（一条向量 = 一组内容项，图文混合）。
- 引擎内部把每条转成 WeMM 的 `messages` + `embedding_chat_template.jinja`。**文本绝不能走 vLLM 裸 `input`**：实测 2B 关键词召回从 0.22 掉到 0.06。
- `dimensions`：matryoshka 截断 + 重新 L2 归一化。nous-app 所有向量列是 `halfvec(2048)`（pgvector HNSW 上限），4B（2560）/ 9B（4096）必须能截到 2048。拒绝不在模型 `matryoshka_dims` 列表里的值，返回 422 说明可选值。
- `video_frames` 上限 16 帧（业务侧按镜头均匀抽帧），`video_url` 由引擎自己抽帧，帧数走同一上限。
- 响应：标准 `{"data":[{"index":i,"embedding":[...]}], "model": ..., "usage": {"prompt_tokens": n}}`，顺序与输入一致，任何一条失败整批 4xx/5xx 并指明 index。
- 走网关 Bearer key（M:N grant），nous-app 不再直连 vLLM 端口。

### 2. Rerank 端点（网关 `/v1/rerank`）

```json
{"model": "qwen3-vl-reranker", "query": "手持跟拍", "documents": ["...", {"type":"image_url", ...}], "top_n": 20}
```

响应 `{"results":[{"index": i, "relevance_score": s}], "model": ...}`，按分数降序。首选 Qwen3-VL-Reranker（同系列，图文都能排），退而求其次 Qwen3-Reranker-0.6B（纯文本）。

### 3. `/v1/models` 带能力声明

每张 embedding 模型卡多回这些字段，nous-app 直接读成能力声明：

```json
{
  "id": "wemm-embedding-2b",
  "kind": "embedding",
  "modalities": ["text", "image", "video"],
  "native_dims": 2048,
  "matryoshka_dims": [64, 128, 256, 512, 1024, 2048],
  "max_video_frames": 16,
  "instruction_style": "prefix",
  "ready": true
}
```

`ready` 沿用现有"模型卡已开且 daemon 在线"的语义；不在请求路径上加载模型。

### 4. 部署与资源

- **WeMM-Embedding-2B 常驻**，放 RTX PRO 6000（UUID `GPU-d24ed424-5712-55e9-9b95-77d997ac80dc`）。⚠️ CUDA 设备序号与 nvidia-smi 序号不一致，2026-09-15 用 `CUDA_VISIBLE_DEVICES=1` 起到了一张 3090 上；必须按 UUID 钉。
- 已验证可用的启动参数（vLLM 0.28，权重在 `/media/heygo/program/models/nous/embedding/`）：
  ```
  --runner pooling --chat-template $M/embedding_chat_template.jinja
  --max-model-len 8192 --max-num-seqs 16 --limit-mm-per-prompt '{"image":1,"video":1}'
  --gpu-memory-utilization  2B: 0.26   4B: 0.30   9B: 0.32
  ```
  显存比例低于这些值会报 "No available memory for the cache blocks"（2B 0.16、4B 0.20 都失败过）。
- 9B 按需起（片段向量 A/B 时），不常驻；4B 不上。
- Qwen3-VL-Embedding-2B/8B 做一张备选卡，同一协议，供 nous-app 对照。
- 并发目标：8 路并发下 p50 文本 ≤ 30 ms、单图 ≤ 150 ms、16 帧片段 ≤ 600 ms（2B 实测文本 21 ms、图 127 ms）。
- 不要动 :39265 的 27B LLM。

## 验收（nous-engine 侧自测，curl 走网关）

1. 文本、单图、16 帧片段、图文混合四种输入各返回 2048 维、L2 范数 ≈ 1。
2. `dimensions: 1024` 返回 1024 维且归一化；`dimensions: 3000` 返回 422。
3. 同一句文本走本端点与走 2026-09-15 的直连基准脚本（`/tmp/embed_bench_instr.py` 的 `WeMM.embed`）余弦 ≥ 0.999。
4. rerank 对 20 条文档返回 20 个分数，降序。
5. `/v1/models` 能力字段齐全；停掉模型进程后 `ready=false`，端点返回 `model_not_ready` 而不是 500。
6. 网关无 key 401，错 key 403。

## 与 nous-app 的接口约定

- nous-app 在 Admin → AI Models 登记一行：协议 `openai-embeddings-multimodal`，base_url 指网关，key 加密存；能力声明从 `/v1/models` 读或手填。
- 换模型 = nous-app 新建向量空间 + 全量重嵌，引擎侧只需保证协议不变。
- 引擎离线时 nous-app 搜索退化为文本腿，不报错；所以端点的失败必须是快失败（连接拒绝 / `model_not_ready`），不要长时间挂起。
