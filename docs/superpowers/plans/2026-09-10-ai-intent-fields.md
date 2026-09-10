# AI 意图显式字段 + 快捷指令选择页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 转录 / 总结 / 解析 / 评级从「勾系统标签」改为抓取请求与快捷指令选择页上的显式字段；选择页补齐「搜索即创建」与「选中清空搜索」。

**Architecture:** 请求层新增 `transcribe / summarize / analyze / rating` 四个字段；服务端把三个布尔映射成 `type='system'` 的 `Transcript / Summary / Analyze` 标签 id 并入既有 `tag_ids` 通道，下载链（按标签名派工作流）一行不改；`rating` 在资源建好后由 parse 工作流 / 批量后置任务写 `resources.rating`。临时令牌的选择结果新增 `options`，`format=text&field=<名>` 给快捷指令逐项读值。前端选择页与 FloatingParse 改发字段，不再翻译成标签 id。

**Tech Stack:** FastAPI + pydantic v2 + SQLAlchemy 2.0 async（后端）、DBOS 工作流、Redis；React + Vite + vitest + @testing-library/react（前端）。

**Spec:** `docs/superpowers/specs/2026-09-10-ai-intent-fields-design.md`

## Global Constraints

- 沟通中文；UI 文案英文（选择页按 `lang` 参数双语，沿用文件内既有 `lang === 'zh' ? … : …` 写法）。
- 不加数据库迁移；不改 `backend/app/tasks/download_helpers.py`；不动 `format=text` 的默认输出。
- 意图标签只按 `type = 'system'` 查找，**绝不创建**；缺失只 WARNING 不阻断。
- 新代码禁止 `text()` 裸 SQL；写库走 repository。
- 工作树：`bash scripts/worktree-manager.sh create feat/ai-intent-fields`，建完立刻 `git fetch origin && git rebase --onto origin/master $(git merge-base HEAD master)`（本机 master 带 squash 前提交，见 memory `feedback-worktree-branch-from-origin-master`）。
- 后端测试命令（在 `backend/` 下）：`uv run pytest <path> -q`。前端测试命令（在 `frontend/` 下）：`npx vitest run <path>`。
- 本机（Mac）已知 13 个环境性红测试（distribution 沙箱 URLBlockedError、`.m4a` MIME），CI 是门禁。
- 提交信息格式 `<type>(<scope>): <中文描述>`；PR 用 `gh pr create`，CI 8 项全绿即合并（用户既定规则）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/api/media_fetch_helpers.py` | 请求模型加字段（`AiIntentFields` 基类）；`intent_tag_names` 纯映射；`resolve_intent_tag_ids` 查系统标签；单链路并入意图 id、转发 `rating` |
| `backend/app/repositories/tags_repository.py` | 新方法 `get_system_tag_ids_by_names` |
| `backend/app/workflows/parse.py` | `parse_workflow(rating=…)`；`set_resource_rating` 异步函数 + `set_rating_step` DBOS 步骤 |
| `backend/app/api/media_batch_router.py` | 嵌套的 `_attach_tags_after_save` 提成模块级 `attach_after_save`，并入意图 id 与 rating |
| `backend/app/api/temp_token_router.py` | `SelectionOptions`；Redis `options`；`field=` 单值输出 |
| `backend/tests/test_ai_intent_fields.py` | Task 1–4 的测试 |
| `backend/tests/test_temp_token_selection_options.py` | Task 5 的测试 |
| `frontend/services/parserService.ts` | `FetchOptions` 加三个布尔；`applyIntentFields` |
| `frontend/components/TopicInspiration/FloatingParse.tsx` | 三个按钮改本地布尔状态；EagleTagPicker 隐藏 Pipeline 组 |
| `frontend/components/TopicInspiration/FloatingParse.intents.test.tsx` | Task 6 测试 |
| `frontend/pages/ShortcutsTagsPage.tsx` | 隐藏 Pipeline 组；四项单选 + 全字段保存；选中清空搜索；搜索即创建条 |
| `frontend/pages/ShortcutsTagsPage.test.tsx` | Task 7–8 测试 |

---

### Task 1: 请求字段与纯映射函数

**Files:**
- Modify: `backend/app/api/media_fetch_helpers.py:13`（import）、`:33-63`（两个请求模型）
- Test: `backend/tests/test_ai_intent_fields.py`（新建）

**Interfaces:**
- Produces: `class AiIntentFields(BaseModel)`，字段 `transcribe: bool=False`、`summarize: bool=False`、`analyze: bool=False`、`rating: Optional[int]=None`（0–5，`""`→None）；`MediaFetchRequest` 与 `BatchFetchRequest` 改为继承它。`INTENT_TAG_NAMES: dict[str, str]`；`def intent_tag_names(*, transcribe: bool, summarize: bool, analyze: bool) -> list[str]`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_ai_intent_fields.py
"""AI 意图从标签改显式字段（spec 2026-09-10-ai-intent-fields-design.md）。"""

import pytest
from pydantic import ValidationError


def test_fetch_request_accepts_intent_fields_with_string_coercion():
    from app.api.media_fetch_helpers import MediaFetchRequest

    req = MediaFetchRequest(
        url="https://v.douyin.com/x/", transcribe="1", summarize="false", analyze=True, rating="4"
    )
    assert (req.transcribe, req.summarize, req.analyze, req.rating) == (True, False, True, 4)


def test_fetch_request_defaults_and_blank_rating():
    from app.api.media_fetch_helpers import BatchFetchRequest, MediaFetchRequest

    single = MediaFetchRequest(url="https://v.douyin.com/x/", rating="")
    batch = BatchFetchRequest(urls=["https://v.douyin.com/x/"])
    for req in (single, batch):
        assert (req.transcribe, req.summarize, req.analyze, req.rating) == (False, False, False, None)


def test_fetch_request_rejects_out_of_range_rating():
    from app.api.media_fetch_helpers import MediaFetchRequest

    with pytest.raises(ValidationError):
        MediaFetchRequest(url="https://v.douyin.com/x/", rating=6)


@pytest.mark.parametrize(
    "flags, expected",
    [
        ((False, False, False), []),
        ((True, False, False), ["Transcript"]),
        ((False, True, False), ["Summary"]),
        ((False, False, True), ["Analyze"]),
        ((True, True, True), ["Transcript", "Summary", "Analyze"]),
    ],
)
def test_intent_tag_names_mapping(flags, expected):
    from app.api.media_fetch_helpers import intent_tag_names

    t, s, a = flags
    assert intent_tag_names(transcribe=t, summarize=s, analyze=a) == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q`
Expected: FAIL —— `ValidationError`（多余字段被拒或字段缺失）与 `ImportError: cannot import name 'intent_tag_names'`。

- [ ] **Step 3: 最小实现**

`backend/app/api/media_fetch_helpers.py` 第 13 行 import 改为：

```python
from pydantic import BaseModel, Field, field_validator
```

在 `_coerce_str_to_list` 之后、`class MediaFetchRequest` 之前插入：

```python
INTENT_TAG_NAMES: dict[str, str] = {
    "transcribe": "Transcript",
    "summarize": "Summary",
    "analyze": "Analyze",
}


class AiIntentFields(BaseModel):
    """显式 AI 意图（spec 2026-09-10）。三个布尔映射到 Pipeline 组的系统标签
    Transcript / Summary / Analyze；rating 写 resources.rating。快捷指令传的是
    文本，所以 "1"/"0"/"true"/"false" 由 pydantic 默认强转，空串 rating 视为无。"""

    transcribe: bool = False
    summarize: bool = False
    analyze: bool = False
    rating: Optional[int] = Field(None, ge=0, le=5)

    @field_validator("rating", mode="before")
    @classmethod
    def _blank_rating_is_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v


def intent_tag_names(*, transcribe: bool, summarize: bool, analyze: bool) -> list[str]:
    """(transcribe, summarize, analyze) → 需要挂上的系统标签英文名，顺序固定。"""
    flags = {"transcribe": transcribe, "summarize": summarize, "analyze": analyze}
    return [INTENT_TAG_NAMES[key] for key, on in flags.items() if on]
```

把 `class MediaFetchRequest(BaseModel):` 改为 `class MediaFetchRequest(AiIntentFields):`，`class BatchFetchRequest(BaseModel):` 改为 `class BatchFetchRequest(AiIntentFields):`。其余字段不动。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q`
Expected: 8 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/media_fetch_helpers.py backend/tests/test_ai_intent_fields.py
git commit -m "feat(media): 抓取请求新增 transcribe/summarize/analyze/rating 显式字段与意图→系统标签映射"
```

---

### Task 2: 只查系统标签的解析器

**Files:**
- Modify: `backend/app/repositories/tags_repository.py`（在 `get_tag_by_name` 方法前加新方法，约 `:298`）
- Modify: `backend/app/api/media_fetch_helpers.py`（在 `resolve_tag_names_to_ids` 之后）
- Test: `backend/tests/test_ai_intent_fields.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `intent_tag_names`。
- Produces: `TagsRepository.get_system_tag_ids_by_names(self, names: List[str]) -> Dict[str, int]`；`async def resolve_intent_tag_ids(*, transcribe: bool, summarize: bool, analyze: bool) -> list[str]`（返回 str id 列表，顺序同 `intent_tag_names`，缺失跳过）。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_ai_intent_fields.py`：

```python
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_resolve_intent_tag_ids_uses_system_lookup_and_skips_missing(caplog):
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_system_tag_ids_by_names = AsyncMock(return_value={"Transcript": 11, "Analyze": 33})
    repo.create_tag = AsyncMock()
    with patch.object(h, "get_tags_repository", return_value=repo):
        ids = await h.resolve_intent_tag_ids(transcribe=True, summarize=True, analyze=True)

    assert ids == ["11", "33"]
    repo.get_system_tag_ids_by_names.assert_awaited_once_with(["Transcript", "Summary", "Analyze"])
    repo.create_tag.assert_not_called()
    assert "Summary" in caplog.text


@pytest.mark.asyncio
async def test_resolve_intent_tag_ids_no_flags_no_query():
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_system_tag_ids_by_names = AsyncMock()
    with patch.object(h, "get_tags_repository", return_value=repo):
        assert await h.resolve_intent_tag_ids(transcribe=False, summarize=False, analyze=False) == []
    repo.get_system_tag_ids_by_names.assert_not_called()
```

`caplog` 与 loguru：文件顶部（import 之后）加 loguru 官方文档的 fixture 覆盖，让 loguru 输出进 caplog：

```python
from loguru import logger as _loguru


@pytest.fixture
def caplog(caplog):
    handler_id = _loguru.add(caplog.handler, format="{message}", level="WARNING")
    yield caplog
    _loguru.remove(handler_id)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q -k resolve_intent`
Expected: FAIL —— `AttributeError: module ... has no attribute 'resolve_intent_tag_ids'`。

- [ ] **Step 3: 最小实现**

`backend/app/repositories/tags_repository.py`，在 `async def get_tag_by_name(` 之前插入：

```python
    async def get_system_tag_ids_by_names(self, names: List[str]) -> Dict[str, int]:
        """英文名 → id，只看 ``type = 'system'`` 行，永不创建。

        意图字段（transcribe / summarize / analyze）映射到 Pipeline 组的
        Transcript / Summary / Analyze；mig 220 的约定是只匹配系统类型，
        同名用户标签不算数。"""
        if not names:
            return {}
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(Tags.name, Tags.id).where(
                        Tags.type == "system", Tags.name.in_(list(names))
                    )
                )
            ).all()
        return {str(name): int(tag_id) for name, tag_id in rows}
```

`backend/app/api/media_fetch_helpers.py`，在 `resolve_tag_names_to_ids` 之后插入：

```python
async def resolve_intent_tag_ids(
    *, transcribe: bool, summarize: bool, analyze: bool
) -> list[str]:
    """意图布尔 → Pipeline 系统标签 id（str）。只查 type='system'，绝不创建；
    查不到说明种子缺失（部署问题），WARNING 后跳过，不阻断抓取。"""
    names = intent_tag_names(transcribe=transcribe, summarize=summarize, analyze=analyze)
    if not names:
        return []
    found = await get_tags_repository().get_system_tag_ids_by_names(names)
    missing = [n for n in names if n not in found]
    if missing:
        logger.warning(
            f"[Fetch] intent system tags missing (seed problem, skipped): {missing}"
        )
    return [str(found[n]) for n in names if n in found]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q`
Expected: 10 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/repositories/tags_repository.py backend/app/api/media_fetch_helpers.py backend/tests/test_ai_intent_fields.py
git commit -m "feat(tags): 意图布尔解析为 Pipeline 系统标签 id，只查 system 类型、缺失只告警"
```

---

### Task 3: 单链路接线（并入意图 id + rating 步骤）

**Files:**
- Modify: `backend/app/api/media_fetch_helpers.py`（`handle_media_fetch_dispatch` 内 `effective_tag_ids` 构造处 `:355-370`；`dbos_workflow_kwargs` 处 `:500-516`）
- Modify: `backend/app/workflows/parse.py`（`attach_tags_step` 之后新增 `set_resource_rating` / `set_rating_step`，约 `:522`；`parse_workflow` 签名 `:607-618`；调用点 `:692-693` 之后）
- Test: `backend/tests/test_ai_intent_fields.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `resolve_intent_tag_ids`。
- Produces: `async def set_resource_rating(resource_id: str, rating: int) -> bool`（parse.py）；`parse_workflow(..., rating: Optional[int] = None)`。

- [ ] **Step 1: 写失败测试**

追加：

```python
@pytest.mark.asyncio
async def test_set_resource_rating_writes_via_repository():
    from app.workflows import parse as parse_mod

    repo = MagicMock()
    repo.update_resource = AsyncMock(return_value={"id": 7, "rating": 4})
    with patch("app.repositories.resources_repository.ResourcesRepository", return_value=repo):
        assert await parse_mod.set_resource_rating("7", 4) is True
    repo.update_resource.assert_awaited_once_with("7", {"rating": 4})


@pytest.mark.asyncio
async def test_set_resource_rating_swallows_failure():
    from app.workflows import parse as parse_mod

    repo = MagicMock()
    repo.update_resource = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.repositories.resources_repository.ResourcesRepository", return_value=repo):
        assert await parse_mod.set_resource_rating("7", 4) is False


def test_single_fetch_path_is_wired_for_intents_and_rating():
    """源码钉：单链路必须把意图 id 并入 effective_tag_ids，并把 rating 转发给 parse_workflow。
    parse_workflow 被 @DBOS.workflow() 包着，inspect 未必能取到原函数源码，所以直接读文件。"""
    import inspect
    from pathlib import Path

    from app.api import media_fetch_helpers as h
    from app.workflows import parse as parse_mod

    src = inspect.getsource(h.handle_media_fetch_dispatch)
    assert "await resolve_intent_tag_ids(" in src
    assert '"rating": request.rating' in src

    wf_text = Path(parse_mod.__file__).read_text(encoding="utf-8")
    assert "rating: Optional[int] = None," in wf_text
    assert "set_rating_step(resource_id=str(resource_id), rating=int(rating))" in wf_text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q -k "rating or wired"`
Expected: FAIL —— `AttributeError: ... 'set_resource_rating'` 与源码钉断言失败。

- [ ] **Step 3: 实现**

`backend/app/workflows/parse.py`，紧跟 `attach_tags_step` 定义之后：

```python
async def set_resource_rating(resource_id: str, rating: int) -> bool:
    """把抓取请求带来的评级写到 resources.rating。失败只告警——评级是装饰
    字段，不该让一次成功的解析变红。"""
    from app.repositories.resources_repository import ResourcesRepository

    try:
        await ResourcesRepository().update_resource(resource_id, {"rating": rating})
        return True
    except Exception as e:
        logger.warning(f"[parse] set_resource_rating failed for {resource_id}: {e}")
        return False


@DBOS.step()
def set_rating_step(*, resource_id: str, rating: int) -> bool:
    """DBOS 步骤外壳；逻辑在 set_resource_rating 便于直接测。"""
    return asyncio.run(set_resource_rating(resource_id, rating))
```

`parse_workflow` 签名在 `flow_id: Optional[str] = None,` 之后加一行：

```python
    rating: Optional[int] = None,
```

在 `attach_tags_step(...)` 调用（`if tag_ids and resource_id:` 块）之后加：

```python
    # 3a'. 抓取请求带的评级（spec 2026-09-10）。None = 调用方没给，不写。
    if rating is not None and resource_id:
        set_rating_step(resource_id=str(resource_id), rating=int(rating))
```

`backend/app/api/media_fetch_helpers.py` 的 `handle_media_fetch_dispatch`，在 `if tags:` 那段 `try/except` 之后、`has_cookie = False` 之前插入：

```python
    # 显式意图 → Pipeline 系统标签 id，走同一条 tag_ids 通道，下载链不用改。
    try:
        for tid in await resolve_intent_tag_ids(
            transcribe=request.transcribe,
            summarize=request.summarize,
            analyze=request.analyze,
        ):
            if tid not in effective_tag_ids:
                effective_tag_ids.append(tid)
    except Exception as e:
        logger.warning(f"[Fetch] intent tag resolution failed (non-fatal): {e}")
```

`dbos_workflow_kwargs` 字典里 `"tag_ids": effective_tag_ids,` 之后加：

```python
            "rating": request.rating,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py tests/test_douyin_unified_parse_chain.py -q`
Expected: 全部 passed（后者是既有的 parse 链测试，确认签名改动没打破它）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/media_fetch_helpers.py backend/app/workflows/parse.py backend/tests/test_ai_intent_fields.py
git commit -m "feat(media): 单链路并入意图标签 id，parse 工作流新增 rating 步骤"
```

---

### Task 4: 批量链路接线

**Files:**
- Modify: `backend/app/api/media_batch_router.py`（`:13-14` import；`:141-184` 嵌套函数提为模块级；`fetch_videos_batch` 顶部解析意图 id）
- Test: `backend/tests/test_ai_intent_fields.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `resolve_intent_tag_ids`、既有 `resolve_and_attach_tags`。
- Produces: `async def attach_after_save(platform_id: str, tag_ids: list[str] | None, tag_names: list[str] | None, user_id: str, *, rating: int | None, attempts: int = 10, poll_seconds: float = 1.0) -> bool`（模块级，`tag_ids` 已含意图 id）。

- [ ] **Step 1: 写失败测试**

追加：

```python
@pytest.mark.asyncio
async def test_batch_attach_after_save_attaches_tags_and_rating():
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(side_effect=[None, {"id": 99}])
    res_repo.update_resource = AsyncMock(return_value={})
    tags_repo = MagicMock()
    tags_repo.bulk_add_tags_to_resource = AsyncMock()
    with (
        patch("app.repositories.resources_repository.ResourcesRepository", return_value=res_repo),
        patch.object(b, "get_tags_repository", return_value=tags_repo),
        patch.object(b, "resolve_and_attach_tags", new=AsyncMock()) as attach_names,
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ok = await b.attach_after_save("pid1", ["11", "33"], ["cats"], "u1", rating=5)

    assert ok is True
    tags_repo.bulk_add_tags_to_resource.assert_awaited_once_with("99", ["11", "33"], source="manual")
    attach_names.assert_awaited_once_with("99", ["cats"], "u1")
    res_repo.update_resource.assert_awaited_once_with("99", {"rating": 5})


@pytest.mark.asyncio
async def test_batch_attach_after_save_gives_up_after_attempts():
    from app.api import media_batch_router as b

    res_repo = MagicMock()
    res_repo.get_resource_by_platform_id = AsyncMock(return_value=None)
    with (
        patch("app.repositories.resources_repository.ResourcesRepository", return_value=res_repo),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ok = await b.attach_after_save("pid1", ["11"], None, "u1", rating=None, attempts=3)
    assert ok is False
    assert res_repo.get_resource_by_platform_id.await_count == 3


def test_batch_route_is_wired_for_intents():
    import inspect

    from app.api import media_batch_router as b

    src = inspect.getsource(b.fetch_videos_batch)
    assert "await resolve_intent_tag_ids(" in src
    assert "attach_after_save," in src
    assert "rating=request.rating" in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q -k batch`
Expected: FAIL —— `AttributeError: ... 'attach_after_save'`。

- [ ] **Step 3: 实现**

`backend/app/api/media_batch_router.py` import 段把 `resolve_and_attach_tags,` 后加 `resolve_intent_tag_ids,`（同一个 `from app.api.media_fetch_helpers import (...)`）。

在 `@router.post("/fetch/batch"...)` 之前加模块级函数：

```python
async def attach_after_save(
    platform_id: str,
    tag_ids: list[str] | None,
    tag_names: list[str] | None,
    user_id: str,
    *,
    rating: int | None,
    attempts: int = 10,
    poll_seconds: float = 1.0,
) -> bool:
    """批量抓取是 background 保存资源，所以标签 / 评级只能等资源出现后再挂。
    ``tag_ids`` 已含意图映射出的系统标签 id。返回是否在预算内等到了资源。"""
    import asyncio

    from app.repositories.resources_repository import ResourcesRepository

    res_repo = ResourcesRepository()
    for _ in range(attempts):
        resource = await res_repo.get_resource_by_platform_id(platform_id)
        if resource:
            rid = str(resource["id"])
            if tag_ids:
                await get_tags_repository().bulk_add_tags_to_resource(
                    rid, tag_ids, source="manual"
                )
            if tag_names:
                await resolve_and_attach_tags(rid, tag_names, user_id)
            if rating is not None:
                await res_repo.update_resource(rid, {"rating": rating})
            logger.info(f"Tags/rating attached to resource {rid} for {platform_id}")
            return True
        await asyncio.sleep(poll_seconds)
    logger.warning(f"Timeout attaching tags for {platform_id}")
    return False
```

在 `fetch_videos_batch` 里、`points_service = PointsService()` 之前加：

```python
    intent_ids = await resolve_intent_tag_ids(
        transcribe=request.transcribe,
        summarize=request.summarize,
        analyze=request.analyze,
    )
    effective_tag_ids = list(request.tag_ids or [])
    for tid in intent_ids:
        if tid not in effective_tag_ids:
            effective_tag_ids.append(tid)
```

把原来 `if request.tag_ids or request.tags:` 起到 `background_tasks.add_task(_attach_tags_after_save, ...)` 结束的整段（含嵌套函数定义）替换为：

```python
                    if effective_tag_ids or request.tags or request.rating is not None:
                        background_tasks.add_task(
                            attach_after_save,
                            platform_id,
                            effective_tag_ids,
                            request.tags,
                            auth.user_id,
                            rating=request.rating,
                        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ai_intent_fields.py -q && uv run ruff check app/api/media_batch_router.py app/api/media_fetch_helpers.py app/workflows/parse.py app/repositories/tags_repository.py`
Expected: 全部 passed；ruff 无输出。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/media_batch_router.py backend/tests/test_ai_intent_fields.py
git commit -m "feat(media): 批量链路并入意图标签 id 并写 rating，后置挂载提为模块级可测函数"
```

---

### Task 5: 临时令牌选择结果加选项与 field 单值输出

**Files:**
- Modify: `backend/app/api/temp_token_router.py`（`:23` import；`:45-50` 两个模型；`:131-148` `save_selection`；`:238-256` `get_selection`）
- Test: `backend/tests/test_temp_token_selection_options.py`（新建）

**Interfaces:**
- Produces: `class SelectionOptions(BaseModel)`（`rating: Optional[int]=None` 0–5、`transcribe/summarize/analyze: bool=False`）；`SelectionRequest(SelectionOptions)` 与 `SelectionResponse(SelectionOptions)` 各带 `tags: list[str]`；`SELECTION_FIELDS = ("rating", "transcribe", "summarize", "analyze")`；`get_selection(token, format="json", field=None)`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_temp_token_selection_options.py
"""快捷指令选择结果携带评级 / 转录 / 总结 / 解析（spec 2026-09-10）。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TOKEN_DATA = {"user_id": "u1", "scopes": ["tags:read", "tags:write"], "selection": []}


def _redis(ttl: int = 120):
    r = MagicMock()
    r.ttl = AsyncMock(return_value=ttl)
    r.setex = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_save_selection_persists_options_alongside_tags():
    from app.api.temp_token_router import SelectionRequest, save_selection

    redis = _redis()
    with (
        patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=dict(TOKEN_DATA))),
        patch("app.api.temp_token_router.get_async_redis", new=AsyncMock(return_value=redis)),
    ):
        await save_selection(
            "tok", SelectionRequest(tags=["cats"], rating=3, transcribe=True, analyze=True)
        )

    _key, _ttl, raw = redis.setex.await_args.args
    stored = json.loads(raw)
    assert stored["selection"] == ["cats"]
    assert stored["options"] == {"rating": 3, "transcribe": True, "summarize": False, "analyze": True}


@pytest.mark.asyncio
async def test_get_selection_json_returns_options_and_legacy_defaults():
    from app.api.temp_token_router import get_selection

    legacy = {**TOKEN_DATA, "selection": ["a", "b"]}  # 旧令牌：没有 options 键
    with patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=legacy)):
        resp = await get_selection("tok", format="json", field=None)
    assert resp.model_dump() == {
        "tags": ["a", "b"], "rating": None, "transcribe": False, "summarize": False, "analyze": False
    }


@pytest.mark.asyncio
async def test_get_selection_text_default_is_unchanged_tag_csv():
    from app.api.temp_token_router import get_selection

    data = {**TOKEN_DATA, "selection": ["a", "b"], "options": {"rating": 5, "transcribe": True}}
    with patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=data)):
        resp = await get_selection("tok", format="text", field=None)
    assert resp.body == b"a,b"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, expected",
    [("rating", b"5"), ("transcribe", b"1"), ("summarize", b"0"), ("analyze", b"0")],
)
async def test_get_selection_text_field_returns_single_value(field, expected):
    from app.api.temp_token_router import get_selection

    data = {**TOKEN_DATA, "selection": ["a"], "options": {"rating": 5, "transcribe": True}}
    with patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=data)):
        resp = await get_selection("tok", format="text", field=field)
    assert resp.body == expected


@pytest.mark.asyncio
async def test_get_selection_text_rating_none_reads_as_zero():
    from app.api.temp_token_router import get_selection

    with patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=dict(TOKEN_DATA))):
        resp = await get_selection("tok", format="text", field="rating")
    assert resp.body == b"0"


@pytest.mark.asyncio
async def test_get_selection_rejects_unknown_field():
    from app.api.temp_token_router import get_selection

    with patch("app.api.temp_token_router._get_token_data", new=AsyncMock(return_value=dict(TOKEN_DATA))):
        with pytest.raises(HTTPException) as exc:
            await get_selection("tok", format="text", field="mood")
    assert exc.value.status_code == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_temp_token_selection_options.py -q`
Expected: FAIL —— `SelectionRequest` 不接受 `rating`（pydantic 默认忽略额外字段则表现为 `stored["options"]` KeyError）、`get_selection() got an unexpected keyword argument 'field'`。

- [ ] **Step 3: 实现**

`backend/app/api/temp_token_router.py` 第 23 行改为 `from pydantic import BaseModel, Field`。

把原来的 `SelectionRequest` / `SelectionResponse` 两个类替换为：

```python
class SelectionOptions(BaseModel):
    """选择页上的四项单选（spec 2026-09-10）。旧令牌没有 options 键时按此默认读。"""

    rating: Optional[int] = Field(None, ge=0, le=5)
    transcribe: bool = False
    summarize: bool = False
    analyze: bool = False


class SelectionRequest(SelectionOptions):
    tags: list[str]


class SelectionResponse(SelectionOptions):
    tags: list[str]


SELECTION_FIELDS = ("rating", "transcribe", "summarize", "analyze")


def _options_from(data: dict) -> SelectionOptions:
    return SelectionOptions.model_validate(data.get("options") or {})


def _field_as_text(options: SelectionOptions, field: str) -> str:
    """快捷指令逐项读值：rating → "0"–"5"（None 记 0），布尔 → "1"/"0"。"""
    if field == "rating":
        return str(options.rating or 0)
    return "1" if getattr(options, field) else "0"
```

（`Optional` 已需要：文件顶部 `import json` 后加 `from typing import Optional`。）

`save_selection` 里 `data["selection"] = request.tags` 之后加一行：

```python
    data["options"] = request.model_dump(exclude={"tags"})
```

`get_selection` 改为：

```python
@router.get("/{token}/selection")
async def get_selection(
    token: str,
    format: str = Query("json", description="Response format: json or text"),
    field: Optional[str] = Query(
        None,
        description="With format=text: return ONE option as plain text — "
        "rating (0-5) | transcribe | summarize | analyze (1/0).",
    ),
):
    """
    Retrieve saved tag selection. Called by Shortcuts after web view closes.

    Use ?format=text to get comma-separated plain text (e.g. "tag1,tag2,tag3").
    Use ?format=text&field=rating (or transcribe / summarize / analyze) to get
    that single option as plain text, so Shortcuts needs no JSON parsing.
    """
    from fastapi.responses import PlainTextResponse

    data = await _get_token_data(token)
    tags = data.get("selection") or []
    options = _options_from(data)

    if format == "text":
        if field is None:
            return PlainTextResponse(",".join(tags))
        if field not in SELECTION_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown field '{field}'; expected one of {', '.join(SELECTION_FIELDS)}",
            )
        return PlainTextResponse(_field_as_text(options, field))

    return SelectionResponse(tags=tags, **options.model_dump())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_temp_token_selection_options.py tests/test_temp_token_tag_conflict.py -q`
Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/temp_token_router.py backend/tests/test_temp_token_selection_options.py
git commit -m "feat(shortcuts): 选择结果携带评级/转录/总结/解析，format=text&field= 逐项单值输出"
```

---

### Task 6: 前端 parserService 与 FloatingParse 改发布尔意图

**Files:**
- Modify: `frontend/services/parserService.ts:53-57`（`FetchOptions`）、`:95-105` 与 `:136-145`（两个 body 构造）
- Modify: `frontend/components/TopicInspiration/FloatingParse.tsx:29-33`、`:76-92`、`:107-125`、`:236-262`
- Test: `frontend/components/TopicInspiration/FloatingParse.intents.test.tsx`（新建）

**Interfaces:**
- Produces: `FetchOptions` 新增 `transcribe?: boolean; summarize?: boolean; analyze?: boolean;`；`export const applyIntentFields = (body: Record<string, unknown>, options: FetchOptions) => void`（只在 true 时写入）。
- 意图按钮加 `data-testid="ai-intent-transcribe|summarize|analyze"`。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/components/TopicInspiration/FloatingParse.intents.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f?: string) => f ?? _k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
const parseShareLink = vi.fn().mockResolvedValue({ title: 'ok' });
vi.mock('../../services/parserService', () => ({
  parseShareLink: (...a: unknown[]) => parseShareLink(...a),
  parseBatchLinks: vi.fn(),
  getSodaPlaylist: vi.fn(),
  downloadSodaTracks: vi.fn(),
}));
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([
    { id: '1', name: 'Transcript', type: 'system', group_name: 'Pipeline' },
    { id: '2', name: 'Cats', type: 'user', group_name: 'Animals' },
  ]),
  createTag: vi.fn(),
}));
vi.mock('../EagleTagPicker', () => ({
  EagleTagPicker: ({ allTags }: { allTags: Array<{ name: string }> }) => (
    <div data-testid="picker">{allTags.map((t) => t.name).join(',')}</div>
  ),
}));

import { FloatingParse } from './FloatingParse';

describe('FloatingParse AI intents', () => {
  it('sends transcribe/analyze booleans instead of tag ids and hides Pipeline tags', async () => {
    render(<FloatingParse open onOpenChange={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/paste/i), {
      target: { value: 'https://v.douyin.com/abc/' },
    });
    fireEvent.click(screen.getByTestId('ai-intent-transcribe'));
    fireEvent.click(screen.getByTestId('ai-intent-analyze'));
    await waitFor(() => expect(screen.getByTestId('picker').textContent).toBe('Cats'));
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    await waitFor(() => expect(parseShareLink).toHaveBeenCalled());
    const [, opts] = parseShareLink.mock.calls[0];
    expect(opts).toMatchObject({ transcribe: true, analyze: true });
    expect(opts.summarize).toBeFalsy();
    expect(opts.tag_ids).toEqual([]);
  });
});
```

（Analyze 有两个按钮：意图按钮靠 `data-testid`，解析按钮是唯一带 accessible name "Analyze" 的 `<button>`；意图按钮改成 `aria-label` 为 `AI intent: Analyze` 以免撞名，见下面实现。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/TopicInspiration/FloatingParse.intents.test.tsx`
Expected: FAIL —— `Unable to find an element by: [data-testid="ai-intent-transcribe"]`。

- [ ] **Step 3: 实现**

`frontend/services/parserService.ts`：

```ts
export interface FetchOptions {
  video_bool?: boolean;
  cover_bool?: boolean;
  tag_ids?: string[];
  /** AI intents (spec 2026-09-10): sent only when true; mapped server-side to Pipeline system tags. */
  transcribe?: boolean;
  summarize?: boolean;
  analyze?: boolean;
}

/** Copy the true-valued AI intents onto a request body (false/undefined are omitted). */
export const applyIntentFields = (body: Record<string, unknown>, options: FetchOptions) => {
  for (const key of ['transcribe', 'summarize', 'analyze'] as const) {
    if (options[key]) body[key] = true;
  }
};
```

`parseShareLink` 的 `if (options.tag_ids?.length) { body.tag_ids = options.tag_ids; }` 之后加 `applyIntentFields(body, options);`；`parseBatchLinks` 的对应位置加 `applyIntentFields(batchBody, options);`。

`FloatingParse.tsx`：

把 `AI_INTENTS` 常量替换为：

```tsx
type IntentKey = 'transcribe' | 'summarize' | 'analyze';
const AI_INTENTS: ReadonlyArray<{ key: IntentKey; label: string; Icon: typeof Mic }> = [
  { key: 'transcribe', label: 'Transcript', Icon: Mic },
  { key: 'summarize', label: 'Summary', Icon: FileText },
  { key: 'analyze', label: 'Analyze', Icon: Eye },
];
const NO_INTENTS: Record<IntentKey, boolean> = { transcribe: false, summarize: false, analyze: false };
```

在 `const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);` 之后加：

```tsx
  const [intents, setIntents] = useState<Record<IntentKey, boolean>>(NO_INTENTS);
  // Pipeline 组的三枚系统标签由上面的意图按钮承载，不再当普通标签给用户勾。
  const pickerTags = useMemo(() => allTags.filter((tg) => tg.group_name !== 'Pipeline'), [allTags]);
```

删除 `aiTagId` 与 `toggleAiIntent` 两个函数（`// --- AI intent helpers ---` 整段）。

两个 parse 调用的 `tag_ids: selectedTagIds,` 之后各加一行 `...intents,`（`parseBatchLinks` 与 `parseShareLink` 都加）。

意图按钮 JSX 替换为：

```tsx
                  {AI_INTENTS.map(({ key, label, Icon }) => {
                    const active = intents[key];
                    return (
                      <button
                        key={key}
                        type="button"
                        data-testid={`ai-intent-${key}`}
                        aria-label={`AI intent: ${label}`}
                        aria-pressed={active}
                        onClick={() => setIntents((prev) => ({ ...prev, [key]: !prev[key] }))}
                        className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded-md text-[11px] font-medium border transition-colors ${
                          active
                            ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                            : 'bg-island-2 text-content-2 border-line'
                        }`}
                      >
                        <Icon size={12} />
                        {label}
                      </button>
                    );
                  })}
```

`<EagleTagPicker ... allTags={allTags}` 改为 `allTags={pickerTags}`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/TopicInspiration/ && npx tsc --noEmit -p tsconfig.json`
Expected: 该目录全部 passed；tsc 无错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/services/parserService.ts frontend/components/TopicInspiration/FloatingParse.tsx frontend/components/TopicInspiration/FloatingParse.intents.test.tsx
git commit -m "feat(parse): FloatingParse 意图按钮改发 transcribe/summarize/analyze 布尔，标签选择器隐藏 Pipeline 组"
```

---

### Task 7: 选择页：隐藏 Pipeline 组、四项单选、全字段保存、选中清空搜索

**Files:**
- Modify: `frontend/pages/ShortcutsTagsPage.tsx`（状态 `:49-60`；过滤 `:114-159`；`toggle` `:284-322`；渲染：搜索框之后 `:370`、标签区 `:412`）
- Test: `frontend/pages/ShortcutsTagsPage.test.tsx`（新建）

**Interfaces:**
- Produces（页面内部）：`type Options = { rating: number | null; transcribe: boolean; summarize: boolean; analyze: boolean }`；`const saveSelection = (tags: Set<string>, opts: Options) => void`（单次 POST 全字段）；`const setOption = <K extends keyof Options>(key: K, value: Options[K]) => void`。
- 单选控件 `data-testid`：`opt-rating-0..5`、`opt-transcribe-0/1`、`opt-summarize-0/1`、`opt-analyze-0/1`。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/pages/ShortcutsTagsPage.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../components/ui', () => ({
  UiSelect: (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} />,
}));

const TAGS = [
  { id: '1', name: 'Transcript', name_zh: '转录', color: '#000', group_id: 'g1', group_name: 'Pipeline', media_count: 9, type: 'system' },
  { id: '2', name: 'Cats', name_zh: '猫', color: '#000', group_id: 'g2', group_name: 'Animals', media_count: 3, type: 'user' },
  { id: '3', name: 'Dogs', name_zh: '狗', color: '#000', group_id: 'g2', group_name: 'Animals', media_count: 1, type: 'user' },
];

function mockFetch() {
  const calls: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith('/tags?enabled_only=true')) {
      return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
    }
    return new Response(JSON.stringify({ success: true }), { status: 200 });
  }));
  return calls;
}

async function renderPage() {
  window.history.replaceState({}, '', '/?token=tok&lang=zh');
  const { ShortcutsTagsPage } = await import('./ShortcutsTagsPage');
  render(<ShortcutsTagsPage />);
  await screen.findByText('猫');
}

describe('ShortcutsTagsPage options', () => {
  beforeEach(() => vi.resetModules());

  it('hides Pipeline-group tags', async () => {
    mockFetch();
    await renderPage();
    // 选项行的「转录」是文字标签，不是按钮；标签芯片的可访问名是「转录 9」（名 + 计数）。
    expect(screen.queryByRole('button', { name: /转录\s*9/ })).toBeNull();
    expect(screen.queryByText('Pipeline')).toBeNull();
  });

  it('saves options together with tags in one POST', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByText('猫'));
    fireEvent.click(screen.getByTestId('opt-transcribe-1'));
    fireEvent.click(screen.getByTestId('opt-rating-4'));
    await waitFor(() => {
      const saves = calls.filter((c) => c.url.endsWith('/selection'));
      expect(saves.at(-1)?.body).toEqual({
        tags: ['Cats'], rating: 4, transcribe: true, summarize: false, analyze: false,
      });
    });
  });

  it('clears the search box after selecting a search hit', async () => {
    mockFetch();
    await renderPage();
    const box = screen.getByPlaceholderText('搜索标签...') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '狗' } });
    expect(screen.queryByText('猫')).toBeNull();
    fireEvent.click(screen.getByText('狗'));
    expect(box.value).toBe('');
    expect(screen.getByText('猫')).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run pages/ShortcutsTagsPage.test.tsx`
Expected: 三个用例 FAIL（转录仍渲染；`opt-transcribe-1` 找不到；搜索框未清空）。

- [ ] **Step 3: 实现**

状态区（`const [createError, ...]` 之后）加：

```tsx
  type Options = { rating: number | null; transcribe: boolean; summarize: boolean; analyze: boolean };
  const DEFAULT_OPTIONS: Options = { rating: null, transcribe: false, summarize: false, analyze: false };
  const [options, setOptions] = useState<Options>(DEFAULT_OPTIONS);
```

过滤：`filteredTags` 的 `useMemo` 开头把 `tags` 换成不含 Pipeline 组的列表——在 `const query = ...` 之前加：

```tsx
  // Pipeline 组（Transcript / Summary / Analyze）由下方四项单选承载，不再当标签选。
  const pickableTags = useMemo(() => tags.filter((t) => t.group_name !== 'Pipeline'), [tags]);
```

然后 `filteredTags` 与 `topTags` 两个 `useMemo` 里的 `tags` 全部改为 `pickableTags`，依赖数组同步改（`tagGroups` 保留用 `tags`，创建表单的分组下拉仍应列出所有组）。

`toggle` 替换为（保存逻辑抽出，标签与选项共用一次 POST）：

```tsx
  // 单次 POST 全字段：标签 + 四项单选。空标签也照发，后端以此清空。
  const saveSelection = useCallback((nextTags: Set<string>, nextOptions: Options) => {
    if (!token) return;
    setSaveStatus('saving');
    fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/selection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: Array.from(nextTags), ...nextOptions }),
    })
      .then(() => {
        setSaveStatus('saved');
        setTimeout(() => setSaveStatus('idle'), 1500);
      })
      .catch((err) => {
        console.error('Failed to save selection:', err);
        setSaveStatus('idle');
      });
  }, [token]);

  const toggle = useCallback((tagName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tagName)) next.delete(tagName);
      else next.add(tagName);
      saveSelection(next, options);
      return next;
    });
    // 从搜索结果里点中即清空搜索，列表回到全量视图。
    setSearch('');
  }, [options, saveSelection]);

  const setOption = useCallback(<K extends keyof Options>(key: K, value: Options[K]) => {
    setOptions((prev) => {
      const next = { ...prev, [key]: value };
      saveSelection(selected, next);
      return next;
    });
  }, [selected, saveSelection]);
```

渲染：在 `{/* Create tag form */}` 之前插入四排单选：

```tsx
      {/* Processing options — replaces picking Transcript/Summary/Analyze as tags */}
      <div className="px-4 py-3 border-b border-ink-800 space-y-2.5">
        {([
          { key: 'rating', label: lang === 'zh' ? '评级' : 'Rating',
            choices: [null, 1, 2, 3, 4, 5].map((v) => ({ value: v, text: v === null ? (lang === 'zh' ? '无' : 'None') : '★'.repeat(v) })) },
          { key: 'transcribe', label: lang === 'zh' ? '转录' : 'Transcribe', choices: [{ value: false, text: lang === 'zh' ? '否' : 'No' }, { value: true, text: lang === 'zh' ? '是' : 'Yes' }] },
          { key: 'summarize', label: lang === 'zh' ? '总结' : 'Summarize', choices: [{ value: false, text: lang === 'zh' ? '否' : 'No' }, { value: true, text: lang === 'zh' ? '是' : 'Yes' }] },
          { key: 'analyze', label: lang === 'zh' ? '解析' : 'Analyze', choices: [{ value: false, text: lang === 'zh' ? '否' : 'No' }, { value: true, text: lang === 'zh' ? '是' : 'Yes' }] },
        ] as Array<{ key: keyof Options; label: string; choices: Array<{ value: Options[keyof Options]; text: string }> }>).map((row) => (
          <div key={row.key} className="flex items-center gap-2" role="radiogroup" aria-label={row.label}>
            <span className="w-12 shrink-0 text-xs text-ink-400">{row.label}</span>
            <div className="flex flex-wrap gap-1.5">
              {row.choices.map((c) => {
                const active = options[row.key] === c.value;
                const id = row.key === 'rating' ? (c.value === null ? 0 : c.value) : (c.value ? 1 : 0);
                return (
                  <button
                    key={String(c.value)}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    data-testid={`opt-${row.key}-${id}`}
                    onClick={() => setOption(row.key, c.value as never)}
                    className={`px-2.5 py-1 rounded-full border text-xs transition-colors ${
                      active
                        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                        : 'bg-ink-800 text-ink-300 border-ink-700'
                    }`}
                  >
                    {c.text}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run pages/ShortcutsTagsPage.test.tsx && npx tsc --noEmit -p tsconfig.json`
Expected: 3 passed；tsc 无错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/pages/ShortcutsTagsPage.tsx frontend/pages/ShortcutsTagsPage.test.tsx
git commit -m "feat(shortcuts): 选择页四项单选（评级/转录/总结/解析）随标签一次保存，隐藏 Pipeline 组，选中即清空搜索"
```

---

### Task 8: 选择页「搜索即创建」条（带 EN/ZH 对译与「=」）

**Files:**
- Modify: `frontend/pages/ShortcutsTagsPage.tsx`（`setOption` 之后新增 quick-create 逻辑；`{filteredTags.length === 0 && (...)}` 处替换渲染）
- Test: `frontend/pages/ShortcutsTagsPage.test.tsx`（追加）

**Interfaces:**
- Consumes: 既有 `isChinese`、`handleCreateTag` 里的 409 文案逻辑（抽成 `describeCreateError(res, name)` 共用）。
- Produces: `const quickCreate = async () => void`；`data-testid`：`quick-create-btn`、`quick-translate-input`、`quick-same-btn`。

- [ ] **Step 1: 写失败测试**

追加到 `frontend/pages/ShortcutsTagsPage.test.tsx`：

```tsx
describe('ShortcutsTagsPage search-or-create', () => {
  beforeEach(() => vi.resetModules());

  it('offers to create the missing tag and posts name/name_zh split by language', async () => {
    const calls = mockFetch();
    await renderPage();
    const box = screen.getByPlaceholderText('搜索标签...');
    fireEvent.change(box, { target: { value: '鸟类' } });
    const btn = await screen.findByTestId('quick-create-btn');
    expect(btn.textContent).toContain('鸟类');
    fireEvent.click(screen.getByTestId('quick-same-btn'));
    expect((screen.getByTestId('quick-translate-input') as HTMLInputElement).value).toBe('鸟类');
    fireEvent.change(screen.getByTestId('quick-translate-input'), { target: { value: 'Birds' } });
    fireEvent.click(btn);
    await waitFor(() => {
      const create = calls.find((c) => c.url.endsWith('/tags') && c.body);
      expect(create?.body).toEqual({ name: 'Birds', name_zh: '鸟类', group_id: null });
    });
    await waitFor(() => expect((box as HTMLInputElement).value).toBe(''));
  });
});
```

`mockFetch` 里加一条分支，放在 `/tags?enabled_only=true` 判断之后：

```ts
    if (url.endsWith('/tags') && init?.method === 'POST') {
      return new Response(JSON.stringify({ success: true, data: { id: '9', name: 'Birds', name_zh: '鸟类', type: 'user', color: '#6366f1', group_id: null, group_name: null, media_count: 0 } }), { status: 200 });
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run pages/ShortcutsTagsPage.test.tsx -t "search-or-create"`
Expected: FAIL —— `Unable to find an element by: [data-testid="quick-create-btn"]`。

- [ ] **Step 3: 实现**

在 Task 7 的 `setOption` 定义**之后**加（`quickCreate` 调用 `toggle`，放在其后免得 eslint `no-use-before-define`；对译请求复用 `handleInputChange` 里的 MyMemory 调用，抽成 `fetchTranslation`）：

```tsx
  const fetchTranslation = async (text: string): Promise<string> => {
    try {
      const langPair = isChinese(text) ? 'zh|en' : 'en|zh';
      const res = await fetch(
        `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=${langPair}&de=8512939@qq.com`,
      );
      if (!res.ok) return '';
      const data = await res.json();
      const translated = data?.responseData?.translatedText;
      return translated && translated !== text ? translated : '';
    } catch {
      return ''; // Translation is optional
    }
  };

  // Search-or-create bar (mirrors chrome-extension popup.js syncQuickCreateBar).
  const [quickTranslate, setQuickTranslate] = useState('');
  const quickTouchedRef = useRef(false);
  const quickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [quickCreating, setQuickCreating] = useState(false);
  const [quickError, setQuickError] = useState<string | null>(null);
  const showQuickCreate = !!query && filteredTags.length === 0 && !showCreateForm;

  useEffect(() => {
    // New query → drop the previous suggestion and ask for a fresh one; an
    // edited / "="-ed value is the user's and survives.
    quickTouchedRef.current = false;
    setQuickTranslate('');
    setQuickError(null);
    if (quickTimerRef.current) clearTimeout(quickTimerRef.current);
    if (!showQuickCreate) return;
    const term = search.trim();
    quickTimerRef.current = setTimeout(async () => {
      const translated = await fetchTranslation(term);
      if (translated && !quickTouchedRef.current) setQuickTranslate(translated);
    }, 600);
    return () => { if (quickTimerRef.current) clearTimeout(quickTimerRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, showQuickCreate]);

  const quickCreate = async () => {
    const term = search.trim();
    if (!term || !token || quickCreating) return;
    const other = quickTranslate.trim();
    const payload = isChinese(term)
      ? { name: other || term, name_zh: term, group_id: null }
      : { name: term, name_zh: other || null, group_id: null };
    setQuickCreating(true);
    setQuickError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await describeCreateError(res, payload.name));
      const tagsRes = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags?enabled_only=true`);
      if (tagsRes.ok) {
        const data = await tagsRes.json();
        setTags(data.tags || []);
      }
      toggle(payload.name); // selects + clears the search
    } catch (err) {
      setQuickError(err instanceof Error ? err.message : 'Failed to create tag');
    } finally {
      setQuickCreating(false);
    }
  };
```

把 `handleCreateTag` 里从 `if (!res.ok) {` 到对应 `}` 的整段错误文案逻辑抽成组件内函数 `const describeCreateError = async (res: Response, name: string): Promise<string> => { ... return <原来 throw 的字符串> }`，`handleCreateTag` 改为 `if (!res.ok) throw new Error(await describeCreateError(res, name));`（文案一字不改）。

渲染：把

```tsx
        {filteredTags.length === 0 && (
          <p className="text-center text-sm text-ink-500 py-8">
            {query
              ? (lang === 'zh' ? '没有匹配的标签' : 'No tags match your search')
              : (lang === 'zh' ? '暂无标签' : 'No tags yet')}
          </p>
        )}
```

替换为：

```tsx
        {filteredTags.length === 0 && !showQuickCreate && (
          <p className="text-center text-sm text-ink-500 py-8">
            {lang === 'zh' ? '暂无标签' : 'No tags yet'}
          </p>
        )}
        {showQuickCreate && (
          <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3 space-y-2">
            <p className="text-xs text-ink-500">
              {lang === 'zh' ? '没有匹配的标签' : 'No tags match your search'}
            </p>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink-500 w-8">{isChinese(search.trim()) ? 'EN:' : 'ZH:'}</span>
              <input
                data-testid="quick-translate-input"
                value={quickTranslate}
                onChange={(e) => { quickTouchedRef.current = true; setQuickTranslate(e.target.value); }}
                placeholder={lang === 'zh' ? '对应译名（可改）' : 'Counterpart name (editable)'}
                className="flex-1 px-2 py-1 rounded bg-ink-800 border border-ink-700 text-ink-50 outline-none focus:border-indigo-500"
              />
              <button
                type="button"
                data-testid="quick-same-btn"
                title={lang === 'zh' ? '两种语言同名' : 'Same in both languages'}
                onClick={() => { quickTouchedRef.current = true; setQuickTranslate(search.trim()); }}
                className="px-2 py-1 rounded border border-ink-700 text-ink-300"
              >
                =
              </button>
            </div>
            {quickError && <p className="text-xs text-red-400">{quickError}</p>}
            <button
              type="button"
              data-testid="quick-create-btn"
              onClick={quickCreate}
              disabled={quickCreating}
              className="w-full py-2 rounded-lg bg-indigo-600 text-sm font-medium text-white disabled:opacity-40"
            >
              {quickCreating
                ? (lang === 'zh' ? `创建「${search.trim()}」中...` : `Creating "${search.trim()}"...`)
                : (lang === 'zh' ? `创建「${search.trim()}」` : `Create "${search.trim()}"`)}
            </button>
          </div>
        )}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run pages/ShortcutsTagsPage.test.tsx && npx tsc --noEmit -p tsconfig.json && npx eslint pages/ShortcutsTagsPage.tsx`
Expected: 4 passed；tsc、eslint 无错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/pages/ShortcutsTagsPage.tsx frontend/pages/ShortcutsTagsPage.test.tsx
git commit -m "feat(shortcuts): 选择页搜索无结果时直接创建，带 EN/ZH 对译与「=」同名，创建后自动选中"
```

---

### Task 9: PR、部署、真栈验收

**Files:** 无代码改动。

- [ ] **Step 1: 全量本地回归**

Run:
```bash
cd backend && uv run pytest -q -x --ignore=tests/services/distribution 2>&1 | tail -3
cd ../frontend && npx vitest run 2>&1 | tail -3
```
Expected: 后端除已知 13 个环境性红外全绿（用 `-p no:cacheprovider` 无所谓）；前端全绿。

- [ ] **Step 2: 开 PR**

```bash
git push -u origin feat/ai-intent-fields
gh pr create --title "feat(intents): 转录/总结/解析/评级改为显式字段；快捷指令选择页四项单选 + 搜索即创建" --body "$(cat <<'EOF'
Spec: docs/superpowers/specs/2026-09-10-ai-intent-fields-design.md
Plan: docs/superpowers/plans/2026-09-10-ai-intent-fields.md

- 抓取请求（单/批）新增 transcribe / summarize / analyze / rating；服务端映射到 Pipeline 系统标签 id 并入 tag_ids，下载链不改；rating 写 resources.rating
- 临时令牌选择结果新增 options；`format=text&field=<名>` 逐项单值给快捷指令
- 选择页：四项单选随标签一次保存、隐藏 Pipeline 组、搜索即创建（EN/ZH 对译 + "="）、选中清空搜索
- FloatingParse 改发布尔意图

Test plan
- [ ] CI 8 项全绿
- [ ] 部署后真栈：快捷指令调试令牌 → 选择页勾「转录 + 解析 + ★★★★」→ `field=transcribe` 得 `1` → POST /media/fetch 四字段 → 资源带 Transcript/Analyze 系统标签、rating=4、Task Center 出现转录与解析任务
EOF
)"
```

- [ ] **Step 3: 盯 CI，绿即合并**

```bash
gh pr checks --watch
gh pr merge --squash --delete-branch
```

- [ ] **Step 4: 等 Deploy GPU 与前端 Pages 链，真栈探针**

```bash
gh run list --workflow deploy-gpu.yml --branch master --limit 1
gh run list --workflow deploy-pages.yml --branch master --limit 1
```
两条 `completed success` 后，在 gpupc 上验证映射（只读，不发抓取）：

```bash
ssh heygo@10.0.0.10 'docker exec -i -w /app nous-backend /app/.venv/bin/python -' <<'PY'
import asyncio
from app.api.media_fetch_helpers import resolve_intent_tag_ids
print(asyncio.run(resolve_intent_tag_ids(transcribe=True, summarize=True, analyze=True)))
PY
```
Expected: 三个 id 的列表（缺一个即种子问题，去查 `tags` 表 `type='system'` 行）。

然后按 PR 里的 Test plan 用真实快捷指令走一遍，并把 spec §6 的三个 URL/字段名交给用户改快捷指令。

- [ ] **Step 5: 更新记忆**

在 `~/.claude/projects/-Volumes-program-project-code-repos-nous-app/memory/` 写一条 project 记忆：意图字段契约已上线、快捷指令侧要改的四次 `field=` 请求、Chrome 插件改造仍待做（spec §3.7）。
