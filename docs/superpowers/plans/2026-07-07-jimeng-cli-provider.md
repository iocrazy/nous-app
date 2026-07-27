# Jimeng CLI Provider（dreamina 生图+生视频接入）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把官方 `dreamina` CLI（即梦，订阅额度）接成 Nous 的图像+视频生成 provider——生图为 shot Generate 主力（解 Seedream 未开通阻塞），生视频给 shot 卡新增 Generate Video 能力（flag-dark）。用户三裁决（2026-07-07）：图+视频一起 / 容器装 CLI+NAS 一次登录 / **CLI 为主力，Ark 行保留备份**。

**Architecture:** 与 ArkImageProvider 平行的 `JimengCliProvider`（subprocess 驱动）：`asyncio.create_subprocess_exec` 调 `dreamina text2image|text2video|image2video`（off-loop + `wait_for` 硬超时 + kill——backend freeze 血泪约束），robust 提取混合 stdout 里的 JSON（submit_id/gen_status），产物经 `--download_dir` 落本地临时目录 → `register_generated_media` 新增 `source_path` 本地文件直采（跳过 URL 下载）。DB catalog 照旧驱动：`mediahub_models` 加 `provider='jimeng-cli'` 行（无 api_key），解析扩展到按 provider 字段分派。

**CLI 接口事实**（从 Infinite-Canvas 行为观察得到的接口知识；**clean-room：不搬其任何 Python 代码**；dreamina 本身是官方工具直接用）：
- 安装 `curl -fsSL https://jimeng.jianying.com/cli | bash`；`dreamina login`（交互）；`user_credit` 查额度/验登录态；`logout`
- `text2image --prompt=... --ratio=W:H --resolution_type=... --poll=N [--model_version=5.0]`
- `image2image --images=<path> --prompt=... --resolution_type=... --poll=N [--model_version=...]`
- 视频族：`text2video` / `image2video`（model_version = seedance2.0 / seedance2.0fast / 3.0pro 等）
- `query_result --submit_id=... --download_dir=<dir>`（提交后轮询下载）
- stdout 是日志+JSON 混排——解析要扫描首个/最优 JSON 对象（键含 submit_id/gen_status/result_json/images/videos 者优先）

## Global Constraints

全部既有约束继承（off-loop subprocess/硬超时/lint gate/task_type ≤20/dispatch bundle/#1006 String 收敛/CI 忽略 Vercel）。新增：
- **subprocess 纪律**：绝不在 event loop 上同步 wait；每次调用带硬超时（生图 poll+120s、生视频 poll+300s 上限），超时必 `proc.kill()`
- **CLI 输出不可信**：JSON 解析失败/gen_status 失败/额度不足都要有结构化错误（不裸 500）；stderr 全量进日志
- **登录态是外部依赖**：CLI 返回未登录错误 → provider 抛带 `jimeng_not_logged_in` 语义的错误，健康检查（user_credit）红点提示，不 crash
- ⚠️ **compose 变更需 NAS 手动 `docker compose up -d`**（Watchtower 不读 compose）——volume 挂载正好和一次性登录同一趟运维，写进 runbook

## File Structure

```
docker/Dockerfile.backend（或现 backend Dockerfile 位置——实现者先找）  (modify: 装 dreamina)
docker/docker-compose.yml                              (modify: dreamina 凭证 volume)
docs/runbook/jimeng-cli.md                             (new: 安装/登录/续期/排障 runbook)
backend/app/services/media/parsers/video_providers/jimeng_cli.py   (new: provider 核心)
backend/app/services/media/parsers/video_providers/db_registry.py  (modify: 按 provider 分派 + resolve_video_provider)
backend/app/services/library/generated_media_service.py            (modify: +source_path 本地直采)
backend/app/workflows/script_shot_video.py             (new: 生视频 workflow，task_type ≤20)
backend/app/workflows/_dispatch_bundle.py              (modify: import 新 workflow——#1055 血泪)
backend/app/api/script_shots_router.py                 (modify: +POST /shots/{id}/generate-video, flag 门)
supabase/migrations/34X_jimeng_cli_catalog.sql         (取下一空号: catalog 种子行, 幂等)
frontend/editor/storyboard/ShotCard.tsx                (modify: Generate Video 按钮 + <video> 预览, flag)
frontend/editor/sceneService.ts                        (modify: +generateShotVideo)
backend/tests/test_jimeng_cli_provider.py              (new)
frontend/editor/__tests__/...                          (modify)
```

**PR 切分**：**PR-J1** = Task 1-4（Docker/runbook + provider + 解析 + 本地直采 + catalog——生图主力就位）；**PR-J2** = Task 5-6（生视频 workflow + 端点 + ShotCard UI，flag `FEATURE_SHOT_VIDEO`/`VITE_FEATURE_SHOT_VIDEO` dark）。

---

### Task 1: Docker + runbook——CLI 进容器

- Dockerfile 装 dreamina（官方 curl 脚本；固定安装目录并确认二进制落点，`ENV PATH` 补齐；装失败不阻塞 build 的坑不许有——装不上就 build fail，别静默）
- compose：新 named volume（如 `dreamina-auth:/root/.dreamina`——实现者先在本机装一次确认凭证真实落盘路径再定挂载点）挂 backend+worker 两个 service
- `docs/runbook/jimeng-cli.md`：NAS 首次登录步骤（`sudo docker exec -it mediahub-app-worker dreamina login`）、`user_credit` 验证、登录过期续期、compose up 提醒（Watchtower 不读 compose 的血泪引用）
- 测试：无（构建面）；本机 docker build 验证通过即可
- Commit `feat(infra): dreamina CLI in backend image + auth volume + runbook`

### Task 2: provider 核心 `jimeng_cli.py`

**Interfaces（Produces）：**
```python
class JimengCliProvider:  # 同时实现 image 与 video 两个生成入口
    async def generate_image(self, *, prompt: str, aspect: str, model_version: str | None) -> GenResult
    async def generate_video(self, *, prompt: str, aspect: str, model_version: str | None,
                             image_path: str | None = None) -> GenResult
    async def health(self) -> dict   # user_credit 包装：{ok, credit?, error?}
# GenResult = {local_path: str, mime: str, raw: dict}
```
- `_run_cli(args, timeout)`：create_subprocess_exec + wait_for + kill；stdout/stderr 捕获；返回 (rc, stdout, stderr)
- `_extract_json(text)`：扫描 text 中的 JSON 候选，按含 submit_id/gen_status/result_json/images/videos 键加权取最优（穷举单测：纯 JSON/前后日志夹杂/多对象/无 JSON）
- 提交后无产物但有 submit_id → `query_result --submit_id --download_dir` 一次补拉
- 失败语义分类：`not_logged_in` / `no_credit` / `generation_failed` / `timeout` / `parse_error`——各自典型 stdout 样本进单测（fake subprocess）
- aspect→ratio/resolution 映射与 ArkImageProvider 的 aspect 语义对齐
- 测试全部 fake subprocess（monkeypatch create_subprocess_exec），不碰真 CLI
- Commit `feat(ai): jimeng-cli provider — dreamina image+video generation`

### Task 3: 解析分派 + 本地直采

- `db_registry.py`：`resolve_image_provider` 改为按行的 `provider` 字段分派——`'jimeng-cli'` → JimengCliProvider（无 key），其余走现 Ark 路径；新增 `resolve_video_provider`（type='video' 行同逻辑）。**多行 enabled 时的选择顺序：jimeng-cli 优先（用户裁决 CLI 为主力），显式排序字段后议**
- `generated_media_service.register_generated_media` 加 `source_path: Optional[str]`（与 source_url 二选一）：本地文件直接进 Tier-1/对象存储管线（对象存储分支同样支持——读文件而非下载）；`script_shot_generate` workflow 的 persist 步适配（provider 返回 local_path 时走 source_path）
- 迁移 `34X_jimeng_cli_catalog.sql`：INSERT 两行幂等种子（`jimeng-cli-image` type='image' actual_model='5.0'、`jimeng-cli-seedance` type='video' actual_model='seedance2.0fast'，enabled=true/provider='jimeng-cli'/api_key NULL）——**以 mediahub_models 实际列为准先核 information_schema**
- 测试：分派矩阵（jimeng 行/ark 行/无 enabled 行）+ source_path 直采（fake fs）+ 迁移 dev 双跑
- Commit `feat(ai): provider dispatch by catalog provider field + local-file ingest + jimeng catalog seed`

**→ PR-J1 ship**（终审重点：subprocess 超时/kill 路径、JSON 解析健壮性、登录态失败语义、本地直采不破坏对象存储 fallback 链）。ship 后运维一趟 NAS：compose up + `dreamina login` + `user_credit` 绿 → **真机 Generate E2E（task #48 用 CLI 路径解锁）**。

### Task 4:（并入 Task 3 验证）Generate 真机 E2E
- prod：canary 用户 → shot Generate → 走 jimeng-cli → 图落 generated_media → shot 卡显示缩图（P3 全链路终于闭环）
- 记录额度消耗口径（user_credit 前后差）

### Task 5: 生视频 workflow + 端点（flag-dark）

- `script_shot_video.py` workflow：类比 script_shot_generate——resolve_video_provider → generate_video（若 shot 已有 image_url 可传 image_path 走 image2video，否则 text2video）→ register_generated_media(kind video, mime video/mp4) → shot 新列？**不加列**：视频 URL 写 `generated_media` 行 + shot.metadata jsonb（如无 metadata 列则挂 `data_json`——实现者核列）；status 机同 generate（generating/done/failed 复用或并列 video_status——先核列再定，倾向复用 metadata 内嵌状态避免迁移）
- task_type=`shot_video`（≤20 ✓）；**import 进 _dispatch_bundle.py**
- `POST /shots/{shot_id}/generate-video`：verify_shot_access + `FEATURE_SHOT_VIDEO` flag 门 + 派发（rollback 语义同 generate 端点）
- 测试：wiring（flag off 404/on 派发）+ workflow 步单测（fake provider）+ `test_workflow_registered_in_dispatch_bundle` pin
- Commit `feat(script): shot video generation — seedance via jimeng-cli (flag-dark)`

### Task 6: ShotCard UI + 收口

- ShotCard：`VITE_FEATURE_SHOT_VIDEO` 内 Generate Video 按钮（两击确认同 Auto Storyboard 范式）+ 有视频时 `<video controls preload="metadata">` 内嵌预览（durable /cover 类似的同源 URL——视频走 `/api/v1/generated-media/{id}/file`？**先核该端点对 video 的可用性**，`<video>` 标签带不了 Bearer，如 file 端点要 token 则视频也要一个免 token serving 端点，照 /cover 先例）
- i18n en/zh；零 emoji；双主题
- 门禁 + opus 终审 + ship（PR-J2）+ 真机视频 E2E（生 1 条 seedance2.0fast 短视频全链路）
- memory 收官

## Self-Review 已做
- 三裁决全落：图+视频（T2 双入口/T5-6 视频面）、容器+登录（T1+runbook）、CLI 主力（T3 分派优先级，Ark 行保留）
- 血泪清单：off-loop subprocess/#1055 dispatch bundle/task_type 长度/compose 手动 up/信息 schema 先核/#1006
- 不做：Ark 视频（Seedance via Ark 等火山开通后加行即可，分派逻辑已通用）、每 shot 选 provider UI（用户没选双活）、budget guard
