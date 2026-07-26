# Asset Prompt Management Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 素材正/负双语提示词管理——负面提示词列 + PNG 负面提取 + 信息面板折叠区块（Tags 下方、点开自动打触发标签）+ 网格卡片角标 + Settings 触发标签管理。

**Architecture:** 在既有单条 prompt 实现（`resources.gen_prompt`/`gen_prompt_zh`、translate/generate 端点、PNG extractor）上原地扩展。前端把 2506 行的 `ResourceDetailPage` 里的 prompt 区块抽成独立 `PromptSection` 组件再增强；卡片角标和 Settings 卡片各自独立小组件。数据源不加新端点——前端列表本来就是 Supabase `select('*')`。

**Tech Stack:** FastAPI + Pydantic + Supabase（后端）、React 19 + TypeScript + vitest + testing-library（前端）、DBOS workflow（上传后处理）。

**Spec:** `docs/superpowers/specs/2026-07-26-asset-prompt-management-design.md`

## Global Constraints

- 分支：`feature/asset-prompt-management`（已存在，基于 origin/master）
- UI 文本一律英文；界面文案走 i18n（`frontend/public/locales/en.json` + `zh.json`）
- prompt 字段字符上限 **20000**（`MAX_PROMPT_CHARS`，schema 层 `max_length=20000`）
- 标签开关列名 **`prompt_trigger`**（不得用 `show_prompt`——画板节点数据已占用该名）
- 自动创建的默认触发标签：`name='AI'`、`type='user'`（**不是** system）
- 显隐规则（面板+角标共用）：有 prompt 数据（4 列任一非空）→ 显示；无数据带触发标签 → 折叠 Add 入口；无数据无标签 → 轻量 Add 文字行
- 后端测试：`cd backend && uv run pytest <path> -v`；前端：`cd frontend && npx vitest run <path>`；类型检查：`cd frontend && npm run typecheck`
- 每个 task 结束 commit，git commit 末尾加 `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: DB migrations（384 负面列 + 385 触发开关）

**Files:**
- Create: `supabase/migrations/384_resources_gen_prompt_negative.sql`
- Create: `supabase/migrations/385_tags_prompt_trigger.sql`

**Interfaces:**
- Produces: `resources.gen_prompt_negative TEXT`、`resources.gen_prompt_negative_zh TEXT`、`tags.prompt_trigger BOOLEAN NOT NULL DEFAULT false`（后续所有 task 依赖这三列存在）

- [ ] **Step 1: 写 migration 384**

```sql
-- 384: resources.gen_prompt_negative / _zh — negative generation prompt.
--
-- Extends the bilingual asset prompt block (284/289) with the negative
-- side. A1111-style assets carry "Negative prompt: ..." in PNG metadata;
-- the upload extractor and the info-panel editor both write here.
-- Char cap (20000) is enforced at the API schema layer, same as gen_prompt.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_prompt_negative TEXT,
    ADD COLUMN IF NOT EXISTS gen_prompt_negative_zh TEXT;

COMMENT ON COLUMN public.resources.gen_prompt_negative IS
    'Negative AI generation prompt (EN side), counterpart of gen_prompt.';
COMMENT ON COLUMN public.resources.gen_prompt_negative_zh IS
    'Negative AI generation prompt (ZH side), counterpart of gen_prompt_zh.';
```

- [ ] **Step 2: 写 migration 385**

```sql
-- 385: tags.prompt_trigger — user-configurable "Prompt tag" switch.
--
-- Assets carrying any tag with prompt_trigger=true show the Prompt panel
-- (collapsed row) and the grid-card badge even before prompt data exists.
-- NOT named show_prompt: that name is already used by canvas node data
-- (frontend/features/canvas-core/smart/factories.ts).

ALTER TABLE public.tags
    ADD COLUMN IF NOT EXISTS prompt_trigger BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.tags.prompt_trigger IS
    'When true, assets tagged with this tag surface the Prompt panel/badge.';
```

- [ ] **Step 3: 本地执行并验证**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/384_resources_gen_prompt_negative.sql
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/385_tags_prompt_trigger.sql
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c \
  "SELECT column_name FROM information_schema.columns WHERE table_name='resources' AND column_name LIKE 'gen_prompt%' UNION ALL SELECT column_name FROM information_schema.columns WHERE table_name='tags' AND column_name='prompt_trigger';"
```

Expected: 5 行 — `gen_prompt`、`gen_prompt_zh`、`gen_prompt_negative`、`gen_prompt_negative_zh`、`prompt_trigger`。
（本地 54322 连不上时跳过执行、只提交文件——merge 后 `run-migration.yml` 会在 gpupc 上跑。）

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/384_resources_gen_prompt_negative.sql supabase/migrations/385_tags_prompt_trigger.sql
git commit -m "feat(db): resources 负面提示词两列 + tags.prompt_trigger 触发开关 (mig 384/385)"
```

---

### Task 2: PNG extractor 负面提取（后端 TDD）

**Files:**
- Modify: `backend/app/services/library/png_prompt_extractor.py`
- Test: `backend/tests/services/test_png_prompt_extractor.py`

**Interfaces:**
- Consumes: 现有 `parse_a1111_parameters(text) -> str`、`extract_png_prompt(path) -> Optional[str]`（保持向后兼容不删）
- Produces:
  - `class PngPromptPair(NamedTuple): positive: str; negative: Optional[str]`
  - `parse_a1111_pair(text: str) -> Optional[PngPromptPair]`
  - `extract_png_prompt_pair(file_path: str | Path) -> Optional[PngPromptPair]`（Task 3 消费）

- [ ] **Step 1: 写失败测试**（追加到现有测试文件；文件顶部 import 处加 `parse_a1111_pair, extract_png_prompt_pair, PngPromptPair`。现有 `A1111_BLOB` 常量已含 `Negative prompt: lowres, bad anatomy` 行和 `_chunk`/PNG 构造 helper，直接复用；参考文件内已有的 tEXt 用例的 PNG 组装写法）

```python
NO_NEG_BLOB = (
    "a cat sitting on a windowsill\n"
    "Steps: 30, Sampler: DPM++ 2M, CFG scale: 5, Seed: 42"
)

MULTILINE_NEG_BLOB = (
    "portrait, dramatic light\n"
    "Negative prompt: lowres, bad hands,\nextra fingers, watermark\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 7"
)


class TestParseA1111Pair:
    def test_pair_extracts_positive_and_negative(self):
        pair = parse_a1111_pair(A1111_BLOB)
        assert pair == PngPromptPair(
            positive="masterpiece, 1girl, silver hair,\nbacklit, golden hour",
            negative="lowres, bad anatomy",
        )

    def test_pair_without_negative(self):
        pair = parse_a1111_pair(NO_NEG_BLOB)
        assert pair.positive == "a cat sitting on a windowsill"
        assert pair.negative is None

    def test_multiline_negative_stops_at_settings_line(self):
        pair = parse_a1111_pair(MULTILINE_NEG_BLOB)
        assert pair.negative == "lowres, bad hands,\nextra fingers, watermark"

    def test_empty_blob_returns_none(self):
        assert parse_a1111_pair("") is None

    def test_legacy_positive_only_helper_unchanged(self):
        # back-compat: old callers still get the bare positive string
        assert parse_a1111_parameters(A1111_BLOB) == (
            "masterpiece, 1girl, silver hair,\nbacklit, golden hour"
        )


class TestExtractPngPromptPair:
    def test_pair_from_a1111_png(self, tmp_path: Path):
        png = tmp_path / "a.png"
        png.write_bytes(_png_with_text_chunk("parameters", A1111_BLOB))
        pair = extract_png_prompt_pair(png)
        assert pair.negative == "lowres, bad anatomy"

    def test_comfyui_png_has_no_negative(self, tmp_path: Path):
        graph = json.dumps({
            "1": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a long descriptive positive prompt here"}},
        })
        png = tmp_path / "c.png"
        png.write_bytes(_png_with_text_chunk("prompt", graph))
        pair = extract_png_prompt_pair(png)
        assert pair.positive.startswith("a long descriptive")
        assert pair.negative is None

    def test_legacy_extract_still_positive_only(self, tmp_path: Path):
        png = tmp_path / "b.png"
        png.write_bytes(_png_with_text_chunk("parameters", A1111_BLOB))
        assert extract_png_prompt(png) == (
            "masterpiece, 1girl, silver hair,\nbacklit, golden hour"
        )
```

注意：现有测试文件里如果没有名为 `_png_with_text_chunk` 的 helper（构造 `PNG_SIGNATURE + tEXt chunk + IEND` 字节串），按文件内既有 tEXt 用例的组装方式提一个模块级 helper 出来复用，不要重复内联。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/services/test_png_prompt_extractor.py -v`
Expected: 新用例 FAIL（`ImportError: cannot import name 'parse_a1111_pair'`），旧用例 PASS。

- [ ] **Step 3: 实现**（`png_prompt_extractor.py`）

在 import 区加 `from typing import NamedTuple`（并入现有 typing import 行），然后：

```python
class PngPromptPair(NamedTuple):
    """Positive + optional negative prompt extracted from PNG metadata."""

    positive: str
    negative: Optional[str]


def parse_a1111_pair(text: str) -> Optional[PngPromptPair]:
    """Split an A1111 ``parameters`` blob into positive/negative prompts.

    Blob layout: positive (multi-line) → optional ``Negative prompt: ...``
    block (multi-line) → ``Steps: ...`` settings line. ComfyUI graphs don't
    label negative in API format, so this only applies to A1111 blobs.
    """
    pos_lines: list[str] = []
    neg_lines: list[str] = []
    section = "positive"
    for line in text.splitlines():
        if section == "positive":
            if line.startswith("Negative prompt:"):
                section = "negative"
                first = line[len("Negative prompt:"):].strip()
                if first:
                    neg_lines.append(first)
                continue
            if _SETTINGS_LINE_RE.match(line):
                break
            pos_lines.append(line)
        else:
            if _SETTINGS_LINE_RE.match(line):
                break
            neg_lines.append(line)
    positive = "\n".join(pos_lines).strip()
    if not positive:
        return None
    negative = "\n".join(neg_lines).strip() or None
    return PngPromptPair(positive=positive, negative=negative)
```

`parse_a1111_parameters` 改为薄委托（删掉原 for 循环体）：

```python
def parse_a1111_parameters(text: str) -> str:
    """Extract the positive prompt from an A1111 ``parameters`` blob."""
    pair = parse_a1111_pair(text)
    return pair.positive if pair else ""
```

`extract_png_prompt` 同样改薄委托，主逻辑搬进 pair 版（try/except 与截断策略保持原样）：

```python
def extract_png_prompt_pair(file_path: str | Path) -> Optional[PngPromptPair]:
    """Extract positive+negative generation prompts from a PNG, or None.

    A1111 ``parameters`` first (labeled negative), then ComfyUI ``prompt``
    graph (positive only — API format doesn't label negative). Never raises.
    """
    path = Path(file_path)
    try:
        comfy_graph: Optional[str] = None
        for keyword, text in _iter_text_chunks(path):
            if keyword == "parameters":
                pair = parse_a1111_pair(text)
                if pair:
                    return PngPromptPair(
                        positive=pair.positive[:MAX_PROMPT_CHARS],
                        negative=(pair.negative or None) and pair.negative[:MAX_PROMPT_CHARS],
                    )
            elif keyword == "prompt" and comfy_graph is None:
                comfy_graph = text
        if comfy_graph:
            prompt = extract_comfyui_prompt(comfy_graph)
            if prompt:
                return PngPromptPair(positive=prompt[:MAX_PROMPT_CHARS], negative=None)
        return None
    except OSError as e:
        logger.warning(f"[PngPrompt] cannot read {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[PngPrompt] unexpected parse failure for {path}: {e}")
        return None


def extract_png_prompt(file_path: str | Path) -> Optional[str]:
    """Back-compat: positive prompt only. See extract_png_prompt_pair."""
    pair = extract_png_prompt_pair(file_path)
    return pair.positive if pair else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/services/test_png_prompt_extractor.py -v`
Expected: 全部 PASS（含旧用例——`parse_a1111_parameters` 空输入返回 `""` 与原行为一致，若旧用例断言不同以旧用例为准调整委托）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library/png_prompt_extractor.py backend/tests/services/test_png_prompt_extractor.py
git commit -m "feat(parse): PNG extractor 提取 A1111 负面提示词 (PngPromptPair)"
```

---

### Task 3: upload_postprocess 写入负面（不覆盖已有值）

**Files:**
- Modify: `backend/app/workflows/upload_postprocess.py`（step 定义在 ~L86，Phase A2 写入在 ~L177-195）
- Test: `backend/tests/test_upload_postprocess_workflow.py`（~L274 现有 mock 目标是 `extract_png_prompt`）

**Interfaces:**
- Consumes: Task 2 的 `extract_png_prompt_pair` → `PngPromptPair`
- Produces: step 返回值改为 `Optional[dict]`：`{"positive": str, "negative": str | None}`（DBOS step 输出走 JSON 序列化，dict 比 NamedTuple 稳）

- [ ] **Step 1: 改现有 workflow 测试的 mock**

把 ~L274 的 patch 目标 `app.services.library.png_prompt_extractor.extract_png_prompt`（返回 str）改为
`app.services.library.png_prompt_extractor.extract_png_prompt_pair`，返回值改为
`PngPromptPair(positive="masterpiece, 1girl", negative="lowres")`（文件顶部补 import）。
并在该用例断言 `update_resource` 收到的 patch 同时含 `gen_prompt` 与 `gen_prompt_negative`。
再加一个用例：resource 已有 `gen_prompt_negative`（mock `get_resource_by_id` 返回
`{"gen_prompt": "", "gen_prompt_negative": "user typed"}`）→ patch 只含 `gen_prompt`，不含 negative（never clobber）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_upload_postprocess_workflow.py -v -k prompt`
Expected: FAIL（step 仍调旧函数/返回 str）。

- [ ] **Step 3: 实现**

step（~L86 一带）：

```python
async def upload_postprocess_png_prompt_step(file_path: str) -> Optional[dict]:
    from app.services.library.png_prompt_extractor import extract_png_prompt_pair

    pair = extract_png_prompt_pair(file_path)
    if not pair:
        return None
    return {"positive": pair.positive, "negative": pair.negative}
```

（保留原有装饰器/签名形态——若原 step 有 `@DBOS.step()` 之类装饰器，原样保留只改函数体与返回类型。）

Phase A2 写入（替换 ~L183-190 的 `if prompt:` 块）：

```python
                pair = await upload_postprocess_png_prompt_step(file_path)
                if pair and pair.get("positive"):
                    current = await svc.repo.get_resource_by_id(resource_id)
                    if current:
                        patch: dict = {}
                        if not (current.get("gen_prompt") or "").strip():
                            patch["gen_prompt"] = pair["positive"]
                        if pair.get("negative") and not (
                            current.get("gen_prompt_negative") or ""
                        ).strip():
                            patch["gen_prompt_negative"] = pair["negative"]
                        if patch:
                            await svc.repo.update_resource(resource_id, patch)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_upload_postprocess_workflow.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/upload_postprocess.py backend/tests/test_upload_postprocess_workflow.py
git commit -m "feat(upload): PNG 上传后处理同时提取写入负面提示词"
```

---

### Task 4: ResourceUpdate 负面字段 + translate 正负都翻

**Files:**
- Modify: `backend/app/schemas/resources.py`（ResourceUpdate ~L15-30）
- Modify: `backend/app/api/resources_ai_router.py`（translate_gen_prompt ~L42-115）
- Test: `backend/tests/test_gen_prompt_translate_plan.py`（新建）

**Interfaces:**
- Consumes: mig 384 两列
- Produces:
  - `ResourceUpdate.gen_prompt_negative` / `gen_prompt_negative_zh`（`PATCH /resources/{id}` 因此直接支持，前端 Task 6 消费）
  - `build_translate_plan(resource: dict, target_lang: str) -> list[tuple[str, str, str]]`（router 模块级纯函数，返回 `(source_field, target_field, source_text)` 列表）
  - translate 端点响应 `data` 含 4 个字段：`gen_prompt`、`gen_prompt_zh`、`gen_prompt_negative`、`gen_prompt_negative_zh`

- [ ] **Step 1: 写失败测试**（纯函数级，避开 TestClient/auth 依赖）

```python
# backend/tests/test_gen_prompt_translate_plan.py

"""Unit tests for build_translate_plan — which prompt fields get translated."""

from app.api.resources_ai_router import build_translate_plan


def test_zh_target_translates_both_sides():
    resource = {"gen_prompt": "a cat", "gen_prompt_negative": "lowres"}
    plan = build_translate_plan(resource, "zh")
    assert plan == [
        ("gen_prompt", "gen_prompt_zh", "a cat"),
        ("gen_prompt_negative", "gen_prompt_negative_zh", "lowres"),
    ]


def test_en_target_reads_zh_sides():
    resource = {"gen_prompt_zh": "一只猫", "gen_prompt_negative_zh": "低分辨率"}
    plan = build_translate_plan(resource, "en")
    assert plan == [
        ("gen_prompt_zh", "gen_prompt", "一只猫"),
        ("gen_prompt_negative_zh", "gen_prompt_negative", "低分辨率"),
    ]


def test_skips_empty_sides():
    plan = build_translate_plan({"gen_prompt": "a cat", "gen_prompt_negative": "  "}, "zh")
    assert plan == [("gen_prompt", "gen_prompt_zh", "a cat")]


def test_all_empty_gives_empty_plan():
    assert build_translate_plan({}, "zh") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_gen_prompt_translate_plan.py -v`
Expected: FAIL（`ImportError: cannot import name 'build_translate_plan'`）。

- [ ] **Step 3: 实现**

`schemas/resources.py` — `gen_prompt_zh` 字段后追加：

```python
    gen_prompt_negative: Optional[str] = Field(
        None,
        max_length=20000,
        description="Negative AI generation prompt attached to this asset",
    )
    gen_prompt_negative_zh: Optional[str] = Field(
        None,
        max_length=20000,
        description="Chinese-language negative AI generation prompt",
    )
```

`resources_ai_router.py` — router 定义前加模块级纯函数：

```python
_PROMPT_FIELD_PAIRS = [
    # (en_field, zh_field)
    ("gen_prompt", "gen_prompt_zh"),
    ("gen_prompt_negative", "gen_prompt_negative_zh"),
]


def build_translate_plan(
    resource: dict, target_lang: str
) -> list[tuple[str, str, str]]:
    """(source_field, target_field, source_text) per non-empty source side.

    target_lang='zh' reads the EN columns; 'en' reads the ZH columns.
    Empty/whitespace sources are skipped so a positive-only asset still
    translates cleanly.
    """
    plan: list[tuple[str, str, str]] = []
    for en_field, zh_field in _PROMPT_FIELD_PAIRS:
        source_field, target_field = (
            (en_field, zh_field) if target_lang == "zh" else (zh_field, en_field)
        )
        text = (resource.get(source_field) or "").strip()
        if text:
            plan.append((source_field, target_field, text))
    return plan
```

`translate_gen_prompt` 端点：把单字段逻辑（`source_field = ...` 到 `updated = ...` 那段）替换为：

```python
    plan = build_translate_plan(resource, data.target_lang)
    if not plan:
        raise HTTPException(
            status_code=400, detail="No prompt text to translate from"
        )

    try:
        provider_key, provider_config, _model, agent_slug = (
            await resolve_translate_provider_config(auth.user_id)
        )
        svc = TranslateService(
            provider_key=provider_key,
            provider_config=provider_config,
            agent_slug=agent_slug,
        )
        patch: dict = {}
        for _source_field, target_field, source_text in plan:
            translated = await svc.translate(
                text=source_text,
                target_lang=data.target_lang,
                user_id=auth.user_id,
                resource_id=str(resource_id),
            )
            if translated:
                patch[target_field] = translated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Translate gen_prompt failed for {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")

    if not patch:
        raise HTTPException(
            status_code=502,
            detail=(
                "Translation produced no result — check the translation "
                "agent's provider configuration in Settings → AI"
            ),
        )

    updated = await repo.update_resource(resource_id, patch)
    return {
        "success": True,
        "data": {
            "gen_prompt": (updated or {}).get("gen_prompt"),
            "gen_prompt_zh": (updated or {}).get("gen_prompt_zh"),
            "gen_prompt_negative": (updated or {}).get("gen_prompt_negative"),
            "gen_prompt_negative_zh": (updated or {}).get("gen_prompt_negative_zh"),
        },
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_gen_prompt_translate_plan.py -v`
Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/resources.py backend/app/api/resources_ai_router.py backend/tests/test_gen_prompt_translate_plan.py
git commit -m "feat(api): PATCH 支持负面提示词字段;translate 一次调用正负都翻"
```

---

### Task 5: tags 后端 prompt_trigger 读写

**Files:**
- Modify: `backend/app/schemas/tags.py`（TagCreate/TagUpdate/TagResponse）
- Modify: `backend/app/api/tags_router.py`（create ~与 update_tag ~L398：把 prompt_trigger 传进 repo 调用——先读这两个 handler，与 `enabled` 字段同样的透传方式加上即可）
- Test: `backend/tests/test_tags_prompt_trigger_schema.py`（新建）

**Interfaces:**
- Consumes: mig 385 `tags.prompt_trigger`
- Produces: `TagCreate.prompt_trigger: bool = False`、`TagUpdate.prompt_trigger: Optional[bool]`、`TagResponse.prompt_trigger: bool`；`GET /tags` 与 `PUT /tags/{id}` 因此可读写该字段（前端 Task 6/7/9 消费）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_tags_prompt_trigger_schema.py

"""prompt_trigger flows through the tag schemas (mig 385)."""

from app.schemas.tags import TagCreate, TagResponse, TagUpdate


def test_tag_create_defaults_false():
    tag = TagCreate(name="AI")
    assert tag.prompt_trigger is False


def test_tag_create_accepts_true():
    assert TagCreate(name="AI", prompt_trigger=True).prompt_trigger is True


def test_tag_update_optional():
    assert TagUpdate().prompt_trigger is None
    assert TagUpdate(prompt_trigger=True).prompt_trigger is True


def test_tag_response_surfaces_flag():
    row = {
        "id": "123", "name": "AI", "type": "user",
        "color": "#6366f1", "prompt_trigger": True,
    }
    assert TagResponse.model_validate(row).prompt_trigger is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_tags_prompt_trigger_schema.py -v`
Expected: FAIL（字段不存在）。

- [ ] **Step 3: 实现**

`schemas/tags.py`：

```python
# TagCreate 内（TagBase 不加——避免影响其它 TagBase 派生响应的必填校验）:
    prompt_trigger: bool = Field(
        False,
        description="Assets with this tag surface the Prompt panel/badge",
    )

# TagUpdate 内:
    prompt_trigger: Optional[bool] = Field(
        None, description="Toggle the Prompt-panel trigger for this tag"
    )

# TagResponse 内:
    prompt_trigger: bool = Field(
        False, description="Whether this tag triggers the Prompt panel"
    )
```

`tags_router.py`：读 create/update handler，把 `prompt_trigger` 按 `enabled` 同款方式透传给
`tags_repository.create_tag(...)` / `update_tag(...)`（repo 是 `**kwargs` 透传 supabase update 的，通常 router 侧
`tag_update.model_dump(exclude_none=True)` 已经自动带上——确认后无改动也算完成，跑 Step 4 验证）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_tags_prompt_trigger_schema.py tests -k "tag" -v --no-header -q 2>&1 | tail -20`
Expected: 新测试 4 PASS，既有 tag 相关测试无回归。

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/tags.py backend/app/api/tags_router.py backend/tests/test_tags_prompt_trigger_schema.py
git commit -m "feat(tags): prompt_trigger 开关读写通路 (schema + router 透传)"
```

---

### Task 6: 前端类型 + 服务层 + 触发标签工具函数

**Files:**
- Modify: `frontend/types.ts`（Resource ~L342 gen_prompt 附近；Tag ~L435）
- Modify: `frontend/services/unifiedTagService.ts`（TagUpdate 类型与 createTag data 形参）
- Create: `frontend/utils/promptTriggerTags.ts`
- Test: `frontend/utils/promptTriggerTags.test.ts`

**Interfaces:**
- Consumes: Task 4/5 的后端字段
- Produces:
  - `Resource.gen_prompt_negative?: string | null`、`Resource.gen_prompt_negative_zh?: string | null`
  - `Tag.prompt_trigger?: boolean`
  - `hasPromptData(r: Pick<Resource,'gen_prompt'|'gen_prompt_zh'|'gen_prompt_negative'|'gen_prompt_negative_zh'>): boolean`
  - `pickDefaultTriggerTag(tags: Tag[]): Tag | null`（`prompt_trigger` 且 `created_at` 最早；Tag 无 created_at 字段时按数组顺序第一个——fetchAllTags 服务端有序）
  - `ensureDefaultTriggerTag(allTags: Tag[]): Promise<Tag>`（无触发标签时 `createTag({name:'AI', color:'#6366f1', type:'user', prompt_trigger:true})`）

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/utils/promptTriggerTags.test.ts
import { describe, expect, it, vi } from 'vitest';
import { hasPromptData, pickDefaultTriggerTag } from './promptTriggerTags';
import type { Tag } from '../types';

const tag = (over: Partial<Tag>): Tag =>
  ({ id: '1', name: 't', color: null, icon: null, type: 'user', ...over }) as Tag;

describe('hasPromptData', () => {
  it('true when any of the four fields is non-empty', () => {
    expect(hasPromptData({ gen_prompt: 'x', gen_prompt_zh: null } as never)).toBe(true);
    expect(hasPromptData({ gen_prompt: null, gen_prompt_negative: 'neg' } as never)).toBe(true);
  });
  it('false when all empty/whitespace', () => {
    expect(hasPromptData({ gen_prompt: '  ', gen_prompt_zh: null } as never)).toBe(false);
  });
});

describe('pickDefaultTriggerTag', () => {
  it('returns first prompt_trigger tag', () => {
    const tags = [tag({ id: 'a' }), tag({ id: 'b', prompt_trigger: true }), tag({ id: 'c', prompt_trigger: true })];
    expect(pickDefaultTriggerTag(tags)?.id).toBe('b');
  });
  it('null when none', () => {
    expect(pickDefaultTriggerTag([tag({})])).toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run utils/promptTriggerTags.test.ts`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`types.ts` — Resource 的 `gen_prompt_zh?` 行后加：

```typescript
  gen_prompt_negative?: string | null;
  gen_prompt_negative_zh?: string | null;
```

Tag 接口（`enabled?` 附近）加：

```typescript
  prompt_trigger?: boolean;
```

`unifiedTagService.ts`：`TagUpdate` 类型加 `prompt_trigger?: boolean`；`createTag` 的 data 形参类型加
`prompt_trigger?: boolean`（body 原样 JSON 透传，无其它改动）。

`frontend/utils/promptTriggerTags.ts`：

```typescript
// frontend/utils/promptTriggerTags.ts

/**
 * Prompt trigger-tag helpers (spec 2026-07-26-asset-prompt-management).
 * A "trigger tag" (tags.prompt_trigger=true) surfaces the Prompt panel and
 * grid badge on assets that carry it. The default trigger tag is the first
 * prompt_trigger tag in fetchAllTags order; when none exists we create a
 * user-scoped 'AI' tag on demand.
 */
import { createTag } from '../services/unifiedTagService';
import type { Resource, Tag } from '../types';

type PromptFields = Pick<
  Resource,
  'gen_prompt' | 'gen_prompt_zh' | 'gen_prompt_negative' | 'gen_prompt_negative_zh'
>;

export function hasPromptData(r: PromptFields): boolean {
  return [r.gen_prompt, r.gen_prompt_zh, r.gen_prompt_negative, r.gen_prompt_negative_zh]
    .some((v) => !!(v && v.trim()));
}

export function pickDefaultTriggerTag(tags: Tag[]): Tag | null {
  return tags.find((t) => t.prompt_trigger) ?? null;
}

export async function ensureDefaultTriggerTag(allTags: Tag[]): Promise<Tag> {
  const existing = pickDefaultTriggerTag(allTags);
  if (existing) return existing;
  return createTag({
    name: 'AI',
    color: '#6366f1',
    type: 'user',
    prompt_trigger: true,
  } as Parameters<typeof createTag>[0]);
}
```

（若 `createTag` 的形参签名与上面不符——先读该函数 ~L50——按实际签名调整传参；`type` 不在形参里就去掉。）

- [ ] **Step 4: 跑测试 + 类型检查确认通过**

Run: `cd frontend && npx vitest run utils/promptTriggerTags.test.ts && npm run typecheck`
Expected: 测试 PASS、tsc 0 error。

- [ ] **Step 5: Commit**

```bash
git add frontend/types.ts frontend/services/unifiedTagService.ts frontend/utils/promptTriggerTags.ts frontend/utils/promptTriggerTags.test.ts
git commit -m "feat(fe): 负面提示词类型 + prompt_trigger 类型与触发标签工具"
```

---

### Task 7: PromptSection 组件（折叠/展开 + 负面框 + 自动打标）并接入 ResourceDetailPage

**Files:**
- Create: `frontend/components/resources/PromptSection.tsx`
- Test: `frontend/components/resources/PromptSection.test.tsx`
- Modify: `frontend/components/ResourceDetailPage.tsx`（删 ~L1825-1933 旧 Prompt 区块 + L300-304 的 `promptValue/promptOpen/promptLang` 等本地 state、L472-493 的 `commitPrompt/copyPrompt`、L437-439 的同步 effect；`handleGeneratePrompt`/`handleTranslatePrompt` 保留在页面传入；新区块挂到 EagleTagPicker 之后、Properties `<div className="px-4 mt-4 border-t...">` 之前）
- Modify: `frontendend/public/locales/en.json`、`frontend/public/locales/zh.json` → 实际路径 `frontend/public/locales/en.json` / `zh.json`

**Interfaces:**
- Consumes: Task 6 的 `hasPromptData`、Task 4 的 PATCH 字段、现有 `handleResourceUpdate` / `translateGenPrompt` / `handleGeneratePrompt`
- Produces:

```typescript
export interface PromptSectionProps {
  resource: Resource;
  /** Optimistic local-merge + PATCH (page's handleResourceUpdate). */
  onPatch: (fields: Partial<Resource>) => void;
  /** Merge already-persisted fields into page state (post-translate). */
  onMerge: (fields: Partial<Resource>) => void;
  /** True when any assigned tag has prompt_trigger. */
  hasTriggerTag: boolean;
  /** Apply the default trigger tag to this asset (page implements). */
  onEnsureTriggerTag: () => Promise<void>;
  /** Image assets get the Generate (reverse-engineer) button. */
  canGenerate: boolean;
  generating: boolean;
  onGenerate: () => void;
  translating: boolean;
  onTranslate: (lang: 'en' | 'zh') => void;
}
```

组件内部 state：`expanded`、`lang: 'en'|'zh'`、`posValue`、`negValue`（resource/lang 变化时同步，同旧实现 L437 的 effect 模式）。

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/components/resources/PromptSection.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptSection } from './PromptSection';
import type { Resource } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const base = (over: Partial<Resource> = {}): Resource =>
  ({
    id: 'r1', filename: 'a.png', file_type: 'image',
    gen_prompt: null, gen_prompt_zh: null,
    gen_prompt_negative: null, gen_prompt_negative_zh: null,
    ...over,
  }) as unknown as Resource;

const noop = () => {};
const props = (over: Partial<Parameters<typeof PromptSection>[0]> = {}) => ({
  resource: base(), onPatch: noop, onMerge: noop,
  hasTriggerTag: false, onEnsureTriggerTag: vi.fn().mockResolvedValue(undefined),
  canGenerate: true, generating: false, onGenerate: noop,
  translating: false, onTranslate: noop,
  ...over,
});

describe('PromptSection', () => {
  it('shows collapsed preview when prompt data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'masterpiece, 1girl' }) })} />);
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull(); // not expanded yet
  });

  it('shows light Add Prompt entry when no data and no trigger tag', () => {
    render(<PromptSection {...props()} />);
    expect(screen.getByText(/Add Prompt/)).toBeTruthy();
  });

  it('expanding calls onEnsureTriggerTag and reveals both textareas', async () => {
    const p = props({ resource: base({ gen_prompt: 'pos text' }) });
    render(<PromptSection {...p} />);
    fireEvent.click(screen.getByText(/pos text/));
    expect(p.onEnsureTriggerTag).toHaveBeenCalledOnce();
    expect(await screen.findByDisplayValue('pos text')).toBeTruthy();
    expect(screen.getByPlaceholderText(/negative/i)).toBeTruthy();
  });

  it('negative blur patches gen_prompt_negative', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: 'lowres, bad hands' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative: 'lowres, bad hands' });
  });

  it('zh lang patches the _zh columns', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    fireEvent.click(screen.getByText('中'));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: '低分辨率' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative_zh: '低分辨率' });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/resources/PromptSection.test.tsx`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 PromptSection**

```tsx
// frontend/components/resources/PromptSection.tsx

/**
 * PromptSection — bilingual positive/negative AI-generation prompt block.
 *
 * Extracted from ResourceDetailPage (spec 2026-07-26-asset-prompt-management).
 * Sits AFTER the Tags picker, BEFORE Properties. Three states:
 *   - prompt data present  → collapsed one-line preview, click to expand
 *   - no data, trigger tag → collapsed "+ Add Prompt" row
 *   - no data, no tag      → light "+ Add Prompt" text entry
 * Expanding auto-applies the default trigger tag (onEnsureTriggerTag).
 */
import { useEffect, useState } from 'react';
import { ChevronRight, ChevronUp, Copy, Languages, Loader2, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Resource } from '../../types';
import { hasPromptData } from '../../utils/promptTriggerTags';

export interface PromptSectionProps {
  resource: Resource;
  onPatch: (fields: Partial<Resource>) => void;
  onMerge: (fields: Partial<Resource>) => void;
  hasTriggerTag: boolean;
  onEnsureTriggerTag: () => Promise<void>;
  canGenerate: boolean;
  generating: boolean;
  onGenerate: () => void;
  translating: boolean;
  onTranslate: (lang: 'en' | 'zh') => void;
}

export function PromptSection({
  resource, onPatch, onMerge: _onMerge, hasTriggerTag, onEnsureTriggerTag,
  canGenerate, generating, onGenerate, translating, onTranslate,
}: PromptSectionProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [lang, setLang] = useState<'en' | 'zh'>('en');
  const [posValue, setPosValue] = useState('');
  const [negValue, setNegValue] = useState('');

  const posField = lang === 'zh' ? 'gen_prompt_zh' : 'gen_prompt';
  const negField = lang === 'zh' ? 'gen_prompt_negative_zh' : 'gen_prompt_negative';

  // Sync editors from the resource whenever id/lang/data changes.
  useEffect(() => {
    setPosValue((resource[posField] as string | null) || '');
    setNegValue((resource[negField] as string | null) || '');
  }, [resource.id, resource[posField], resource[negField], lang]);

  const dataPresent = hasPromptData(resource);
  const preview =
    (resource.gen_prompt || resource.gen_prompt_zh || '').split('\n')[0];

  const expand = () => {
    setExpanded(true);
    // Fire-and-forget: tag failure must not block editing.
    onEnsureTriggerTag().catch((err) => console.error('ensureTriggerTag:', err));
  };

  const commit = (field: string, value: string, current: string) => {
    if (value.trim() !== current.trim()) onPatch({ [field]: value.trim() } as Partial<Resource>);
  };

  const copyText = (text: string) => {
    if (text) navigator.clipboard.writeText(text).catch((e) => console.error(e));
  };

  if (!expanded && !dataPresent && !hasTriggerTag) {
    return (
      <div className="px-4 mt-2">
        <button onClick={expand} className="text-[11px] text-ink-600 hover:text-[var(--accent-text)] transition-colors">
          + {t('resources.infoPanel.addPrompt', 'Add Prompt')}
        </button>
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="px-4 mt-3">
        <button
          onClick={expand}
          className="w-full flex items-center gap-2 px-2.5 py-2 border border-ink-700/50 bg-ink-800/40 rounded-lg hover:border-[var(--accent-border)] transition-colors text-left"
        >
          <Sparkles size={12} className="text-ink-500 shrink-0" />
          <span className="text-[10px] text-ink-500 uppercase tracking-wider shrink-0">
            {t('resources.infoPanel.prompt', 'Prompt')}
          </span>
          <span className="flex-1 min-w-0 truncate font-mono text-[10.5px] text-ink-500">
            {dataPresent ? preview : `+ ${t('resources.infoPanel.addPrompt', 'Add Prompt')}`}
          </span>
          <ChevronRight size={12} className="text-ink-500 shrink-0" />
        </button>
      </div>
    );
  }

  const otherSidePos = lang === 'zh' ? resource.gen_prompt : resource.gen_prompt_zh;
  const otherSideNeg = lang === 'zh' ? resource.gen_prompt_negative : resource.gen_prompt_negative_zh;

  return (
    <div className="px-4 mt-3">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-ink-500 uppercase tracking-wider">
            {t('resources.infoPanel.prompt', 'Prompt')}
          </span>
          <div className="flex rounded overflow-hidden border border-ink-700/60">
            {(['en', 'zh'] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={`px-1.5 py-0.5 text-[9px] transition-colors ${
                  lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
                }`}
              >
                {l === 'en' ? 'EN' : '中'}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          {canGenerate && (
            <button onClick={onGenerate} disabled={generating}
              title={t('resources.infoPanel.generatePromptHint', 'Reverse-engineer the prompt from this image')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {generating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}{' '}
              {t('resources.infoPanel.generatePrompt', 'Generate')}
            </button>
          )}
          {Boolean(otherSidePos || otherSideNeg) && (
            <button onClick={() => onTranslate(lang)} disabled={translating}
              title={t('resources.infoPanel.translatePromptHint', 'Translate from the other language')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {translating ? <Loader2 size={11} className="animate-spin" /> : <Languages size={11} />}{' '}
              {t('resources.infoPanel.translatePrompt', 'Translate')}
            </button>
          )}
          {Boolean(posValue) && (
            <button onClick={() => copyText(posValue)}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
              <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
            </button>
          )}
          <button onClick={() => setExpanded(false)}
            className="text-ink-500 hover:text-ink-300 transition-colors">
            <ChevronUp size={12} />
          </button>
        </div>
      </div>
      <textarea
        value={posValue}
        onChange={(e) => setPosValue(e.target.value)}
        onBlur={() => commit(posField, posValue, (resource[posField] as string | null) || '')}
        placeholder={t('resources.infoPanel.promptPlaceholder', 'Paste the AI generation prompt...')}
        rows={4}
        className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 resize-none"
      />
      <div className="flex items-center justify-between mt-1.5 mb-1">
        <span className="text-[10px] text-red-400/85 uppercase tracking-wider">
          {t('resources.infoPanel.negativePrompt', 'Negative')}
        </span>
        {Boolean(negValue) && (
          <button onClick={() => copyText(negValue)}
            className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
            <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
          </button>
        )}
      </div>
      <textarea
        value={negValue}
        onChange={(e) => setNegValue(e.target.value)}
        onBlur={() => commit(negField, negValue, (resource[negField] as string | null) || '')}
        placeholder={t('resources.infoPanel.negativePromptPlaceholder', 'Negative prompt (what to avoid)...')}
        rows={2}
        className="w-full bg-red-500/[.06] border border-red-400/25 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-red-400/50 resize-none"
      />
    </div>
  );
}
```

- [ ] **Step 4: 跑组件测试确认通过**

Run: `cd frontend && npx vitest run components/resources/PromptSection.test.tsx`
Expected: 5 PASS。

- [ ] **Step 5: 接入 ResourceDetailPage**

1. 删除旧 Prompt 区块 JSX（~L1825-1933，从 `{/* Prompt — AI generation prompt...` 到对应闭合）。
2. 删除 `promptValue/promptOpen/promptLang` state（L300-302）、`commitPrompt`/`copyPrompt`（L472-493）、同步 effect（L436-439）。**保留** `promptTranslating/promptGenerating` state 与 `handleGeneratePrompt`/`handleTranslatePrompt`/轮询 refs。
3. `handleTranslatePrompt` 改签名接收 lang：`const handleTranslatePrompt = useCallback(async (lang: 'en' | 'zh') => { ... await translateGenPrompt(resourceId, lang); ... }`（内部把原 `promptLang` 引用替换为参数 `lang`）。
4. 在 EagleTagPicker 之后、Properties 块之前插入：

```tsx
          <PromptSection
            resource={resource}
            onPatch={(fields) => handleResourceUpdate(fields)}
            onMerge={(fields) => setResource((prev) => (prev ? { ...prev, ...fields } : prev))}
            hasTriggerTag={assignedTags.some((it) => it.tag?.prompt_trigger)}
            onEnsureTriggerTag={async () => {
              const tag = await ensureDefaultTriggerTag(allTags);
              if (!assignedTags.some((it) => String(it.tag?.id) === String(tag.id))) {
                await addResourceTag(resourceId, String(tag.id));
                const updated = await fetchResourceTags(resourceId);
                setAssignedTags(updated);
                if (!allTags.some((tg) => String(tg.id) === String(tag.id))) {
                  setAllTags((prev) => [...prev, tag]);
                }
              }
            }}
            canGenerate={resource.file_type === 'image'}
            generating={promptGenerating}
            onGenerate={handleGeneratePrompt}
            translating={promptTranslating}
            onTranslate={handleTranslatePrompt}
          />
```

顶部 import：`import { PromptSection } from './resources/PromptSection';`、`import { ensureDefaultTriggerTag } from '../utils/promptTriggerTags';`。清理因删除产生的未用 import（如 `Languages`、`Sparkles` 若仅旧区块使用）。

5. i18n：`frontend/public/locales/en.json` 的 `resources.infoPanel` 下加
   `"negativePrompt": "Negative"`、`"negativePromptPlaceholder": "Negative prompt (what to avoid)..."`；
   `zh.json` 对应 `"negativePrompt": "负面"`、`"negativePromptPlaceholder": "负面提示词（要避免的内容）..."`。

- [ ] **Step 6: 类型检查 + 相关测试**

Run: `cd frontend && npm run typecheck && npx vitest run components/resources/PromptSection.test.tsx`
Expected: 0 error / PASS。

- [ ] **Step 7: Commit**

```bash
git add frontend/components/resources/PromptSection.tsx frontend/components/resources/PromptSection.test.tsx frontend/components/ResourceDetailPage.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(fe): PromptSection 组件 — Tags 下方折叠区块、正/负双框、点开自动打触发标签"
```

---

### Task 8: 网格卡片角标 + 悬浮预览

**Files:**
- Create: `frontend/components/resources/PromptBadge.tsx`
- Test: `frontend/components/resources/PromptBadge.test.tsx`
- Modify: `frontend/components/ResourceCard.tsx`（grid 分支缩略图容器 ~L403-452 内，与 gallery/duration 徽章同级）

**Interfaces:**
- Consumes: Task 6 `hasPromptData`
- Produces: `PromptBadge({ resource, shiftLeft }: { resource: Resource; shiftLeft?: boolean })` — 角标 + hover 弹层；`shiftLeft` 为 true 时角标放左下（视频/音频的时长徽章占了右下）

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/components/resources/PromptBadge.test.tsx
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptBadge } from './PromptBadge';
import type { Resource } from '../../types';

const res = (over: Partial<Resource> = {}): Resource =>
  ({ id: 'r1', gen_prompt: 'masterpiece, 1girl', gen_prompt_negative: 'lowres', ...over }) as unknown as Resource;

describe('PromptBadge', () => {
  it('renders nothing without prompt data', () => {
    const { container } = render(<PromptBadge resource={res({ gen_prompt: null, gen_prompt_negative: null })} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders badge when prompt exists', () => {
    render(<PromptBadge resource={res()} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
  });

  it('hover reveals popover with positive and negative preview', () => {
    render(<PromptBadge resource={res()} />);
    fireEvent.mouseEnter(screen.getByText('Prompt'));
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.getByText(/lowres/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/resources/PromptBadge.test.tsx`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

```tsx
// frontend/components/resources/PromptBadge.tsx

/**
 * PromptBadge — grid-card corner chip for assets carrying an AI prompt.
 * Hovering the chip (not the whole card) opens a preview popover with
 * truncated positive/negative text and a copy action. Renders null when
 * the asset has no prompt data.
 */
import { useState } from 'react';
import { Copy, Sparkles } from 'lucide-react';

import type { Resource } from '../../types';
import { hasPromptData } from '../../utils/promptTriggerTags';

export function PromptBadge({ resource, shiftLeft = false }: { resource: Resource; shiftLeft?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!hasPromptData(resource)) return null;

  const positive = resource.gen_prompt || resource.gen_prompt_zh || '';
  const negative = resource.gen_prompt_negative || resource.gen_prompt_negative_zh || '';

  return (
    <div
      className={`absolute bottom-1.5 ${shiftLeft ? 'left-1.5' : 'right-1.5'} z-10`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onClick={(e) => e.stopPropagation()}
    >
      {open && (
        <div className="absolute bottom-full mb-1.5 right-0 w-56 bg-ink-950 border border-ink-700 rounded-lg shadow-xl p-2.5 z-20 cursor-default">
          <div className="flex items-center justify-between">
            <span className="text-[9px] text-ink-500 uppercase tracking-widest">Prompt</span>
            <button
              onClick={() => navigator.clipboard.writeText(positive).catch((e) => console.error(e))}
              className="flex items-center gap-1 text-[9px] text-[var(--accent-text)] hover:opacity-80"
            >
              <Copy size={10} /> Copy
            </button>
          </div>
          <p className="mt-0.5 font-mono text-[10px] leading-relaxed text-ink-300 line-clamp-3 break-all">{positive}</p>
          {negative && (
            <>
              <span className="mt-1.5 block text-[9px] text-red-400/85 uppercase tracking-widest">Negative</span>
              <p className="mt-0.5 font-mono text-[10px] leading-relaxed text-red-300/80 line-clamp-2 break-all">{negative}</p>
            </>
          )}
        </div>
      )}
      <span className="inline-flex items-center gap-1 bg-ink-950/75 backdrop-blur-sm border border-[var(--accent-border)] text-[var(--accent-text)] text-[9px] font-semibold rounded-full px-2 py-0.5 cursor-default">
        <Sparkles size={10} /> Prompt
      </span>
    </div>
  );
}
```

（`line-clamp-*` 若项目 Tailwind 无该插件——Tailwind 4 内置——typecheck/渲染验证即可。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/resources/PromptBadge.test.tsx`
Expected: 3 PASS。

- [ ] **Step 5: 接入 ResourceCard**

grid 分支缩略图容器内（~L452 缩略图 `</div>` 收口前、与 duration/gallery 徽章同级）加：

```tsx
        {resource && (
          <PromptBadge
            resource={resource}
            shiftLeft={
              (mimeType?.startsWith('video/') || mimeType?.startsWith('audio/')) &&
              resource?.duration_seconds != null
            }
          />
        )}
```

顶部 import：`import { PromptBadge } from './resources/PromptBadge';`。

- [ ] **Step 6: 类型检查 + 既有卡片测试无回归**

Run: `cd frontend && npm run typecheck && npx vitest run components/resources/PromptBadge.test.tsx components/FileCard.test.tsx`
Expected: 0 error / PASS。

- [ ] **Step 7: Commit**

```bash
git add frontend/components/resources/PromptBadge.tsx frontend/components/resources/PromptBadge.test.tsx frontend/components/ResourceCard.tsx
git commit -m "feat(fe): 网格卡片 Prompt 角标 + 悬浮预览弹层"
```

---

### Task 9: Settings → Tags 的 Prompt Trigger Tags 卡片

**Files:**
- Create: `frontend/components/PromptTriggerTagsCard.tsx`
- Test: `frontend/components/PromptTriggerTagsCard.test.tsx`
- Modify: `frontend/components/SettingsView.tsx`（`activeTab === 'tags'` 分支 ~L765，在 `<TagsSettings ...>` 之前渲染本卡片）
- Modify: `frontend/public/locales/en.json` + `zh.json`（`settings.promptTriggerTags.*`）

**Interfaces:**
- Consumes: Task 5/6 的 `Tag.prompt_trigger`、`fetchAllTags`、`updateTag`、`createTag`
- Produces: `PromptTriggerTagsCard()` — 无 props 自包含卡片

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/components/PromptTriggerTagsCard.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const fetchAllTags = vi.fn();
const updateTag = vi.fn();
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  updateTag: (...a: unknown[]) => updateTag(...a),
  createTag: vi.fn(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { PromptTriggerTagsCard } from './PromptTriggerTagsCard';

const tags = [
  { id: '1', name: 'AI', prompt_trigger: true, type: 'user', color: '#6366f1' },
  { id: '2', name: 'cyberpunk', prompt_trigger: false, type: 'user', color: '#818cf8' },
];

beforeEach(() => {
  fetchAllTags.mockResolvedValue(tags);
  updateTag.mockResolvedValue({ ...tags[1], prompt_trigger: true });
});

describe('PromptTriggerTagsCard', () => {
  it('lists trigger tags as chips', async () => {
    render(<PromptTriggerTagsCard />);
    expect(await screen.findByText('AI')).toBeTruthy();
    expect(screen.queryByText('cyberpunk')).toBeNull();
  });

  it('removing a chip clears prompt_trigger', async () => {
    render(<PromptTriggerTagsCard />);
    fireEvent.click(await screen.findByLabelText('Remove AI'));
    await waitFor(() => expect(updateTag).toHaveBeenCalledWith('1', { prompt_trigger: false }));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/PromptTriggerTagsCard.test.tsx`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

```tsx
// frontend/components/PromptTriggerTagsCard.tsx

/**
 * PromptTriggerTagsCard — Settings → Tags card managing which tags act as
 * "Prompt tags" (tags.prompt_trigger). Assets carrying any of these show
 * the Prompt panel + grid badge; the first one is auto-applied when a
 * prompt is added to an asset.
 */
import { useEffect, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fetchAllTags, updateTag } from '../services/unifiedTagService';
import type { Tag } from '../types';

export function PromptTriggerTagsCard() {
  const { t } = useTranslation();
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch((e) => console.error('fetchAllTags:', e));
  }, []);

  const triggers = allTags.filter((tg) => tg.prompt_trigger);
  const candidates = allTags.filter((tg) => !tg.prompt_trigger);

  const setFlag = async (tag: Tag, value: boolean) => {
    setBusy(String(tag.id));
    try {
      const updated = await updateTag(String(tag.id), { prompt_trigger: value });
      setAllTags((prev) => prev.map((tg) => (String(tg.id) === String(tag.id) ? { ...tg, ...updated } : tg)));
    } catch (err) {
      console.error('updateTag prompt_trigger:', err);
    } finally {
      setBusy(null);
      setAdding(false);
    }
  };

  return (
    <section className="bg-ink-900 border border-ink-800 rounded-xl p-5 mb-4">
      <h3 className="text-sm font-semibold text-ink-200">
        {t('settings.promptTriggerTags.title', 'Prompt Trigger Tags')}
      </h3>
      <p className="text-xs text-ink-500 mt-1 mb-4">
        {t('settings.promptTriggerTags.hint',
          'Assets with any of these tags show the Prompt panel and card badge. The first tag is applied automatically when a prompt is added.')}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {triggers.map((tag) => (
          <span key={String(tag.id)}
            className="inline-flex items-center gap-1.5 text-xs pl-3 pr-1.5 py-1 rounded-full border border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]">
            {tag.name}
            <button
              aria-label={`Remove ${tag.name}`}
              disabled={busy === String(tag.id)}
              onClick={() => setFlag(tag, false)}
              className="w-4 h-4 rounded-full inline-flex items-center justify-center hover:bg-ink-700/60 disabled:opacity-50"
            >
              <X size={10} />
            </button>
          </span>
        ))}
        {adding ? (
          <select
            autoFocus
            onBlur={() => setAdding(false)}
            onChange={(e) => {
              const tag = candidates.find((tg) => String(tg.id) === e.target.value);
              if (tag) setFlag(tag, true);
            }}
            className="bg-ink-800 border border-ink-700 rounded-full text-xs text-ink-300 px-3 py-1 focus:outline-none"
            defaultValue=""
          >
            <option value="" disabled>{t('settings.promptTriggerTags.pick', 'Pick a tag…')}</option>
            {candidates.map((tg) => (
              <option key={String(tg.id)} value={String(tg.id)}>{tg.name}</option>
            ))}
          </select>
        ) : (
          <button onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 text-xs px-3 py-1 rounded-full border border-dashed border-ink-600 text-ink-500 hover:text-[var(--accent-text)] hover:border-[var(--accent-border)] transition-colors">
            <Plus size={11} /> {t('settings.promptTriggerTags.add', 'Add tag')}
          </button>
        )}
      </div>
    </section>
  );
}
```

SettingsView `tags` 分支（~L765）`<TagsSettings` 前加 `<PromptTriggerTagsCard />` + import。
i18n：en `settings.promptTriggerTags` = `{ "title": "Prompt Trigger Tags", "hint": "Assets with any of these tags show the Prompt panel and card badge. The first tag is applied automatically when a prompt is added.", "add": "Add tag", "pick": "Pick a tag…" }`；zh 对应 `{ "title": "Prompt 触发标签", "hint": "带这些标签的素材会显示 Prompt 面板和卡片角标。添加 prompt 时会自动打上第一个标签。", "add": "添加标签", "pick": "选择标签…" }`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/PromptTriggerTagsCard.test.tsx && npm run typecheck`
Expected: 2 PASS、0 type error。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/PromptTriggerTagsCard.tsx frontend/components/PromptTriggerTagsCard.test.tsx frontend/components/SettingsView.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(fe): Settings→Tags 增加 Prompt Trigger Tags 管理卡片"
```

---

### Task 10: 全量验证 + 收尾

**Files:** 无新文件

- [ ] **Step 1: 后端全量相关测试**

```bash
cd backend && uv run pytest tests/services/test_png_prompt_extractor.py tests/test_upload_postprocess_workflow.py tests/test_gen_prompt_translate_plan.py tests/test_tags_prompt_trigger_schema.py -v
```

Expected: 全 PASS。

- [ ] **Step 2: 前端全量测试 + 类型 + lint**

```bash
cd frontend && npm run typecheck && npx vitest run && npm run lint 2>&1 | tail -5
```

Expected: typecheck 0 error；vitest 全 PASS（若有与本改动无关的既有失败，记录并对照 master 确认非本分支引入）；lint 无新增报错。

- [ ] **Step 3: 手动冒烟（可用 Claude 调试测试账号）**

启动 dev（端口看 `.worktree.env`）：上传一张带 A1111 元数据的 PNG → 详情面板 Tags 下方出现折叠 Prompt 行（正/负已填）→ 点开自动打上 AI 标签 → 切中文 + Translate 双栏都翻 → 网格卡片右下角出现角标、悬浮出预览 → Settings → Tags 卡片可增删触发标签。

- [ ] **Step 4: 同步 master 并交付**

```bash
bash scripts/sync-worktree.sh
```

然后用 `/ship` 提 PR（走仓库标准流程：tests / review / VERSION / CHANGELOG）。

---

## Self-Review 记录

- **Spec 覆盖**：mig 384/385（T1）、extractor 负面（T2）、上传写入（T3）、PATCH+translate（T4）、tags 通路（T5）、类型/工具（T6）、面板移位+折叠+自动打标（T7）、角标（T8）、Settings 卡片（T9）。Spec §6.1 的 Send to Canvas 按钮属 Phase 2（spec 注明"Phase 2 前隐藏"），不在本计划。
- **类型一致性**：`PngPromptPair`（T2→T3）、`build_translate_plan`（T4 内部）、`hasPromptData`/`ensureDefaultTriggerTag`（T6→T7/T8）、`PromptSectionProps.onTranslate(lang)` 与页面 `handleTranslatePrompt(lang)` 改签名（T7 Step 5.3）已对齐。
- **占位符**：无 TBD；两处"按实际代码确认"（T5 router 透传、T6 createTag 签名）均给出了判定方法与完成标准。
