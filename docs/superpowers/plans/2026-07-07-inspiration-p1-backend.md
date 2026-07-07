# Inspiration Notes P1 — 数据 + 后端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 灵感笔记的全部后端地基——三张表、#tag 解析、笔记 CRUD/日历聚合、附件对象存储(可换后端 adapter)、REST 路由。纯新增,flag 无关,对现网零影响。

**Architecture:** Router → Service → Repository 三层(项目标准);repo 用 supabase-py admin 客户端(canvas_repository 模板);附件存储复用 `app/services/library/media_storage.py::ObjectStore`(已有 put/get/remove/signed_url),新开 `inspiration` bucket;聚合查询(活动日历/标签计数)走 migration 内 SQL 函数 + `client.rpc()`(PostgREST 不擅 GROUP BY)。

**Tech Stack:** FastAPI + supabase-py(async admin)+ PostgreSQL(migration 348)+ pytest。

**Spec:** `docs/superpowers/specs/2026-07-07-inspiration-notes-design.md`(本计划实现其 §3 数据模型、§4 后端;PAT/tokens 表建在 348 里但端点留给 P4)。

## Global Constraints

- 工作目录:本 worktree(`feature/inspiration-notes` 分支);commit 均在此分支
- 迁移编号 **348**(`supabase/migrations/348_inspiration_notes.sql`);CI 会自动 apply,**禁止** `ALTER PUBLICATION ... DROP IF EXISTS`(已知非法坑)
- ID 全 BIGINT Snowflake,DB 侧 `DEFAULT generate_snowflake_id()`;Python 侧比较/绑定一律 `_bigint()` 收敛(bigint-int-vs-str 铁律)
- `note_date` 由后端按 **Asia/Shanghai** 计算,不用 DB `CURRENT_DATE`
- 附件路径 `{yyyy}/{mm}/{dd}/{uuid}/{filename}` 日期分桶(铁律)
- 上限配置读 `system_settings` 键 `inspiration.max_attachment_mb`(默认 500)——env→DB 铁律
- 错误:越权/不存在一律 404 不泄露存在性;上传超限 413;storage 故障 502;所有 except 分支 `logger.error` 带上下文
- 测试环境变量:`SUPABASE_URL=http://localhost:54321 SUPABASE_SERVICE_ROLE_KEY=dummy_service_role_key_for_ci SUPABASE_ANON_KEY=dummy_anon_key_for_ci`
- 每个 task 结束跑 lint gate:`uv run black <改动文件> && uv run isort <改动文件> && uv run flake8 <改动文件>`
- 所有命令在 `backend/` 目录用 `uv run` 前缀执行

---

### Task 1: Migration 348 — 三张表 + RLS + 聚合函数

**Files:**
- Create: `supabase/migrations/348_inspiration_notes.sql`

**Interfaces:**
- Produces: 表 `inspiration_notes` / `inspiration_attachments` / `inspiration_api_tokens`;SQL 函数 `inspiration_activity(p_user_id uuid, p_from date, p_to date)`、`inspiration_tag_counts(p_user_id uuid)`;`system_settings` 键 `inspiration.max_attachment_mb`

- [ ] **Step 1: 写迁移文件**

```sql
-- 348_inspiration_notes.sql
-- Inspiration Notes P1 (spec: docs/superpowers/specs/2026-07-07-inspiration-notes-design.md)
-- memos-style quick-capture notes + multi-format attachments + PAT tokens (endpoints in P4).

CREATE TABLE IF NOT EXISTS inspiration_notes (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  user_id     UUID NOT NULL,
  content_md  TEXT NOT NULL DEFAULT '',
  tags        TEXT[] NOT NULL DEFAULT '{}',
  ref_hotspot JSONB NULL,
  pinned      BOOLEAN NOT NULL DEFAULT false,
  note_date   DATE NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at  TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_date
  ON inspiration_notes (user_id, note_date DESC);
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_pinned
  ON inspiration_notes (user_id, pinned) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_tags
  ON inspiration_notes USING GIN (tags);

CREATE TABLE IF NOT EXISTS inspiration_attachments (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  note_id         BIGINT NOT NULL REFERENCES inspiration_notes(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL,
  storage_backend VARCHAR(32) NOT NULL DEFAULT 'supabase',
  bucket          VARCHAR(64) NOT NULL DEFAULT 'inspiration',
  path            TEXT NOT NULL,
  mime            VARCHAR(255) NOT NULL,
  size_bytes      BIGINT NOT NULL,
  original_name   TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inspiration_attachments_note
  ON inspiration_attachments (note_id);

CREATE TABLE IF NOT EXISTS inspiration_api_tokens (
  id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  user_id      UUID NOT NULL,
  name         VARCHAR(128) NOT NULL,
  token_hash   VARCHAR(64) NOT NULL,
  last_used_at TIMESTAMPTZ NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at   TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inspiration_api_tokens_hash
  ON inspiration_api_tokens (token_hash);

-- RLS: owner-only(后端全走 service_role,策略防未来直连;模板同 canvases)
ALTER TABLE inspiration_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspiration_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspiration_api_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY inspiration_notes_owner ON inspiration_notes
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY inspiration_attachments_owner ON inspiration_attachments
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY inspiration_api_tokens_owner ON inspiration_api_tokens
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- 活动日历聚合(热力图/月历密度)
CREATE OR REPLACE FUNCTION inspiration_activity(p_user_id uuid, p_from date, p_to date)
RETURNS TABLE(day date, cnt bigint)
LANGUAGE sql STABLE AS $$
  SELECT note_date AS day, COUNT(*) AS cnt
  FROM inspiration_notes
  WHERE user_id = p_user_id AND deleted_at IS NULL
    AND note_date BETWEEN p_from AND p_to
  GROUP BY note_date
  ORDER BY note_date;
$$;

-- 标签聚合(侧栏 Tags 面板 + composer 补全)
CREATE OR REPLACE FUNCTION inspiration_tag_counts(p_user_id uuid)
RETURNS TABLE(tag text, cnt bigint)
LANGUAGE sql STABLE AS $$
  SELECT t.tag, COUNT(*) AS cnt
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.user_id = p_user_id AND n.deleted_at IS NULL
  GROUP BY t.tag
  ORDER BY cnt DESC, tag;
$$;

-- 附件上限配置(env→DB 铁律;jsonb 值存数字非字符串)
INSERT INTO system_settings (key, value)
VALUES ('inspiration.max_attachment_mb', '500'::jsonb)
ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 2: 核对 system_settings 表列名**(PG 只报首个未定义列铁律)

Run: `grep -n 'CREATE TABLE.*system_settings' -A 8 supabase/migrations/*.sql | head -20`
Expected: 确认列就是 `key` + `value`(jsonb)。若实际是别的列名(如 `setting_key`),按实际改 INSERT。

- [ ] **Step 3: 本地语法校验(可用 dev DB 时)**

Run(worktree 根): `psql "postgresql://postgres:postgres@192.168.50.9:55434/postgres" -f supabase/migrations/348_inspiration_notes.sql 2>&1 | tail -5`
Expected: 全 CREATE 无 ERROR。dev DB 不可达则跳过(CI 会验),在 commit message 注明 untested-locally。

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/348_inspiration_notes.sql
git commit -m "feat(inspiration): migration 348 — notes/attachments/api_tokens + activity/tag aggregate fns"
```

---

### Task 2: #tag 解析纯函数

**Files:**
- Create: `backend/app/services/inspiration/__init__.py`(空文件)
- Create: `backend/app/services/inspiration/note_tags.py`
- Test: `backend/tests/test_inspiration_note_tags.py`

**Interfaces:**
- Produces: `parse_tags(content_md: str) -> list[str]` — 去重保序、小写归一、剥离标点;这是前后端共享的**契约**(P2 前端 noteTags.ts 用同一正则语义)

- [ ] **Step 1: 写失败测试**

```python
"""#tag parsing contract tests.

The SAME semantics will be mirrored in frontend/components/Inspiration/noteTags.ts
(P2) — if you change a rule here, change it there and in the spec §2.4.
"""

from app.services.inspiration.note_tags import parse_tags


def test_basic_tags_extracted_in_order():
    assert parse_tags("idea #hooks and #formats now") == ["hooks", "formats"]


def test_dedup_and_lowercase():
    assert parse_tags("#Hooks #hooks #HOOKS") == ["hooks"]


def test_cjk_and_hyphen_underscore_allowed():
    assert parse_tags("试试 #灵感 #short-form #a_b") == ["灵感", "short-form", "a_b"]


def test_trailing_punctuation_stripped():
    assert parse_tags("end #hooks. and (#formats)") == ["hooks", "formats"]


def test_not_a_tag_inside_word_or_url_fragment():
    assert parse_tags("c# is a language, see x.com/a#b") == []


def test_code_blocks_are_ignored():
    md = "text #real\n```\n# comment not a tag\nfoo #fake\n```\n`inline #fake2`"
    assert parse_tags(md) == ["real"]


def test_empty_and_bare_hash():
    assert parse_tags("") == []
    assert parse_tags("# heading text") == []  # markdown 标题不是 tag(# 后有空格)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_inspiration_note_tags.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.inspiration`

- [ ] **Step 3: 实现**

```python
"""Inline #tag parser for inspiration notes.

Contract (spec §2.4, mirrored by frontend noteTags.ts):
- a tag starts with `#` preceded by start-of-text or whitespace/`(`
- tag chars: unicode letters/digits, `-`, `_`; terminated by anything else
- `# ` (hash-space, i.e. markdown heading) is NOT a tag
- fenced ``` blocks and `inline code` are stripped before parsing
- output: lowercase (ASCII only — CJK untouched), de-duplicated, first-seen order
"""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# 前导:行首或空白或全半角左括号;标签体:字母数字(含 CJK)/-/_,至少 1 字符
_TAG = re.compile(r"(?:(?<=^)|(?<=[\s(（]))#([\w一-鿿-]+)", re.UNICODE)


def parse_tags(content_md: str) -> list[str]:
    """Extract inline #tags from markdown; see module docstring for the contract."""
    if not content_md:
        return []
    text = _CODE_FENCE.sub(" ", content_md)
    text = _INLINE_CODE.sub(" ", text)
    seen: dict[str, None] = {}
    for match in _TAG.finditer(text):
        tag = match.group(1).lower()
        if tag:
            seen.setdefault(tag, None)
    return list(seen.keys())
```

- [ ] **Step 4: 跑测试确认全过**

Run: `uv run pytest tests/test_inspiration_note_tags.py -v`
Expected: 7 passed。若 `test_not_a_tag_inside_word_or_url_fragment` 挂在 `x.com/a#b`(`/` 不在前导集,应已不匹配)按失败情况微调正则,**以测试为准不改测试**。

- [ ] **Step 5: Lint + Commit**

```bash
uv run black app/services/inspiration/ tests/test_inspiration_note_tags.py && uv run isort app/services/inspiration/ tests/test_inspiration_note_tags.py && uv run flake8 app/services/inspiration/ tests/test_inspiration_note_tags.py
git add backend/app/services/inspiration/ backend/tests/test_inspiration_note_tags.py
git commit -m "feat(inspiration): inline #tag parser — shared contract with frontend"
```

---

### Task 3: Notes Repository

**Files:**
- Create: `backend/app/repositories/inspiration_repository.py`
- Test: `backend/tests/test_inspiration_repository.py`

**Interfaces:**
- Consumes: `app.db.supabase_client.get_async_supabase_admin`(现有)
- Produces(全部 async,Task 5 service 依赖):
  - `InspirationNotesRepository.create(user_id: str, content_md: str, tags: list[str], note_date: str, ref_hotspot: dict | None) -> dict`
  - `.get_by_id(note_id) -> dict | None`(不含已软删)
  - `.list(user_id, *, date: str | None, tag: str | None, q: str | None, limit: int, before_id: str | None) -> list[dict]`(keyset:id DESC)
  - `.update(note_id, *, content_md: str | None, tags: list[str] | None, pinned: bool | None) -> dict | None`
  - `.soft_delete(note_id) -> bool`
  - `.activity(user_id, date_from: str, date_to: str) -> list[dict]`(rpc `inspiration_activity`)
  - `.tag_counts(user_id) -> list[dict]`(rpc `inspiration_tag_counts`)
  - 模块级 `get_inspiration_notes_repository()` 工厂(单例,现有 repo 惯例)

- [ ] **Step 1: 写失败测试**(fake 链式 supabase client,模式同现有 repo 测试)

```python
"""InspirationNotesRepository unit tests — fake chainable supabase client."""

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.inspiration_repository import InspirationNotesRepository


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Chainable stand-in recording every builder call."""

    def __init__(self, result_data):
        self._result_data = result_data
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    async def execute(self):
        return FakeResult(self._result_data)


class FakeClient:
    def __init__(self, result_data):
        self.query = FakeQuery(result_data)
        self.rpc_query = FakeQuery(result_data)

    def table(self, name):
        self.query.calls.append(("table", (name,), {}))
        return self.query

    def rpc(self, fn, params):
        self.rpc_query.calls.append(("rpc", (fn, params), {}))
        return self.rpc_query


def _patch_client(fake):
    return patch(
        "app.repositories.inspiration_repository.get_async_supabase_admin",
        new=AsyncMock(return_value=fake),
    )


@pytest.mark.asyncio
async def test_create_inserts_row_and_returns_dict():
    fake = FakeClient([{"id": 1, "content_md": "x"}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        row = await repo.create(
            user_id="u1", content_md="x #a", tags=["a"], note_date="2026-07-07", ref_hotspot=None
        )
    assert row == {"id": 1, "content_md": "x"}
    insert_calls = [c for c in fake.query.calls if c[0] == "insert"]
    assert len(insert_calls) == 1
    payload = insert_calls[0][1][0]
    assert payload["user_id"] == "u1"
    assert payload["tags"] == ["a"]
    assert payload["note_date"] == "2026-07-07"
    assert "ref_hotspot" not in payload  # None 不显式写,吃 DB 默认


@pytest.mark.asyncio
async def test_list_applies_keyset_and_filters():
    fake = FakeClient([])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        await repo.list("u1", date="2026-07-07", tag="hooks", q="ferry", limit=20, before_id="99")
    names = [c[0] for c in fake.query.calls]
    assert "lt" in names  # keyset: id < before_id
    assert "contains" in names  # tags @> [tag]
    assert "ilike" in names  # content_md ILIKE %q%
    lt = next(c for c in fake.query.calls if c[0] == "lt")
    assert lt[1] == ("id", 99)  # bigint 收敛为 int


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing():
    fake = FakeClient(None)
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        assert await repo.get_by_id("123") is None


@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at():
    fake = FakeClient([{"id": 1}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        ok = await repo.soft_delete("123")
    assert ok is True
    update_calls = [c for c in fake.query.calls if c[0] == "update"]
    assert "deleted_at" in update_calls[0][1][0]


@pytest.mark.asyncio
async def test_activity_calls_rpc_with_named_params():
    fake = FakeClient([{"day": "2026-07-07", "cnt": 3}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        rows = await repo.activity("u1", "2026-04-01", "2026-07-07")
    assert rows == [{"day": "2026-07-07", "cnt": 3}]
    rpc = fake.rpc_query.calls[0]
    assert rpc[1][0] == "inspiration_activity"
    assert rpc[1][1] == {"p_user_id": "u1", "p_from": "2026-04-01", "p_to": "2026-07-07"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_inspiration_repository.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 repository**

```python
"""Repository for inspiration_notes (migration 348).

Pure data access via the service-role supabase client (canvas_repository
template). Ownership checks live in the service layer — every method here
trusts its caller. All bigint ids are coerced with _bigint before binding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


class InspirationNotesRepository:
    TABLE = "inspiration_notes"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self,
        user_id: str,
        content_md: str,
        tags: List[str],
        note_date: str,
        ref_hotspot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            payload: Dict[str, Any] = {
                "user_id": user_id,
                "content_md": content_md,
                "tags": tags,
                "note_date": note_date,
            }
            if ref_hotspot is not None:
                payload["ref_hotspot"] = ref_hotspot
            result = await client.table(self.TABLE).insert(payload).execute()
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration create failed (user={user_id}): {e}")
            return None

    async def get_by_id(self, note_id: Any) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", _bigint(note_id))
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration get_by_id({note_id}) failed: {e}")
            return None

    async def list(
        self,
        user_id: str,
        *,
        date: Optional[str] = None,
        tag: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        before_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            query = (
                client.table(self.TABLE)
                .select("*")
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
            )
            if date:
                query = query.eq("note_date", date)
            if tag:
                query = query.contains("tags", [tag])
            if q:
                query = query.ilike("content_md", f"%{q}%")
            if before_id:
                query = query.lt("id", _bigint(before_id))
            result = await query.order("id", desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration list failed (user={user_id}): {e}")
            return []

    async def update(
        self,
        note_id: Any,
        *,
        content_md: Optional[str] = None,
        tags: Optional[List[str]] = None,
        pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            payload: Dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if content_md is not None:
                payload["content_md"] = content_md
            if tags is not None:
                payload["tags"] = tags
            if pinned is not None:
                payload["pinned"] = pinned
            result = (
                await client.table(self.TABLE)
                .update(payload)
                .eq("id", _bigint(note_id))
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration update({note_id}) failed: {e}")
            return None

    async def soft_delete(self, note_id: Any) -> bool:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", _bigint(note_id))
                .execute()
            )
            return bool(result and result.data)
        except Exception as e:
            logger.error(f"inspiration soft_delete({note_id}) failed: {e}")
            return False

    async def activity(
        self, user_id: str, date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            result = await client.rpc(
                "inspiration_activity",
                {"p_user_id": user_id, "p_from": date_from, "p_to": date_to},
            ).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration activity failed (user={user_id}): {e}")
            return []

    async def tag_counts(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            result = await client.rpc(
                "inspiration_tag_counts", {"p_user_id": user_id}
            ).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration tag_counts failed (user={user_id}): {e}")
            return []


_repo: Optional[InspirationNotesRepository] = None


def get_inspiration_notes_repository() -> InspirationNotesRepository:
    global _repo
    if _repo is None:
        _repo = InspirationNotesRepository()
    return _repo
```

- [ ] **Step 4: 跑测试确认全过**

Run: `uv run pytest tests/test_inspiration_repository.py -v`
Expected: 5 passed。注意 `.rpc(...)` 在真实客户端返回 builder 需 `.execute()`——fake 已同构;若真实 supabase-py 版本 rpc 直接 await(无 execute),按仓里现有 rpc 用法对齐(grep `\.rpc(` 现有 repo)并同步改 fake。

- [ ] **Step 5: Lint + Commit**

```bash
uv run black app/repositories/inspiration_repository.py tests/test_inspiration_repository.py && uv run isort app/repositories/inspiration_repository.py tests/test_inspiration_repository.py && uv run flake8 app/repositories/inspiration_repository.py tests/test_inspiration_repository.py
git add backend/app/repositories/inspiration_repository.py backend/tests/test_inspiration_repository.py
git commit -m "feat(inspiration): notes repository — CRUD, keyset list, activity/tag rpc"
```

---

### Task 4: 附件 Repository + Storage 服务

**Files:**
- Create: `backend/app/repositories/inspiration_attachments_repository.py`
- Create: `backend/app/services/inspiration/attachment_service.py`
- Test: `backend/tests/test_inspiration_attachments.py`

**Interfaces:**
- Consumes: `app.services.library.media_storage.ObjectStore`(现有:`put_bytes(key, data, mime)` / `remove(key)` / `signed_url(key, ttl_seconds=300)`——签名以现文件为准,实现前先读一遍该类);Task 3 的 `_bigint` 模式
- Produces(Task 6 router 依赖):
  - `InspirationAttachmentsRepository.create(note_id, user_id, bucket, path, mime, size_bytes, original_name) -> dict`;`.get_by_id(att_id) -> dict | None`;`.list_for_notes(note_ids: list) -> list[dict]`;`.delete(att_id) -> bool`;工厂 `get_inspiration_attachments_repository()`
  - `AttachmentService.store(user_id, note_id, filename, mime, data: bytes) -> dict`(校验大小→分桶 path→ObjectStore 写→repo 插行;超限抛 `AttachmentTooLarge`)
  - `AttachmentService.sign_get(att_row: dict) -> str`;`AttachmentService.delete(att_row: dict) -> bool`(best-effort 删对象)
  - `INSPIRATION_BUCKET = "inspiration"`;异常类 `AttachmentTooLarge(limit_mb: int)`

- [ ] **Step 1: 写失败测试**

```python
"""Attachment repo + service tests. ObjectStore and repos are mocked."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.inspiration.attachment_service import (
    AttachmentService,
    AttachmentTooLarge,
)


def _service(store=None, repo=None, limit_mb=500):
    svc = AttachmentService()
    svc._store = store or AsyncMock()
    svc._repo = repo or AsyncMock()
    svc._get_limit_mb = AsyncMock(return_value=limit_mb)
    return svc


@pytest.mark.asyncio
async def test_store_writes_object_then_row_with_date_bucket_path():
    store, repo = AsyncMock(), AsyncMock()
    repo.create.return_value = {"id": 7, "path": "whatever"}
    svc = _service(store, repo)
    row = await svc.store("u1", "42", "pic.png", "image/png", b"\x89PNG")
    assert row["id"] == 7
    key = store.put_bytes.call_args[0][0]
    # {yyyy}/{mm}/{dd}/{uuid}/{filename} 日期分桶铁律
    parts = key.split("/")
    assert len(parts) == 5 and parts[4] == "pic.png"
    assert len(parts[0]) == 4 and parts[0].isdigit()
    repo.create.assert_awaited_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["mime"] == "image/png"
    assert kwargs["size_bytes"] == 4
    assert kwargs["original_name"] == "pic.png"


@pytest.mark.asyncio
async def test_store_rejects_over_limit_before_touching_storage():
    store = AsyncMock()
    svc = _service(store=store, limit_mb=1)
    with pytest.raises(AttachmentTooLarge):
        await svc.store("u1", "42", "big.bin", "application/octet-stream", b"x" * (1024 * 1024 + 1))
    store.put_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_row_even_if_object_removal_fails():
    store, repo = AsyncMock(), AsyncMock()
    store.remove.side_effect = RuntimeError("s3 down")
    repo.delete.return_value = True
    svc = _service(store, repo)
    ok = await svc.delete({"id": 7, "path": "2026/07/07/u/x.png", "bucket": "inspiration"})
    assert ok is True
    repo.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_filename_is_sanitized_in_object_key():
    store, repo = AsyncMock(), AsyncMock()
    repo.create.return_value = {"id": 1}
    svc = _service(store, repo)
    await svc.store("u1", "42", "../../evil name?.png", "image/png", b"x")
    key = store.put_bytes.call_args[0][0]
    assert ".." not in key and "?" not in key and " " not in key.split("/")[4]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_inspiration_attachments.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3a: 实现 attachments repository**

```python
# app/repositories/inspiration_attachments_repository.py
"""Repository for inspiration_attachments (migration 348). Pure data access."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


class InspirationAttachmentsRepository:
    TABLE = "inspiration_attachments"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self,
        note_id: Any,
        user_id: str,
        bucket: str,
        path: str,
        mime: str,
        size_bytes: int,
        original_name: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .insert(
                    {
                        "note_id": _bigint(note_id),
                        "user_id": user_id,
                        "bucket": bucket,
                        "path": path,
                        "mime": mime,
                        "size_bytes": size_bytes,
                        "original_name": original_name,
                    }
                )
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration attachment create failed (note={note_id}): {e}")
            return None

    async def get_by_id(self, attachment_id: Any) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", _bigint(attachment_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration attachment get({attachment_id}) failed: {e}")
            return None

    async def list_for_notes(self, note_ids: List[Any]) -> List[Dict[str, Any]]:
        if not note_ids:
            return []
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .in_("note_id", [_bigint(n) for n in note_ids])
                .order("id")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration attachments list_for_notes failed: {e}")
            return []

    async def delete(self, attachment_id: Any) -> bool:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .delete()
                .eq("id", _bigint(attachment_id))
                .execute()
            )
            return bool(result and result.data)
        except Exception as e:
            logger.error(f"inspiration attachment delete({attachment_id}) failed: {e}")
            return False


_repo: Optional[InspirationAttachmentsRepository] = None


def get_inspiration_attachments_repository() -> InspirationAttachmentsRepository:
    global _repo
    if _repo is None:
        _repo = InspirationAttachmentsRepository()
    return _repo
```

- [ ] **Step 3b: 实现 attachment service:**

```python
"""Attachment storage orchestration for inspiration notes.

Storage goes through ObjectStore (media_storage.py) — the adapter boundary
the spec requires: rows carry storage_backend/bucket/path, so a future
MinIO/S3/FS adapter only swaps the store class behind _store.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.services.library.media_storage import ObjectStore

INSPIRATION_BUCKET = "inspiration"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNSAFE = re.compile(r"[^\w.\-一-鿿]+")


class AttachmentTooLarge(Exception):
    def __init__(self, limit_mb: int) -> None:
        self.limit_mb = limit_mb
        super().__init__(f"attachment exceeds {limit_mb} MB limit")


def _sanitize(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).strip("._") or "file"
    return cleaned[:120]


class AttachmentService:
    def __init__(self) -> None:
        self._store = ObjectStore(INSPIRATION_BUCKET)
        self._repo = get_inspiration_attachments_repository()

    async def _get_limit_mb(self) -> int:
        # system_settings 键 inspiration.max_attachment_mb;读法参照现有
        # system_settings 消费方(jsonb 可能返 int 或 str,两者都接)。
        from app.repositories.system_settings_repository import (
            get_system_settings_repository,
        )

        try:
            raw = await get_system_settings_repository().get_value(
                "inspiration.max_attachment_mb"
            )
            return int(raw) if raw is not None else 500
        except Exception as e:
            logger.warning(f"attachment limit read failed, using 500MB default: {e}")
            return 500

    async def store(
        self, user_id: str, note_id: Any, filename: str, mime: str, data: bytes
    ) -> Optional[Dict[str, Any]]:
        limit_mb = await self._get_limit_mb()
        if len(data) > limit_mb * 1024 * 1024:
            raise AttachmentTooLarge(limit_mb)
        now = datetime.now(_SHANGHAI)
        key = (
            f"{now:%Y}/{now:%m}/{now:%d}/{uuid.uuid4().hex}/{_sanitize(filename)}"
        )
        await self._store.put_bytes(key, data, mime)
        return await self._repo.create(
            note_id=note_id,
            user_id=user_id,
            bucket=INSPIRATION_BUCKET,
            path=key,
            mime=mime,
            size_bytes=len(data),
            original_name=filename[:255],
        )

    async def sign_get(self, att_row: Dict[str, Any]) -> str:
        return await self._store.signed_url(att_row["path"])

    async def delete(self, att_row: Dict[str, Any]) -> bool:
        try:
            await self._store.remove(att_row["path"])
        except Exception as e:
            logger.warning(f"attachment object removal failed ({att_row['path']}): {e}")
        return await self._repo.delete(att_row["id"])
```

**注意**:实现前先 `Read backend/app/services/library/media_storage.py` 的 `ObjectStore` 真实方法签名(`put_bytes` 参数顺序/命名)与 `system_settings` 的 repo 是否存在及其取值方法名——**以现网代码为准**,不同则改 service 并同步改 mock 断言。若无现成 system_settings repo,直接在 service 里用 admin client 查表(同 Task 3 模板)。

- [ ] **Step 4: 跑测试确认全过**

Run: `uv run pytest tests/test_inspiration_attachments.py -v`
Expected: 4 passed

- [ ] **Step 5: Lint + Commit**

```bash
uv run black app/repositories/inspiration_attachments_repository.py app/services/inspiration/attachment_service.py tests/test_inspiration_attachments.py && uv run isort app/repositories/inspiration_attachments_repository.py app/services/inspiration/attachment_service.py tests/test_inspiration_attachments.py && uv run flake8 app/repositories/inspiration_attachments_repository.py app/services/inspiration/attachment_service.py tests/test_inspiration_attachments.py
git add backend/app/repositories/inspiration_attachments_repository.py backend/app/services/inspiration/attachment_service.py backend/tests/test_inspiration_attachments.py
git commit -m "feat(inspiration): attachments — ObjectStore adapter, date-bucket keys, size limit"
```

---

### Task 5: Notes Service(编排层)

**Files:**
- Create: `backend/app/services/inspiration/notes_service.py`
- Test: `backend/tests/test_inspiration_notes_service.py`

**Interfaces:**
- Consumes: Task 2 `parse_tags`;Task 3 repo 工厂;Task 4 attachments repo(list_for_notes 拼装)
- Produces(Task 6 router 依赖,全 async):
  - `NotesService.create_note(user_id, content_md, ref_hotspot=None) -> dict`(解析 tags、算 note_date、attachments=[] 附带)
  - `.list_notes(user_id, *, date, tag, q, limit, before_id) -> list[dict]`(每行拼 `attachments: [...]`)
  - `.update_note(user_id, note_id, *, content_md=None, pinned=None) -> dict`(owner 校验:非 owner/不存在抛 `NoteNotFound`;content 变更时重解析 tags)
  - `.delete_note(user_id, note_id) -> None`(同上校验,软删)
  - `.activity(user_id, date_from, date_to) -> list[dict]`;`.tag_counts(user_id) -> list[dict]`
  - 异常 `NoteNotFound(Exception)`;`get_notes_service()` 工厂;`_today_shanghai() -> str`(YYYY-MM-DD)

- [ ] **Step 1: 写失败测试**

```python
"""NotesService orchestration tests — repos mocked."""

from unittest.mock import AsyncMock

import pytest

from app.services.inspiration.notes_service import NotesService, NoteNotFound


def _service():
    svc = NotesService()
    svc._notes = AsyncMock()
    svc._attachments = AsyncMock()
    svc._attachments.list_for_notes.return_value = []
    return svc


@pytest.mark.asyncio
async def test_create_parses_tags_and_stamps_shanghai_date():
    svc = _service()
    svc._notes.create.return_value = {"id": 1, "tags": ["hooks"]}
    await svc.create_note("u1", "idea #hooks")
    kwargs = svc._notes.create.call_args.kwargs
    assert kwargs["tags"] == ["hooks"]
    assert len(kwargs["note_date"]) == 10  # YYYY-MM-DD


@pytest.mark.asyncio
async def test_update_rejects_non_owner_as_not_found():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "someone-else"}
    with pytest.raises(NoteNotFound):
        await svc.update_note("u1", "1", content_md="new")
    svc._notes.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_content_reparses_tags():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.update.return_value = {"id": 1}
    await svc.update_note("u1", "1", content_md="now #fresh")
    assert svc._notes.update.call_args.kwargs["tags"] == ["fresh"]


@pytest.mark.asyncio
async def test_update_pinned_only_does_not_touch_tags():
    svc = _service()
    svc._notes.get_by_id.return_value = {"id": 1, "user_id": "u1"}
    svc._notes.update.return_value = {"id": 1}
    await svc.update_note("u1", "1", pinned=True)
    assert svc._notes.update.call_args.kwargs["tags"] is None


@pytest.mark.asyncio
async def test_list_folds_attachments_per_note():
    svc = _service()
    svc._notes.list.return_value = [{"id": 1}, {"id": 2}]
    svc._attachments.list_for_notes.return_value = [
        {"id": 9, "note_id": 1, "mime": "image/png"}
    ]
    rows = await svc.list_notes("u1", date=None, tag=None, q=None, limit=50, before_id=None)
    assert rows[0]["attachments"][0]["id"] == 9
    assert rows[1]["attachments"] == []


@pytest.mark.asyncio
async def test_delete_missing_raises_not_found():
    svc = _service()
    svc._notes.get_by_id.return_value = None
    with pytest.raises(NoteNotFound):
        await svc.delete_note("u1", "404")


@pytest.mark.asyncio
async def test_assert_owned_raises_for_missing():
    svc = _service()
    svc._notes.get_by_id.return_value = None
    with pytest.raises(NoteNotFound):
        await svc.assert_owned("u1", "404")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_inspiration_notes_service.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
"""Notes service — ownership checks, tag parsing, attachment folding.

Ownership contract: any access to a note the caller does not own raises
NoteNotFound (mapped to HTTP 404 by the router) — existence is never leaked.
note_date is computed here in Asia/Shanghai, never by DB CURRENT_DATE.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.repositories.inspiration_repository import get_inspiration_notes_repository
from app.services.inspiration.note_tags import parse_tags

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class NoteNotFound(Exception):
    pass


def _today_shanghai() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%d")


class NotesService:
    def __init__(self) -> None:
        self._notes = get_inspiration_notes_repository()
        self._attachments = get_inspiration_attachments_repository()

    async def _owned(self, user_id: str, note_id: Any) -> Dict[str, Any]:
        row = await self._notes.get_by_id(note_id)
        if not row or str(row.get("user_id")) != str(user_id):
            raise NoteNotFound()
        return row

    async def assert_owned(self, user_id: str, note_id: Any) -> None:
        """Read-only ownership probe (router upload uses this; raises NoteNotFound)."""
        await self._owned(user_id, note_id)

    async def create_note(
        self, user_id: str, content_md: str, ref_hotspot: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        row = await self._notes.create(
            user_id=user_id,
            content_md=content_md,
            tags=parse_tags(content_md),
            note_date=_today_shanghai(),
            ref_hotspot=ref_hotspot,
        )
        if row is not None:
            row.setdefault("attachments", [])
        return row

    async def list_notes(
        self,
        user_id: str,
        *,
        date: Optional[str],
        tag: Optional[str],
        q: Optional[str],
        limit: int,
        before_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        rows = await self._notes.list(
            user_id, date=date, tag=tag, q=q, limit=limit, before_id=before_id
        )
        atts = await self._attachments.list_for_notes([r["id"] for r in rows]) if rows else []
        by_note: Dict[Any, List[Dict[str, Any]]] = {}
        for att in atts:
            by_note.setdefault(att["note_id"], []).append(att)
        for row in rows:
            row["attachments"] = by_note.get(row["id"], [])
        return rows

    async def update_note(
        self,
        user_id: str,
        note_id: Any,
        *,
        content_md: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        await self._owned(user_id, note_id)
        tags = parse_tags(content_md) if content_md is not None else None
        return await self._notes.update(
            note_id, content_md=content_md, tags=tags, pinned=pinned
        )

    async def delete_note(self, user_id: str, note_id: Any) -> None:
        await self._owned(user_id, note_id)
        await self._notes.soft_delete(note_id)

    async def activity(
        self, user_id: str, date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        return await self._notes.activity(user_id, date_from, date_to)

    async def tag_counts(self, user_id: str) -> List[Dict[str, Any]]:
        return await self._notes.tag_counts(user_id)


_svc: Optional[NotesService] = None


def get_notes_service() -> NotesService:
    global _svc
    if _svc is None:
        _svc = NotesService()
    return _svc
```

- [ ] **Step 4: 跑测试确认全过**

Run: `uv run pytest tests/test_inspiration_notes_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint + Commit**

```bash
uv run black app/services/inspiration/notes_service.py tests/test_inspiration_notes_service.py && uv run isort app/services/inspiration/notes_service.py tests/test_inspiration_notes_service.py && uv run flake8 app/services/inspiration/notes_service.py tests/test_inspiration_notes_service.py
git add backend/app/services/inspiration/notes_service.py backend/tests/test_inspiration_notes_service.py
git commit -m "feat(inspiration): notes service — ownership-as-404, shanghai note_date, attachment folding"
```

---

### Task 6: Schemas + Router + 注册

**Files:**
- Create: `backend/app/schemas/inspiration.py`
- Create: `backend/app/api/inspiration_router.py`
- Modify: `backend/app/api/__init__.py`(import + `api_router.include_router(router=inspiration_router, tags=["Inspiration"])`,插在现有 include 块末尾)
- Test: `backend/tests/test_inspiration_router.py`

**Interfaces:**
- Consumes: Task 5 `get_notes_service` / `NoteNotFound`;Task 4 `AttachmentService` / `AttachmentTooLarge` / attachments repo;`app.core.deps.get_current_user`(返回 dict,user id 取法先 grep 现有 router:`current_user["id"]` 或 `current_user["sub"]`,以 logs_router.py 实际用法为准)
- Produces: `/api/v1/inspiration/*` 端点(spec §4 表,tokens 除外);Pydantic 模型 `NoteCreateIn(content_md: str, ref_hotspot: dict | None)` / `NoteUpdateIn(content_md: str | None, pinned: bool | None)` / `NoteOut(id: str, ...)`(`model_config = ConfigDict(coerce_numbers_to_str=True)` 保 bigint 精度,同 SessionOut 模式)

- [ ] **Step 1: 写失败测试**(直接调 router 函数,绕 HTTP 层;模式同现有 router 单测)

```python
"""Router-layer tests: guard clauses + service exception mapping."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.inspiration_router import (
    create_note,
    delete_note,
    list_notes,
    update_note,
)
from app.schemas.inspiration import NoteCreateIn, NoteUpdateIn
from app.services.inspiration.notes_service import NoteNotFound

USER = {"id": "u1"}


@pytest.mark.asyncio
async def test_list_limit_out_of_range_400():
    for bad in (0, 201):
        with pytest.raises(HTTPException) as exc:
            await list_notes(
                date=None, tag=None, q=None, limit=bad, before_id=None, current_user=USER
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_returns_service_row():
    svc = AsyncMock()
    svc.create_note.return_value = {"id": 123, "content_md": "x", "tags": [],
                                    "note_date": "2026-07-07", "pinned": False,
                                    "attachments": [], "ref_hotspot": None,
                                    "created_at": "2026-07-07T00:00:00Z",
                                    "updated_at": "2026-07-07T00:00:00Z"}
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        out = await create_note(NoteCreateIn(content_md="x"), current_user=USER)
    assert out.id == "123"  # bigint → str


@pytest.mark.asyncio
async def test_update_maps_notfound_to_404():
    svc = AsyncMock()
    svc.update_note.side_effect = NoteNotFound()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await update_note("9", NoteUpdateIn(pinned=True), current_user=USER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_maps_notfound_to_404():
    svc = AsyncMock()
    svc.delete_note.side_effect = NoteNotFound()
    with patch("app.api.inspiration_router.get_notes_service", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await delete_note("9", current_user=USER)
    assert exc.value.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_inspiration_router.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 schemas + router**

```python
# app/schemas/inspiration.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NoteCreateIn(BaseModel):
    content_md: str = Field(..., max_length=100_000)
    ref_hotspot: Optional[Dict[str, Any]] = None


class NoteUpdateIn(BaseModel):
    content_md: Optional[str] = Field(None, max_length=100_000)
    pinned: Optional[bool] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    mime: str
    size_bytes: int
    original_name: str


class NoteOut(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, extra="ignore")
    id: str
    content_md: str
    tags: List[str]
    ref_hotspot: Optional[Dict[str, Any]] = None
    pinned: bool
    note_date: str
    created_at: str
    updated_at: str
    attachments: List[AttachmentOut] = []
```

```python
# app/api/inspiration_router.py
"""Inspiration notes REST API (spec §4). PAT auth + /tokens arrive in P4."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse

from app.core.deps import get_current_user
from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.schemas.inspiration import NoteCreateIn, NoteOut, NoteUpdateIn
from app.services.inspiration.attachment_service import (
    AttachmentService,
    AttachmentTooLarge,
)
from app.services.inspiration.notes_service import NoteNotFound, get_notes_service

router = APIRouter(prefix="/inspiration", tags=["Inspiration"])

_attachment_service: Optional[AttachmentService] = None


def _attachments() -> AttachmentService:
    global _attachment_service
    if _attachment_service is None:
        _attachment_service = AttachmentService()
    return _attachment_service


def _uid(current_user: dict) -> str:
    return str(current_user["id"])


@router.get("/notes", response_model=list[NoteOut])
async def list_notes(
    date: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    before_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return await get_notes_service().list_notes(
        _uid(current_user), date=date, tag=tag, q=q, limit=limit, before_id=before_id
    )


@router.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreateIn, current_user: dict = Depends(get_current_user)
):
    row = await get_notes_service().create_note(
        _uid(current_user), body.content_md, ref_hotspot=body.ref_hotspot
    )
    if row is None:
        raise HTTPException(status_code=502, detail="note persistence failed")
    return NoteOut(**row)


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str, body: NoteUpdateIn, current_user: dict = Depends(get_current_user)
):
    try:
        row = await get_notes_service().update_note(
            _uid(current_user), note_id, content_md=body.content_md, pinned=body.pinned
        )
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    if row is None:
        raise HTTPException(status_code=502, detail="note update failed")
    row.setdefault("attachments", [])
    return NoteOut(**row)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, current_user: dict = Depends(get_current_user)):
    try:
        await get_notes_service().delete_note(_uid(current_user), note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")


@router.get("/notes/activity")
async def notes_activity(
    date_from: str,
    date_to: str,
    current_user: dict = Depends(get_current_user),
):
    return await get_notes_service().activity(_uid(current_user), date_from, date_to)


@router.get("/notes/tags")
async def notes_tags(current_user: dict = Depends(get_current_user)):
    return await get_notes_service().tag_counts(_uid(current_user))


@router.post("/attachments/upload", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    note_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    try:
        await get_notes_service().assert_owned(_uid(current_user), note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail="note not found")
    data = await file.read()
    try:
        row = await _attachments().store(
            _uid(current_user),
            note_id,
            file.filename or "file",
            file.content_type or "application/octet-stream",
            data,
        )
    except AttachmentTooLarge as e:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds the {e.limit_mb} MB attachment limit",
        )
    if row is None:
        raise HTTPException(status_code=502, detail="attachment persistence failed")
    return row


@router.get("/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: str, current_user: dict = Depends(get_current_user)
):
    att = await get_inspiration_attachments_repository().get_by_id(attachment_id)
    if not att or str(att.get("user_id")) != _uid(current_user):
        raise HTTPException(status_code=404, detail="attachment not found")
    url = await _attachments().sign_get(att)
    return RedirectResponse(url, status_code=302)


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str, current_user: dict = Depends(get_current_user)
):
    att = await get_inspiration_attachments_repository().get_by_id(attachment_id)
    if not att or str(att.get("user_id")) != _uid(current_user):
        raise HTTPException(status_code=404, detail="attachment not found")
    await _attachments().delete(att)
```


- [ ] **Step 4: 注册路由**

`backend/app/api/__init__.py`:头部 import 块加 `from app.api.inspiration_router import router as inspiration_router`,include 块末尾加 `api_router.include_router(router=inspiration_router, tags=["Inspiration"])`。

- [ ] **Step 5: 跑本任务测试 + 全量回归**

Run: `uv run pytest tests/test_inspiration_router.py -v` → Expected: 4 passed
Run: `uv run pytest tests/ -q 2>&1 | tail -3` → Expected: 全绿(允许既有 skip;若撞 `test_supabase_client_drain.py::test_loop_dispose_evicts_dict_entry` 已知 flaky,rerun 一次)

- [ ] **Step 6: Lint + Commit**

```bash
uv run black app/schemas/inspiration.py app/api/inspiration_router.py app/api/__init__.py tests/test_inspiration_router.py && uv run isort app/schemas/inspiration.py app/api/inspiration_router.py app/api/__init__.py tests/test_inspiration_router.py && uv run flake8 app/schemas/inspiration.py app/api/inspiration_router.py app/api/__init__.py tests/test_inspiration_router.py
git add backend/app/schemas/inspiration.py backend/app/api/inspiration_router.py backend/app/api/__init__.py backend/tests/test_inspiration_router.py
git commit -m "feat(inspiration): REST router — notes CRUD/activity/tags + attachments upload/get/delete"
```

---

### Task 7: 收口 — 全量验证 + PR

- [ ] **Step 1: 后端全量测试**

Run(backend/): `SUPABASE_URL=http://localhost:54321 SUPABASE_SERVICE_ROLE_KEY=dummy_service_role_key_for_ci SUPABASE_ANON_KEY=dummy_anon_key_for_ci uv run pytest tests/ -q 2>&1 | tail -3`
Expected: 全绿

- [ ] **Step 2: lint gate 对全部新文件**

Run(backend/):
```bash
FILES=$(git diff --name-only origin/master...HEAD -- 'backend/**/*.py' | sed 's|^backend/||')
uv run black --check $FILES && uv run isort --check-only $FILES && uv run flake8 $FILES
```
Expected: exit 0

- [ ] **Step 3: push + PR(不与 CI 等待同批,绿后单独 merge)**

```bash
git push -u origin feature/inspiration-notes
gh pr create --title "feat(inspiration): P1 backend — notes/attachments/api-tokens schema + REST (spec 2026-07-07)" --body "P1 of the inspiration-notes redesign (spec: docs/superpowers/specs/2026-07-07-inspiration-notes-design.md). Pure additive backend: migration 348, tag parser, repos, services (ObjectStore adapter, date-bucket keys, Shanghai note_date), REST router. No UI, no flag needed — nothing reads these tables yet."
```

CI 全绿后:`gh pr merge <N> --squash --delete-branch`(分开两次调用)。**合并后盯 Run SQL Migration workflow**(migration CI 绿≠能 apply 铁律)。
