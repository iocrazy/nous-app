# Unified Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tags` 表成为全域唯一标签实体：笔记 #tag 经影子标签制入池（note_tags 关联表），热点查询期按 name/name_zh 匹配；另含 PR-0 独立修复中文 IME 回车建标签 bug。

**Architecture:** 4 个独立 PR（PR-0 bug 修 / PR-1 数据层 / PR-2 端点 / PR-3 前端），每个 PR 一个 worktree + feature 分支，实现交给 Opus 子代理，`/ship` 收尾。Spec：`docs/superpowers/specs/2026-07-17-unified-tags-design.md`（决策勿翻）。

**Tech Stack:** FastAPI + SQLAlchemy(asyncpg) + Supabase migrations；React 19 + Vite + vitest；pytest。

## Global Constraints

- 与用户沟通中文；**UI 文案英文** + i18n key（en.json/zh.json 双写）
- migration 编号：本计划假定 `368_unified_tags.sql`——**写 PR 时以 master `supabase/migrations/` 最新号 +1 为准**，撞号改名
- schema-drift gate：新表/新列的 SQLAlchemy model **必须同 PR** 加入 `backend/app/models/` 并注册进 `backend/app/models/__init__.py`（import + `__all__`）；FK 必须声明 `ondelete`
- `tags.id` / `note_id` 是 BIGINT Snowflake：Python 内**原生 int**（不 str），API 序列化层才转 string；asyncpg int8 严格，绑参前强转 int
- 后端 push 前：`black --check` + `isort --check` + `flake8`（含 test 文件）；前端：`cd frontend && npm run lint`
- 每 PR 用 `scripts/worktree-manager.sh create <branch>` 建 worktree，**不动用户主 checkout**；切分支后必 `git rev-parse --abbrev-ref HEAD` 断言
- 测试范式：backend repo 测试用 `_FakeSession` boundary-stub（照抄 `backend/tests/test_inspiration_repository.py` 的 `_ScalarResult`/`_FakeSession`/`_cm` 模式，monkeypatch `read_scope`/`write_scope`）；vector/CAST 类 SQL 不适用本计划（无）
- 失败路径显式 raise / 显式 error，不静默吞

---

## PR-0: 中文 IME 回车建标签修复（branch `feature/tags-enter-ime-fix`）

### Task 0.1: EagleTagBrowser — IME 守卫 + name_zh 匹配

**Files:**
- Modify: `frontend/components/EagleTagPicker/EagleTagBrowser.tsx:131-135`（noExactMatch）、`:158-163`（onKeyDown）
- Test: `frontend/components/EagleTagPicker/EagleTagBrowser.enter.test.tsx`（新建）

**Interfaces:**
- Consumes: 现有 `EagleTagBrowserProps`（不变）
- Produces: 行为变化——组合输入中的 Enter 不触发创建；`name_zh` 精确命中时不再显示 Create 行

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/components/EagleTagPicker/EagleTagBrowser.enter.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import { EagleTagBrowser } from './EagleTagBrowser';
import type { Tag } from '../../types';

const tag = (over: Partial<Tag>): Tag => ({
  id: '1', name: 'copywriting', name_zh: '文案', color: null, icon: null,
  type: 'user', created_at: '2026-01-01', ...over,
});

const baseProps = {
  allTags: [tag({})],
  selectedIds: new Set<string>(),
  starredIds: [] as string[],
  settings: { sort: 'name', show_counts: false } as never,
  onToggleTag: vi.fn(),
  onToggleStar: vi.fn(),
  onUpdateSettings: vi.fn(),
  forceMobileLayout: false,
};

describe('EagleTagBrowser enter-to-create', () => {
  it('does not create while IME composition is active', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '新标签' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('creates on Enter after composition ends', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '新标签' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).toHaveBeenCalledWith('新标签', expect.any(String));
  });

  it('treats a name_zh exact match as existing (no create row)', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '文案' } });
    expect(screen.queryByText(/Create "文案"/)).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).not.toHaveBeenCalled();
  });
});
```

注：`fireEvent.keyDown(..., { isComposing: true })` 会写进 nativeEvent；若 jsdom 版本不透传，改用 `const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }); Object.defineProperty(ev, 'isComposing', { value: true }); input.dispatchEvent(ev);`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/EagleTagPicker/EagleTagBrowser.enter.test.tsx`
Expected: FAIL——第 1 个用例 onCreate 被调用（无守卫）、第 3 个用例 Create 行出现（不看 name_zh）

- [ ] **Step 3: 最小实现**

`EagleTagBrowser.tsx` 两处：

```tsx
// noExactMatch（替换 131-135 的 useMemo 体）
const noExactMatch = useMemo(() => {
  if (!search.trim()) return false;
  const q = search.trim().toLowerCase();
  return !allTags.some(
    (tag) =>
      tag.name.toLowerCase() === q ||
      (tag.name_zh ?? '').toLowerCase() === q,
  );
}, [search, allTags]);
```

```tsx
// onKeyDown（替换 158-163）
onKeyDown={(e) => {
  if (e.nativeEvent.isComposing) return; // IME 组合中的 Enter 属于输入法
  if (e.key === 'Enter' && noExactMatch && onCreate) {
    e.preventDefault();
    handleCreate();
  }
}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/EagleTagPicker/EagleTagBrowser.enter.test.tsx`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/EagleTagPicker/EagleTagBrowser.tsx frontend/components/EagleTagPicker/EagleTagBrowser.enter.test.tsx
git commit -m "fix(tags): IME composition guard + name_zh exact-match on enter-to-create"
```

### Task 0.2: 全仓同类 Enter 输入框补 IME 守卫（fix-the-class）

**Files:**
- Modify: `frontend/components/TagsSettings.tsx:747`（create group）、`:793-794`（rename group）、`:1047`（create tag）及同文件其余 `e.key === 'Enter'` 处
- Test: 无新测试（纯守卫插入，行为由 0.1 的测试模式覆盖代表性场景）

**Interfaces:** 无接口变化。

- [ ] **Step 1: 枚举全部场景**

Run: `cd frontend && grep -rn "key === 'Enter'" components/ pages/ | grep -v test | grep -v isComposing`
把输出里**属于"Enter 提交表单/创建"语义**的每一处记下来（跳过导航/换行语义的）。已知至少：`TagsSettings.tsx` 3 处。

- [ ] **Step 2: 逐处加守卫**

统一改法（以 1047 为例）：

```tsx
onKeyDown={(e) => {
  if (e.key === 'Enter' && !e.nativeEvent.isComposing) handleCreateTag();
}}
```

747 / 793 同型：条件里插入 `!e.nativeEvent.isComposing`。

- [ ] **Step 3: 全量前端测试 + lint**

Run: `cd frontend && npm run lint && npx vitest run`
Expected: lint 0 error；vitest 全绿（若有已知无关 flake，单跑失败文件复核）

- [ ] **Step 4: Commit + ship**

```bash
git add -A && git commit -m "fix(tags): IME composition guard for all enter-to-submit tag inputs"
```
然后走 `/ship`（自动 merge base、跑测试、开 PR）。PR 合并后按 `bug_script_editor_narrow_width_clip.md` 的 prod 走查范式真机验证：资源页 Add Tag 中文输入回车建标签成功。

---

## PR-1: 数据层——migration + models + resolve/sync 管线（branch `feature/unified-tags-core`）

### Task 1.1: migration 368 + SQLAlchemy models

**Files:**
- Create: `supabase/migrations/368_unified_tags.sql`
- Modify: `backend/app/models/library.py`（Tags 加 origin）、`backend/app/models/inspiration.py`（新 NoteTags model）、`backend/app/models/__init__.py`（注册）

**Interfaces:**
- Produces: 表 `note_tags(note_id BIGINT, tag_id BIGINT)`；`tags.origin VARCHAR(20) DEFAULT 'curated'`；model 类 `NoteTags`（`app.models.inspiration`）、`Tags.origin`

- [ ] **Step 1: 写 migration**

```sql
-- supabase/migrations/368_unified_tags.sql
-- Unified tags: shadow-tag pool (spec docs/superpowers/specs/2026-07-17-unified-tags-design.md)

-- 1) tags.origin: 'curated' = user-managed shelf; 'note' = auto-created from note #tags
ALTER TABLE tags ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'curated';
ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_origin_check;
ALTER TABLE tags ADD CONSTRAINT tags_origin_check CHECK (origin IN ('curated', 'note'));

-- 2) note_tags junction (mirror of resource_tags)
CREATE TABLE IF NOT EXISTS note_tags (
  note_id BIGINT NOT NULL REFERENCES inspiration_notes(id) ON DELETE CASCADE,
  tag_id  BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (note_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id);

-- RLS: owner-only via the parent note (后端全走 service_role, 策略防未来直连; 模板同 mig349)
ALTER TABLE note_tags ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS note_tags_owner ON note_tags;
CREATE POLICY note_tags_owner ON note_tags
  FOR ALL
  USING (EXISTS (SELECT 1 FROM inspiration_notes n WHERE n.id = note_id AND n.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM inspiration_notes n WHERE n.id = note_id AND n.user_id = auth.uid()));

-- 3) Backfill A: create missing shadow tags (one per user × unseen word)
INSERT INTO tags (name, type, user_id, origin)
SELECT w.word, 'user', w.user_id, 'note'
FROM (
  SELECT DISTINCT n.user_id, lower(t.tag) AS word
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.deleted_at IS NULL
) w
WHERE NOT EXISTS (
  SELECT 1 FROM tags tg
  WHERE (lower(tg.name) = w.word OR lower(tg.name_zh) = w.word)
    AND ((tg.type = 'user' AND tg.user_id = w.user_id) OR tg.type IN ('system', 'time'))
)
ON CONFLICT ON CONSTRAINT unique_tag_per_scope DO NOTHING;

-- 4) Backfill B: link every note word to its resolved tag (spec §5 ranking)
INSERT INTO note_tags (note_id, tag_id)
SELECT n.id, resolved.tag_id
FROM inspiration_notes n
CROSS JOIN LATERAL unnest(n.tags) AS t(tag)
CROSS JOIN LATERAL (
  SELECT tg.id AS tag_id
  FROM tags tg
  WHERE (lower(tg.name) = lower(t.tag) OR lower(tg.name_zh) = lower(t.tag))
    AND ((tg.type = 'user' AND tg.user_id = n.user_id) OR tg.type IN ('system', 'time'))
  ORDER BY (tg.type = 'user') DESC, (lower(tg.name) = lower(t.tag)) DESC, tg.created_at ASC
  LIMIT 1
) resolved
WHERE n.deleted_at IS NULL
ON CONFLICT (note_id, tag_id) DO NOTHING;

-- 5) merge_tags v2: also migrate note_tags (spec §4.5)
CREATE OR REPLACE FUNCTION merge_tags(p_target text, p_sources text[], p_user uuid)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $fn$
DECLARE v_target bigint := p_target::bigint; v_sources bigint[]; v_count bigint;
BEGIN
  SELECT array_agg(DISTINCT s::bigint) INTO v_sources
    FROM unnest(p_sources) AS s WHERE s::bigint <> v_target;
  IF v_sources IS NULL OR array_length(v_sources, 1) IS NULL THEN
    RAISE EXCEPTION 'merge_tags: no source tags to merge';
  END IF;
  INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
  SELECT resource_id, v_target, source, confidence, created_at
    FROM resource_tags WHERE tag_id = ANY(v_sources)
  ON CONFLICT (resource_id, tag_id) DO NOTHING;
  INSERT INTO note_tags (note_id, tag_id, created_at)
  SELECT note_id, v_target, created_at
    FROM note_tags WHERE tag_id = ANY(v_sources)
  ON CONFLICT (note_id, tag_id) DO NOTHING;
  DELETE FROM tags WHERE id = ANY(v_sources);
  SELECT count(*) INTO v_count FROM resource_tags WHERE tag_id = v_target;
  RETURN v_count;
END; $fn$;
NOTIFY pgrst, 'reload schema';
```

注意：Backfill A 的 `INSERT INTO tags` 不带 id 列——`tags.id` 有 `generate_snowflake_id()` server default（见 `backend/app/models/library.py:79` Tags model）。原 267 里对 target/source 归属校验的注释块保留原样（本文件重建函数体时逐字保留 267 的校验 SQL——写 PR 时打开 267 对照，缺了校验就是安全回退）。

- [ ] **Step 2: 本地库 apply**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/368_unified_tags.sql`
Expected: 无 ERROR；`SELECT COUNT(*) FROM note_tags;` 与本地笔记词数量级一致

- [ ] **Step 3: models**

`backend/app/models/library.py` Tags 类内（`sort_order` 行后）加：

```python
    origin: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=False, server_default=text("'curated'")
    )
```

`backend/app/models/inspiration.py` 末尾加（import 区补 `ForeignKey`、`BigInteger` 如缺）：

```python
class NoteTags(Base):
    """Junction: note ↔ tag (mirror of resource_tags). Mig 368."""

    __tablename__ = "note_tags"

    note_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("inspiration_notes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`backend/app/models/__init__.py`：在 inspiration import 行加 `NoteTags`，并加入 `__all__`。

- [ ] **Step 4: 跑 schema-drift gate**

Run: `cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/db/test_schema_drift.py -x -q`
Expected: PASS（origin 列 + note_tags 双向对齐；CHECK/FK/ondelete 全对上）

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/368_unified_tags.sql backend/app/models/
git commit -m "feat(tags): mig368 unified tags — origin column, note_tags junction, backfill, merge v2"
```

### Task 1.2: TagsRepository.resolve_note_tags（含纯函数排序器）

**Files:**
- Modify: `backend/app/repositories/tags_repository.py`
- Test: `backend/tests/test_tags_resolve_note_tags.py`（新建）

**Interfaces:**
- Produces: `async def resolve_note_tags(self, user_id: str, names: list[str]) -> list[int]`（返回与 names 同序去重的 tag id）；纯函数 `pick_note_tag_match(word: str, rows: list[dict]) -> Optional[int]`

- [ ] **Step 1: 写失败测试（纯函数部分 + repo 创建路径）**

```python
# backend/tests/test_tags_resolve_note_tags.py
import pytest

from app.repositories.tags_repository import pick_note_tag_match


def _row(id, name, name_zh=None, type="user", created_at="2026-01-01"):
    return {"id": id, "name": name, "name_zh": name_zh, "type": type, "created_at": created_at}


class TestPickNoteTagMatch:
    def test_own_user_tag_beats_system(self):
        rows = [
            _row(1, "ai", type="system"),
            _row(2, "AI", type="user"),
        ]
        assert pick_note_tag_match("ai", rows) == 2

    def test_name_hit_beats_name_zh_hit(self):
        rows = [
            _row(1, "copywriting", name_zh="文案"),
            _row(2, "文案"),
        ]
        assert pick_note_tag_match("文案", rows) == 2

    def test_oldest_wins_on_tie(self):
        rows = [
            _row(5, "Ai", created_at="2026-03-01"),
            _row(3, "aI", created_at="2026-01-01"),
        ]
        assert pick_note_tag_match("ai", rows) == 3

    def test_no_match_returns_none(self):
        assert pick_note_tag_match("newword", [_row(1, "other")]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_tags_resolve_note_tags.py -q`
Expected: FAIL — ImportError: cannot import name 'pick_note_tag_match'

- [ ] **Step 3: 实现**

`tags_repository.py` 模块级加纯函数：

```python
def pick_note_tag_match(word: str, rows: list[dict]) -> Optional[int]:
    """Spec §5 ranking: own user tag > system/time; name hit > name_zh hit;
    oldest created_at wins. `rows` are candidate tag dicts already filtered
    to lower(name)/lower(name_zh) == word and visibility scope."""
    w = word.lower()

    def _key(r: dict):
        return (
            0 if r.get("type") == "user" else 1,
            0 if (r.get("name") or "").lower() == w else 1,
            str(r.get("created_at") or ""),
        )

    hits = [
        r
        for r in rows
        if (r.get("name") or "").lower() == w or (r.get("name_zh") or "").lower() == w
    ]
    if not hits:
        return None
    return int(sorted(hits, key=_key)[0]["id"])
```

类内加方法（import 区补 `or_`, `and_` 如缺）：

```python
    async def resolve_note_tags(self, user_id: str, names: list[str]) -> list[int]:
        """Resolve parsed note-tag words to tag ids, creating origin='note'
        shadow tags for unseen words (spec §2/§5). Idempotent under races via
        unique_tag_per_scope + re-select."""
        words: list[str] = []
        for n in names:
            w = n.lower()
            if w and w not in words:
                words.append(w)
        if not words:
            return []
        resolved: dict[str, int] = {}
        async with write_scope() as session:
            stmt = select(
                Tags.id, Tags.name, Tags.name_zh, Tags.type, Tags.created_at
            ).where(
                or_(
                    func.lower(Tags.name).in_(words),
                    func.lower(Tags.name_zh).in_(words),
                ),
                or_(
                    and_(Tags.type == "user", Tags.user_id == user_id),
                    Tags.type.in_(("system", "time")),
                ),
            )
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
            for w in words:
                hit = pick_note_tag_match(w, rows)
                if hit is not None:
                    resolved[w] = hit
            for w in words:
                if w in resolved:
                    continue
                ins = (
                    pg_insert(Tags)
                    .values(name=w, type="user", user_id=user_id, origin="note")
                    .on_conflict_do_nothing(
                        index_elements=["name", "type", "user_id"]
                    )
                    .returning(Tags.id)
                )
                new_id = (await session.execute(ins)).scalar()
                if new_id is None:  # lost the race — re-select
                    new_id = (
                        await session.execute(
                            select(Tags.id).where(
                                Tags.name == w,
                                Tags.type == "user",
                                Tags.user_id == user_id,
                            )
                        )
                    ).scalar()
                if new_id is None:
                    raise RuntimeError(f"resolve_note_tags: failed to ensure tag '{w}'")
                resolved[w] = int(new_id)
        return [resolved[w] for w in words]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_tags_resolve_note_tags.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/tags_repository.py backend/tests/test_tags_resolve_note_tags.py
git commit -m "feat(tags): resolve_note_tags — shadow-tag creation with spec §5 ranking"
```

### Task 1.3: NoteTagsRepository（junction diff 同步）

**Files:**
- Create: `backend/app/repositories/note_tags_repository.py`
- Test: `backend/tests/test_note_tags_repository.py`（新建）

**Interfaces:**
- Consumes: `NoteTags` model（Task 1.1）
- Produces: `class NoteTagsRepository` + 工厂 `get_note_tags_repository()`；方法 `async def sync_for_note(self, note_id: int, tag_ids: list[int]) -> None`、`async def counts_for_user(self, user_id: str) -> dict[int, int]`（tag_id → note 数，PR-2 statistics 用）

- [ ] **Step 1: 写失败测试**

照抄 `backend/tests/test_inspiration_repository.py` 顶部的 `_ScalarResult`/`_FakeSession`/`_cm` 三件套（逐字复制到新文件，那是仓库的 boundary-stub 契约），然后：

```python
# backend/tests/test_note_tags_repository.py（fake 三件套之后）
import pytest

import app.repositories.note_tags_repository as mod
from app.repositories.note_tags_repository import NoteTagsRepository


@pytest.mark.asyncio
async def test_sync_inserts_missing_and_deletes_stale(monkeypatch):
    # 现有关联 {11, 12}；目标 {12, 13} → insert 13, delete 11
    session = _FakeSession(results=[_ScalarResult([11, 12]), _ScalarResult(None), _ScalarResult(None)])
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [12, 13])
    sql = " ".join(str(s) for s in session.statements)
    assert "INSERT INTO note_tags" in sql
    assert "DELETE FROM note_tags" in sql


@pytest.mark.asyncio
async def test_sync_noop_when_equal(monkeypatch):
    session = _FakeSession(results=[_ScalarResult([11])])
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [11])
    assert len(session.statements) == 1  # 只有 select，无写
```

（`_FakeSession(results=[...])` 若原文件构造方式不同，按原文件真实签名对齐——契约是"逐字照抄那套 fake"，断言按本任务语义写。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_note_tags_repository.py -q`
Expected: FAIL — ModuleNotFoundError: app.repositories.note_tags_repository

- [ ] **Step 3: 实现**

```python
# backend/app/repositories/note_tags_repository.py
"""Note ↔ tag junction access (mirror of resource_tags usage)."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models.inspiration import InspirationNotes, NoteTags


class NoteTagsRepository:
    async def sync_for_note(self, note_id: int, tag_ids: list[int]) -> None:
        """Diff-sync the junction to exactly `tag_ids` (spec §4.1)."""
        nid = int(note_id)
        wanted = {int(t) for t in tag_ids}
        async with write_scope() as session:
            existing = set(
                (await session.execute(
                    select(NoteTags.tag_id).where(NoteTags.note_id == nid)
                )).scalars().all()
            )
            to_add = sorted(wanted - existing)
            to_del = sorted(existing - wanted)
            if to_add:
                await session.execute(
                    pg_insert(NoteTags)
                    .values([{"note_id": nid, "tag_id": t} for t in to_add])
                    .on_conflict_do_nothing()
                )
            if to_del:
                await session.execute(
                    delete(NoteTags).where(
                        NoteTags.note_id == nid, NoteTags.tag_id.in_(to_del)
                    )
                )

    async def counts_for_user(self, user_id: str) -> dict[int, int]:
        """tag_id → count of live notes for this user (statistics, spec §4.3)."""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(NoteTags.tag_id, func.count().label("cnt"))
                    .join(InspirationNotes, InspirationNotes.id == NoteTags.note_id)
                    .where(
                        InspirationNotes.user_id == user_id,
                        InspirationNotes.deleted_at.is_(None),
                    )
                    .group_by(NoteTags.tag_id)
                )
            ).all()
        return {int(r[0]): int(r[1]) for r in rows}


_repo: NoteTagsRepository | None = None


def get_note_tags_repository() -> NoteTagsRepository:
    global _repo
    if _repo is None:
        _repo = NoteTagsRepository()
    return _repo
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_note_tags_repository.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/note_tags_repository.py backend/tests/test_note_tags_repository.py
git commit -m "feat(tags): NoteTagsRepository — diff sync + per-user counts"
```

### Task 1.4: NotesService 接线（保存管线）

**Files:**
- Modify: `backend/app/services/inspiration/notes_service.py`（create_note / update_note）
- Test: `backend/tests/test_inspiration_notes_service.py`（追加用例）

**Interfaces:**
- Consumes: `resolve_note_tags(user_id, names)`（Task 1.2）、`sync_for_note(note_id, tag_ids)`（Task 1.3）、现有 `parse_tags(content_md)`
- Produces: 保存/编辑笔记后 note_tags 与正文一致；失败显式抛出

- [ ] **Step 1: 写失败测试**

在 `test_inspiration_notes_service.py` 现有 mock 风格上追加（该文件已有 service 构造与 notes repo mock 的 fixture，沿用）：

```python
@pytest.mark.asyncio
async def test_create_note_syncs_note_tags(service, notes_repo, monkeypatch):
    import app.services.inspiration.notes_service as svc_mod

    resolve_calls, sync_calls = [], []

    class _FakeTagsRepo:
        async def resolve_note_tags(self, user_id, names):
            resolve_calls.append((user_id, names))
            return [101, 102]

    class _FakeNoteTagsRepo:
        async def sync_for_note(self, note_id, tag_ids):
            sync_calls.append((note_id, tag_ids))

    monkeypatch.setattr(svc_mod, "get_tags_repository", lambda: _FakeTagsRepo())
    monkeypatch.setattr(svc_mod, "get_note_tags_repository", lambda: _FakeNoteTagsRepo())
    notes_repo.create.return_value = {"id": 7, "content_md": "x #ai #ml", "tags": ["ai", "ml"]}

    await service.create_note("user-1", "x #ai #ml")

    assert resolve_calls == [("user-1", ["ai", "ml"])]
    assert sync_calls == [(7, [101, 102])]


@pytest.mark.asyncio
async def test_update_note_without_content_change_skips_tag_sync(service, notes_repo, monkeypatch):
    import app.services.inspiration.notes_service as svc_mod

    called = []
    monkeypatch.setattr(
        svc_mod, "get_tags_repository",
        lambda: type("R", (), {"resolve_note_tags": lambda s, u, n: called.append(1)})(),
    )
    await service.update_note("user-1", "7", pinned=True)  # content_md=None
    assert called == []
```

（fixture 名以该文件真实命名为准；若无现成 fixture，按文件里第一个测试的构造方式建 service。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_inspiration_notes_service.py -q`
Expected: 新增 2 例 FAIL（service 尚未调用 resolve/sync）

- [ ] **Step 3: 实现**

`notes_service.py` import 区加：

```python
from app.repositories.note_tags_repository import get_note_tags_repository
from app.repositories.tags_repository import get_tags_repository
```

`create_note` 在 `row = await self._notes.create(...)` 之后、return 之前加：

```python
        await self._sync_pool_tags(user_id, row)
```

`update_note` 在成功更新且 `content_md is not None` 分支后加同一调用（row 为更新后的行）。类内加私有方法：

```python
    async def _sync_pool_tags(self, user_id: str, row: dict) -> None:
        """Keep the global tag pool + note_tags junction in step with the
        note body (spec §4.1). content_md is the source of truth; this is
        derived data — any drift self-heals on the next save."""
        names = list(row.get("tags") or [])
        tag_ids = await get_tags_repository().resolve_note_tags(user_id, names)
        await get_note_tags_repository().sync_for_note(int(row["id"]), tag_ids)
```

事务口径（spec §8 的落地修正，写进 PR 描述）：note 行写入与标签同步是两个事务（仓库 per-call session 惯例）；同步失败会把错误抛给调用方（保存接口报错），已写入的 note 行在下次保存时自愈——不引入跨 repo session 传递。

- [ ] **Step 4: 跑通 + 全套 inspiration 测试**

Run: `cd backend && uv run pytest tests/test_inspiration_notes_service.py tests/test_inspiration_router.py tests/test_inspiration_repository.py -q`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/inspiration/notes_service.py backend/tests/test_inspiration_notes_service.py
git commit -m "feat(inspiration): note save pipeline syncs global tag pool via shadow tags"
```

### Task 1.5: 晋升端点（origin note → curated）

**Files:**
- Modify: `backend/app/schemas/tags.py`（TagUpdate 加 origin）、`backend/app/api/tags_router.py`（update 校验）、`backend/app/repositories/tags_repository.py`（update_tag kwargs 白名单如有需放行 origin）
- Test: `backend/tests/test_tags_router.py` 或现有 tags 路由测试文件追加

**Interfaces:**
- Produces: `PUT /api/v1/tags/{id}` body 接受 `origin: "curated"`（仅允许 note→curated 单向；传 `"note"` 一律 422）

- [ ] **Step 1: 写失败测试**

在现有 tags 路由测试文件（`grep -l "tags_router\|/api/v1/tags" backend/tests/*.py` 找到）追加：

```python
@pytest.mark.asyncio
async def test_promote_shadow_tag(client, auth_headers, mock_tags_repo):
    mock_tags_repo.update_tag.return_value = {"id": 1, "name": "ai", "origin": "curated"}
    resp = await client.put("/api/v1/tags/1", json={"origin": "curated"}, headers=auth_headers)
    assert resp.status_code == 200
    assert mock_tags_repo.update_tag.call_args.kwargs["origin"] == "curated"


@pytest.mark.asyncio
async def test_demote_to_note_rejected(client, auth_headers):
    resp = await client.put("/api/v1/tags/1", json={"origin": "note"}, headers=auth_headers)
    assert resp.status_code == 422
```

（client/mock fixture 命名以该文件现状为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/ -k "promote or demote" -q`
Expected: FAIL（schema 不认识 origin / 校验缺失）

- [ ] **Step 3: 实现**

`schemas/tags.py` TagUpdate 加：

```python
    origin: Optional[Literal["curated"]] = None  # promote-only; 'note' is never settable via API
```

（用 `Literal["curated"]`，"note" 直接被 Pydantic 422，无需手写校验。）router 的 update 端点把 `origin` 透传进 `repo.update_tag(tag_id, user_id, **updates)`；`update_tag` 若有列白名单，加入 `origin`。响应 model（TagResponse/TagOut）加 `origin: str = "curated"` 字段，`list`/`get` 端点自然带出。

- [ ] **Step 4: 跑通**

Run: `cd backend && uv run pytest tests/ -k "tags" -q`
Expected: PASS 全绿

- [ ] **Step 5: lint + Commit + ship**

```bash
cd backend && uv run black app tests && uv run isort app tests && uv run flake8 app tests
git add -A && git commit -m "feat(tags): origin field exposure + promote-only endpoint"
```
走 `/ship`（PR-1）。PR 描述里写明：mig368 需在 prod apply（走 CI migration 流程，参考 `reference_prod_migration_apply_as_postgres.md`）。

---

## PR-2: 端点——跨域 statistics + 热点 tag 筛选（branch `feature/unified-tags-endpoints`）

### Task 2.1: /tags/statistics 跨域用量

**Files:**
- Modify: `backend/app/api/tags_router.py`（get_tag_statistics）、`backend/app/schemas/tags.py`（响应 model）、`backend/app/repositories/hotspots_repository.py`（新方法 recent_tag_word_counts）
- Test: 现有 tags 路由测试 + `backend/tests/test_hotspots_repository.py` 追加

**Interfaces:**
- Consumes: `NoteTagsRepository.counts_for_user(user_id) -> dict[int, int]`（Task 1.3）
- Produces: statistics 每项新增 `notes: int`、`hotspots: int`（现有 `count` 字段语义不变 = resources）；`HotspotsRepository.recent_tag_word_counts(days: int = 30) -> dict[str, int]`（lower word → 命中条数）

- [ ] **Step 1: 写失败测试**

hotspots repo 测试（boundary-stub 三件套照抄）：

```python
@pytest.mark.asyncio
async def test_recent_tag_word_counts_sql(monkeypatch):
    session = _FakeSession(results=[_RowResult([("ai", 5), ("文案", 2)])])
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    out = await HotspotsRepository().recent_tag_word_counts(days=30)
    assert out == {"ai": 5, "文案": 2}
    sql = str(session.statements[0])
    assert "unnest" in sql and "captured_at" in sql
```

router 测试：statistics mock 三个数据源，断言响应里 `notes`/`hotspots` 字段按 name/name_zh 匹配拼装：

```python
@pytest.mark.asyncio
async def test_statistics_cross_domain(client, auth_headers, mock_tags_repo, monkeypatch):
    mock_tags_repo.get_tag_counts.return_value = [
        {"id": 1, "name": "copywriting", "name_zh": "文案", "color": None, "icon": None, "type": "user", "count": 3},
    ]
    # note counts: tag 1 → 4 ; hotspot words: 文案 → 2
    ...monkeypatch note_tags counts_for_user -> {1: 4}
    ...monkeypatch hotspots recent_tag_word_counts -> {"文案": 2}
    resp = await client.get("/api/v1/tags/statistics", headers=auth_headers)
    item = resp.json()["tags"][0]
    assert (item["count"], item["notes"], item["hotspots"]) == (3, 4, 2)
```

（`...monkeypatch` 两行按该测试文件的依赖注入方式落实——工厂函数 monkeypatch 或 dependency_overrides，与 Task 1.4 同法；响应外层键名以现有 TagStatisticsResponse 为准。）

- [ ] **Step 2: 跑失败** — `uv run pytest tests/ -k "statistics or recent_tag_word" -q` → FAIL

- [ ] **Step 3: 实现**

`hotspots_repository.py`：

```python
    async def recent_tag_word_counts(self, days: int = 30) -> dict[str, int]:
        """lower(word) → hotspot count within the window (spec §4.3).
        One aggregate over unnest(tags); callers match by name/name_zh."""
        stmt = text(
            "SELECT lower(w.word) AS word, COUNT(*)::bigint AS cnt "
            "FROM hotspots h, LATERAL unnest(h.tags) AS w(word) "
            "WHERE h.captured_at >= now() - make_interval(days => :days) "
            "GROUP BY lower(w.word)"
        )
        async with read_scope() as session:
            rows = (await session.execute(stmt, {"days": int(days)})).all()
        return {str(r[0]): int(r[1]) for r in rows}
```

router `get_tag_statistics`：三源并发取数后拼装：

```python
    tag_counts = await repo.get_tag_counts(user_id, limit)
    note_counts, hotspot_words = await asyncio.gather(
        get_note_tags_repository().counts_for_user(user_id),
        get_hotspots_repository().recent_tag_word_counts(),
    )
    for t in tag_counts:
        words = {(t.get("name") or "").lower(), (t.get("name_zh") or "").lower()} - {""}
        t["notes"] = note_counts.get(int(t["id"]), 0)
        t["hotspots"] = sum(hotspot_words.get(w, 0) for w in words)
```

schema：statistics item model 加 `notes: int = 0`、`hotspots: int = 0`。

注意：`get_tag_counts` 现有 RPC（256）不返回 name_zh——fallback 路径返回 dict 里补 name_zh；若 RPC 路径拿不到 name_zh，router 里用 `repo.get_all_tags` 的 5s 内存缓存/一次查询补全 name_zh（一次 in-list 查询，不 N+1）。

- [ ] **Step 4: 跑通** — `uv run pytest tests/ -k "tags or hotspots" -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(tags): cross-domain usage in /tags/statistics (resources+notes+hotspots)"
```

### Task 2.2: GET /topics 按标签筛选

**Files:**
- Modify: `backend/app/api/topics_router.py`（list_hotspots 加参数）、`backend/app/repositories/hotspots_repository.py`（list_for_date 加 tag_words）、`backend/app/repositories/tags_repository.py`（get_tags_by_ids 如无则加）
- Test: 现有 topics 路由测试 + hotspots repo 测试追加

**Interfaces:**
- Produces: `GET /api/v1/topics?tag_id=1,2` — 后端把 id 解析为 name/name_zh 词集，`hotspots.tags && words`；`list_for_date(..., tag_words: Optional[list[str]] = None)`

- [ ] **Step 1: 写失败测试**

repo 测试：

```python
@pytest.mark.asyncio
async def test_list_for_date_tag_words_overlap(monkeypatch):
    session = _FakeSession(results=[_MappingResult([])])
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    await HotspotsRepository().list_for_date(None, None, tag_words=["ai", "文案"])
    sql = str(session.statements[0]).lower()
    assert "&&" in sql or "overlap" in sql
```

router 测试：mock tags repo 返回 `{name: 'copywriting', name_zh: '文案'}`，断言 repo 收到 `tag_words=['copywriting', '文案']`（lower 后）。

- [ ] **Step 2: 跑失败** — Expected: TypeError: unexpected keyword 'tag_words'

- [ ] **Step 3: 实现**

`list_for_date` 签名尾部加 `tag_words: Optional[list[str]] = None`，query 构建处加：

```python
        if tag_words:
            stmt = stmt.where(Hotspots.tags.overlap([w.lower() for w in tag_words]))
```

`topics_router.list_hotspots` 加参数 `tag_id: Optional[str] = Query(None, description="comma-separated tag ids")`，解析：

```python
    tag_words: Optional[list[str]] = None
    if tag_id:
        ids = [int(x) for x in tag_id.split(",") if x.strip().isdigit()][:20]
        if ids:
            tag_rows = await get_tags_repository().get_tags_by_ids(ids)
            tag_words = [
                w.lower()
                for t in tag_rows
                for w in (t.get("name"), t.get("name_zh"))
                if w
            ]
        if not tag_words:
            tag_words = [" __no_match__"]  # 显式空集：选了无效标签就返回空，不静默放行
```

`get_tags_by_ids(ids: list[int]) -> list[dict]`（tags_repository，如无）：`select(Tags).where(Tags.id.in_(ids))` → `_tag_row` 列表。`view=saved/hidden/foryou/featured` 各分支同样透传 tag_words（这些分支走 `list_by_ids` 的，在 id 集过滤后于 router 层按 words 过滤 `hotspot["tags"]` 交集——分支行为在测试里各写一例）。

- [ ] **Step 4: 跑通** — `uv run pytest tests/ -k "topics or hotspots" -q` → PASS

- [ ] **Step 5: lint + Commit + ship**

```bash
cd backend && uv run black app tests && uv run isort app tests && uv run flake8 app tests
git add -A && git commit -m "feat(topics): filter hotspots by pool tag (name/name_zh overlap)"
```
走 `/ship`（PR-2）。

---

## PR-3: 前端体验（branch `feature/unified-tags-frontend`）

### Task 3.1: Tag.origin 类型 + picker 影子过滤

**Files:**
- Modify: `frontend/types.ts:428`（Tag 加 origin）、`frontend/components/EagleTagPicker/index.tsx`
- Test: `frontend/components/EagleTagPicker/shadowFilter.test.ts`（新建，纯逻辑）

**Interfaces:**
- Produces: `Tag.origin?: 'curated' | 'note'`；导出纯函数 `filterPickerTags(allTags: Tag[], selectedIds: Set<string>): Tag[]`（index.tsx 内导出，影子标签仅在已被选中时保留）

- [ ] **Step 1: 失败测试**

```ts
// frontend/components/EagleTagPicker/shadowFilter.test.ts
import { describe, it, expect } from 'vitest';
import { filterPickerTags } from './index';
import type { Tag } from '../../types';

const t = (id: string, origin?: 'curated' | 'note'): Tag =>
  ({ id, name: id, color: null, icon: null, type: 'user', created_at: '', origin }) as Tag;

describe('filterPickerTags', () => {
  it('hides shadow tags by default', () => {
    expect(filterPickerTags([t('1'), t('2', 'note')], new Set())).toHaveLength(1);
  });
  it('keeps a shadow tag that is already assigned', () => {
    expect(filterPickerTags([t('2', 'note')], new Set(['2']))).toHaveLength(1);
  });
  it('keeps curated and undefined-origin tags', () => {
    expect(filterPickerTags([t('1', 'curated'), t('3')], new Set())).toHaveLength(2);
  });
});
```

- [ ] **Step 2: 跑失败** — `npx vitest run components/EagleTagPicker/shadowFilter.test.ts` → FAIL (no export)

- [ ] **Step 3: 实现**

`types.ts` Tag 接口加 `origin?: 'curated' | 'note';`。`index.tsx`：

```tsx
export function filterPickerTags(allTags: Tag[], selectedIds: Set<string>): Tag[] {
  return allTags.filter((t) => t.origin !== 'note' || selectedIds.has(String(t.id)));
}
```

组件内：

```tsx
const pickerTags = useMemo(() => filterPickerTags(allTags, selectedIds), [allTags, selectedIds]);
```

`FloatingPanel` 的 `allTags={allTags}` 改传 `pickerTags`（Mode 2 的 `displayTags` 仍用原 `allTags`，已选影子标签的 pill 不消失）。

- [ ] **Step 4: 跑通** — vitest 该文件 PASS；`npx vitest run components/EagleTagPicker/` 全绿

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(tags): shadow tags hidden from resource picker by default"`

### Task 3.2: 笔记 # 补全接标签池

**Files:**
- Modify: `frontend/pages/InspirationPage.tsx`（39 行 state 附近 + 319 行 tagSuggestions）
- Test: `frontend/pages/InspirationPage.tagSuggestions.test.ts`（新建纯函数测试）

**Interfaces:**
- Consumes: `fetchAllTags()`（unifiedTagService，5s 缓存）
- Produces: 导出纯函数 `buildTagSuggestions(noteTags: { tag: string; cnt: number }[], poolTags: Tag[]): string[]`——curated 池词优先（name_zh 有则并列建议）、其后笔记历史词，lower 去重

- [ ] **Step 1: 失败测试**

```ts
import { describe, it, expect } from 'vitest';
import { buildTagSuggestions } from './InspirationPage';
import type { Tag } from '../types';

const pool = (name: string, name_zh?: string, origin: 'curated' | 'note' = 'curated'): Tag =>
  ({ id: name, name, name_zh, color: null, icon: null, type: 'user', created_at: '', origin }) as Tag;

describe('buildTagSuggestions', () => {
  it('puts curated pool names first, then note history, deduped', () => {
    const out = buildTagSuggestions(
      [{ tag: 'ai', cnt: 3 }, { tag: 'scratch', cnt: 1 }],
      [pool('ai'), pool('copywriting', '文案')],
    );
    expect(out).toEqual(['ai', 'copywriting', '文案', 'scratch']);
  });
  it('excludes shadow pool tags (they are already note history)', () => {
    const out = buildTagSuggestions([{ tag: 'x', cnt: 1 }], [pool('x', undefined, 'note')]);
    expect(out).toEqual(['x']);
  });
});
```

- [ ] **Step 2: 跑失败** — no export

- [ ] **Step 3: 实现**

`InspirationPage.tsx` 导出：

```tsx
export function buildTagSuggestions(
  noteTags: { tag: string; cnt: number }[],
  poolTags: Tag[],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const push = (w: string | null | undefined) => {
    const v = (w || '').trim();
    if (!v || seen.has(v.toLowerCase())) return;
    seen.add(v.toLowerCase());
    out.push(v);
  };
  for (const t of poolTags) {
    if (t.origin === 'note') continue;
    push(t.name);
    push(t.name_zh);
  }
  for (const n of noteTags) push(n.tag);
  return out;
}
```

页面里加 `const [poolTags, setPoolTags] = useState<Tag[]>([]);`，加载 effect（与 getTagCounts 并列）：

```tsx
useEffect(() => {
  fetchAllTags().then(setPoolTags).catch((err) => console.error('fetchAllTags failed', err));
}, []);
```

319 行改：`tagSuggestions={buildTagSuggestions(tags, poolTags)}`（Composer 与编辑弹窗两处传参点都改）。

- [ ] **Step 4: 跑通** — 该测试 + `npx vitest run pages/ components/Inspiration/` 全绿

- [ ] **Step 5: Commit** — `git commit -am "feat(inspiration): # completion suggests curated pool tags first"`

### Task 3.3: Tags 管理页 From notes 区 + Promote

**Files:**
- Modify: `frontend/components/TagsSettings.tsx`、`frontend/public/locales/en.json` + `zh.json`
- Test: `frontend/components/TagsSettings.promote.test.tsx`（新建）

**Interfaces:**
- Consumes: `updateTag(tagId, { origin: 'curated' })`（unifiedTagService，TagUpdate 类型加 `origin?: 'curated'`）
- Produces: 管理页主列表默认排除 origin='note'；折叠区 "From notes" 列出影子标签 + Promote 按钮；晋升后乐观移入主列表

- [ ] **Step 1: 失败测试**

```tsx
// 渲染 TagsSettings（按现有 TagsSettings 依赖 mock unifiedTagService 模块）：
vi.mock('../services/unifiedTagService', () => ({ ...真实导出名按文件为准,
  fetchAllTags: vi.fn().mockResolvedValue([
    { id: '1', name: 'ai', origin: 'curated', type: 'user', color: null, icon: null, created_at: '' },
    { id: '2', name: 'scratch', origin: 'note', type: 'user', color: null, icon: null, created_at: '' },
  ]),
  updateTag: vi.fn().mockResolvedValue({ id: '2', name: 'scratch', origin: 'curated' }),
}));
// 断言 1: 'scratch' 不在主网格里，展开 "From notes" 后可见
// 断言 2: 点 Promote → updateTag 被调用 ('2', { origin: 'curated' })，行移入主列表
```

（TagsSettings 数据获取入口如非 fetchAllTags，以文件真实 import 为准 mock；测试结构参照 `frontend/components/Inspiration/Composer.test.tsx` 的组件级模式。）

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现**

- `unifiedTagService.ts` 的 `TagUpdate` 类型加 `origin?: 'curated'`。
- `TagsSettings.tsx`：主列表数据源处加 `.filter((t) => t.origin !== 'note')`；侧栏 "Uncategorized" 之后加一个折叠 section：

```tsx
{shadowTags.length > 0 && (
  <div className="mt-4 border-t border-ink-800 pt-3">
    <button onClick={() => setShowShadow(!showShadow)}
      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-ink-400 hover:bg-ink-800">
      <NotebookPen size={15} />
      <span className="flex-1 text-left font-medium">{t('settings.tags.fromNotes', 'From notes')}</span>
      <span className="text-xs text-ink-500">{shadowTags.length}</span>
    </button>
    {showShadow && shadowTags.map((tag) => (
      <div key={tag.id} className="flex items-center gap-2 px-3 py-1.5 text-sm text-ink-300">
        <span className="flex-1 truncate">#{tag.name}</span>
        <button onClick={() => handlePromote(tag)}
          className="text-xs text-indigo-400 hover:text-indigo-300">
          {t('settings.tags.promote', 'Promote')}
        </button>
      </div>
    ))}
  </div>
)}
```

```tsx
const shadowTags = useMemo(() => tags.filter((t) => t.origin === 'note'), [tags]);
const [showShadow, setShowShadow] = useState(false);
const handlePromote = async (tag: Tag) => {
  try {
    const updated = await updateTag(String(tag.id), { origin: 'curated' });
    setTags((prev) => prev.map((t) => (String(t.id) === String(tag.id) ? { ...t, ...updated } : t)));
  } catch (err) {
    setError(err instanceof Error ? err.message : 'Failed to promote tag');
  }
};
```

- 改名/删除弹窗对 `notes` 用量 > 0 的标签补一行说明（statistics 数据已含 notes 字段，若该弹窗无用量数据则仅对 origin==='note' 显示）：i18n key `settings.tags.renameNoteHint` = "Notes keep their original #text; editing a note re-creates the old tag."（zh: "笔记正文中的 #原词不会改写，重新编辑笔记会重建旧标签"）。
- en.json/zh.json 补 `settings.tags.fromNotes` / `promote` / `renameNoteHint` 三组。

- [ ] **Step 4: 跑通** — 该测试 + lint 全绿

- [ ] **Step 5: Commit** — `git commit -am "feat(tags): From-notes shadow section with one-click promote"`

### Task 3.4: 热点 Tag 筛选 chip

**Files:**
- Modify: `frontend/components/TopicInspiration/TopicFilterBar.tsx`、`frontend/services/topicService.ts:59`（getHotspots）、TopicFilterBar 的父页面（`grep -rn "TopicFilterBar" frontend/pages frontend/components` 定位，接 state + 重新拉取）
- Test: `frontend/components/TopicInspiration/TopicFilterBar.test.tsx`（如已有则追加）

**Interfaces:**
- Consumes: PR-2 的 `GET /topics?tag_id=...`；`fetchAllTags()`
- Produces: `getHotspots(day?, category?, q?, view?, sources?, tagIds?: string[])`；TopicFilterBar 新 props `allTags: Tag[]; selectedTagIds: string[]; onTagIdsChange: (ids: string[]) => void`

- [ ] **Step 1: 失败测试**

```tsx
// getHotspots 追加参数的 service 测试（照现有 topicService 测试文件模式，若无则新建 fetch mock 测试）：
it('appends tag_id param', async () => {
  const spy = mockFetchReturning({ hotspots: [] });
  await getHotspots(undefined, undefined, undefined, 'all', undefined, ['1', '2']);
  expect(spy.lastUrl).toContain('tag_id=1%2C2');
});
// TopicFilterBar: 渲染后出现 Tag chip；选中标签触发 onTagIdsChange
```

- [ ] **Step 2: 跑失败**

- [ ] **Step 3: 实现**

`topicService.ts`：

```ts
export async function getHotspots(
  day?: string, category?: string, q?: string,
  view: HotspotView = 'all', sources?: string[], tagIds?: string[],
): Promise<Hotspot[]> {
  // 现有 params 组装处追加：
  if (tagIds && tagIds.length) params.set('tag_id', tagIds.join(','));
```

`TopicFilterBar.tsx`：`ChipId` 加 `'tag'`；`ICONS` 加 `tag: TagIcon`（lucide `Tag` 更名导入避免与类型撞名）；props 加上述三个；`summaries.tag` = 选中数或首个标签名；`isActive.tag = selectedTagIds.length > 0`；`clearAll` 里调 `onTagIdsChange([])`；JSX 中 Source chip 之后加：

```tsx
{chip('tag', t('topic.filterTag', 'Tag'), (
  <div className="max-h-64 overflow-y-auto py-1">
    {allTags.filter((tg) => tg.origin !== 'note').map((tg) => (
      <button key={tg.id}
        onClick={() => {
          const id = String(tg.id);
          onTagIdsChange(
            selectedTagIds.includes(id)
              ? selectedTagIds.filter((x) => x !== id)
              : [...selectedTagIds, id],
          );
        }}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs rounded ${
          selectedTagIds.includes(String(tg.id))
            ? 'bg-indigo-500/15 text-indigo-400'
            : 'text-ink-300 hover:bg-ink-800'
        }`}>
        <span className="w-2 h-2 rounded-full" style={{ background: tg.color || '#6366f1' }} />
        <span className="flex-1 text-left truncate">{tg.name_zh || tg.name}</span>
      </button>
    ))}
  </div>
), () => onTagIdsChange([]))}
```

父页面：`const [tagFilterIds, setTagFilterIds] = useState<string[]>([]);` + `fetchAllTags()` 加载 + 传 props + `getHotspots(..., tagFilterIds)` 依赖数组加入 `tagFilterIds`。i18n：`topic.filterTag` en "Tag" / zh "标签"。

- [ ] **Step 4: 跑通 + 全量**

Run: `cd frontend && npm run lint && npx vitest run`
Expected: 全绿（无关 flake 单跑复核）

- [ ] **Step 5: Commit + ship**

```bash
git add -A && git commit -m "feat(topics): filter hotspots by pool tags; unified tags frontend polish"
```
走 `/ship`（PR-3）。合并部署后按 spec §7 真机走查：中文回车建标签（PR-0 回归）→ 笔记打 #新词 → Settings→Tags 出现 From notes → Promote → 资源 picker 可见 → 热点 Tag chip 筛选。

---

## Self-review 记录

- Spec 覆盖：§3 schema→1.1；§4.1→1.2/1.3/1.4；§4.3→2.1；§4.4→2.2；§4.5 merge→1.1(step1 第5段)；§5 匹配→1.2 纯函数+1.1 回填 SQL 同一 ranking；§6 语义→3.3 提示文案；§7 前端 1-4→3.1-3.4（§7.5 TagsPanel 颜色显示 = spec 标注"非本期必须"，不排）；§8→1.2 raise/1.4 事务口径/2.2 空集哨兵；PR-0→0.1/0.2。
- 类型一致性：`resolve_note_tags -> list[int]`（1.2 产出 = 1.4 消费）；`sync_for_note(note_id: int, tag_ids: list[int])`（1.3 = 1.4）；`counts_for_user -> dict[int,int]`（1.3 = 2.1）；`filterPickerTags`/`buildTagSuggestions` 签名测试与实现一致。
- 已知留白（刻意）：路由测试的 fixture 命名依赖各测试文件现状（计划标注"以文件为准"）——子代理执行时先读目标测试文件再落笔，这是仓库测试文件命名不统一下的最小妥协。
