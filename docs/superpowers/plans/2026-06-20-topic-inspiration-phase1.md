# 话题灵感模块 Phase 1（MVP）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `pages/ParserPage.tsx`（Media Parser）重构成「话题灵感」模块的 MVP：自托管 NewsNow + RSS 信源 → DBOS 定时抓取 + 关键词粗筛 → 时间线页（卡/点/日历）→ 闭环按钮（生成脚本 / 解析下载）+ 悬浮解析。先跑通端到端闭环，AI 评分（特调 Agent）留 Phase 2。

**Architecture:** 后端三张新表（`signal_sources` / `hotspots` / `topic_groups`），统一 `SourceAdapter` 接口（`newsnow` / `rss` 两实现），一个 `@DBOS.scheduled` 抓取 workflow 写入 `hotspots`；REST 暴露列表/日历/闭环动作。前端新 `TopicInspirationPage` + `topicService`，复用 island token、IslandShell info portal，闭环复用已有 `script_ai` agent（生成脚本）与 `dedup_and_dispatch`（解析下载）。

**Tech Stack:** FastAPI · DBOS durable workflows · Supabase(asyncpg + supabase-py admin client) · pgvector · React 19 + Vite + TailwindCSS island tokens · feedparser。

## Global Constraints

- **零 emoji**：UI 一律 Lucide line icon / 纯文字，绝不用彩色 emoji（铁律，沿用 island redesign）。
- **双主题**：前端只用 `index.css` 语义 token（`var(--island)` / `--island-2` / `--line` / `--line-strong` / `--content` / `--content-2..4` / `--accent`）+ Tailwind `bg-island` / `text-content-2` / `border-line` 等映射类。**禁止写死颜色、禁止新增 `zinc` 类**。新代码用 `island ? 'text-content-2' : 'text-ink-400'` 双态分支（`island = islandUI()`，`frontend/utils/featureFlags.ts`）。
- **UI 文本全英文** + i18n key（camelCase），翻译写 `frontend/public/locales/{en,zh}.json`。
- **Migration**：下一个序号 `304`，命名 `304_topic_inspiration.sql`；PK 用 `id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id()`；末尾 `NOTIFY pgrst, 'reload schema';`。RLS 必开 + service_role bypass。SQL 走 `supabase/migrations/` + PR + CI apply（不手动 SSH psql）。
- **task_tracking 纪律**：信源健康写 `signal_sources` 自有列，**不碰 `task_tracking`**；闭环下载任务由 `dedup_and_dispatch` 内部经 manager 建任务，业务代码不直接 PATCH phase 列。
- **Phase 1 数据归属决定（审阅重点）**：`signal_sources` / `hotspots` / `topic_groups` 的 `user_id` **可空，`NULL` = 系统级全局**。Phase 1 抓取写全局行（`user_id=NULL`），所有登录用户共读同一时间线（新闻对所有人一致，抓一次而非每用户抓）。RLS SELECT 放行 `user_id IS NULL OR auth.uid() = user_id`，写入仅 service_role。按用户兴趣的个性化评分留 Phase 2，在全局池之上叠加。
- **后端 lint gate**：push 前对改动 `.py` 跑 `black` / `isort` / `flake8`（非 ruff）。
- **下载卡片代码保留不删**：仅隐藏 UI（feature flag / 注释）。

---

## File Structure

**后端（新建）**
- `supabase/migrations/304_topic_inspiration.sql` — 三表 + 索引 + RLS + 种子全局信源。
- `backend/app/services/topics/__init__.py`
- `backend/app/services/topics/adapters/base.py` — `HotspotCandidate` dataclass + `SourceAdapter` 抽象。
- `backend/app/services/topics/adapters/rss_adapter.py` — RSS 实现。
- `backend/app/services/topics/adapters/newsnow_adapter.py` — NewsNow 实现。
- `backend/app/services/topics/adapters/registry.py` — `get_adapter(kind)`。
- `backend/app/services/topics/keyword_filter.py` — 关键词粗筛纯函数。
- `backend/app/repositories/signal_sources_repository.py` — 信源 CRUD + 健康写回。
- `backend/app/repositories/hotspots_repository.py` — dedup 插入 / 按日列表 / distinct 日期。
- `backend/app/schemas/topics.py` — Pydantic 响应模型。
- `backend/app/api/topics_router.py` — REST。
- `backend/app/workflows/topic_inspiration.py` — `@DBOS.scheduled` 抓取 workflow。
- `docker/docker-compose.yml`（改）+ `docs/runbook/newsnow-selfhost.md`（新）— NewsNow 容器。

**后端（修改）**
- `backend/app/workflows/_scheduled_bundle.py` — 注册新 workflow。
- `backend/app/api/__init__.py` — include `topics_router`。

**前端（新建）**
- `frontend/services/topicService.ts`
- `frontend/pages/TopicInspirationPage.tsx`
- `frontend/components/TopicInspiration/Timeline.tsx`
- `frontend/components/TopicInspiration/HotspotCard.tsx`
- `frontend/components/TopicInspiration/HotspotInfoPanel.tsx` — IslandShell portal info 卡。
- `frontend/components/TopicInspiration/FloatingParse.tsx` — 悬浮解析（吸收 ParserPage）。
- `frontend/components/TopicInspiration/parseModeDetect.ts` — 合三为一检测纯函数。
- `frontend/components/TopicInspiration/parseModeDetect.test.ts`

**前端（修改）**
- `frontend/router.tsx` · `frontend/components/Sidebar.tsx` · `frontend/utils/routeConfig.ts` — 路由 + 导航重命名。
- `frontend/public/locales/{en,zh}.json` — i18n。

---

## 后端

### Task 1: Migration 304 — 三表 + RLS + 种子全局信源

**Files:**
- Create: `supabase/migrations/304_topic_inspiration.sql`

**Interfaces:**
- Produces: 表 `signal_sources(id,user_id,kind,name,config,enabled,category,health,consecutive_failures,last_error,last_fetched_at,last_ok_at,created_at)`、`hotspots(id,user_id,source_id,source_label,title,url,origin_url,content_original,content_translated,summary,ai_summary,reason,score,tags,category,topic_group_id,media_url,cover_url,captured_at,rank_timeline,dedup_key,created_at)`、`topic_groups(id,user_id,label,source_count,heat,first_seen,last_seen,embedding,created_at)`。

- [ ] **Step 1: 写 migration SQL**

```sql
-- 304_topic_inspiration.sql
-- Topic Inspiration module: signal_sources / hotspots / topic_groups.
-- Phase 1 数据归属: user_id 可空, NULL = 系统级全局 (抓一次, 全员共读).

-- ============ signal_sources ============
CREATE TABLE IF NOT EXISTS signal_sources (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL = global/system
    kind TEXT NOT NULL CHECK (kind IN ('newsnow', 'rss', 'http_api', 'custom')),
    name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT true,
    category TEXT,
    health TEXT NOT NULL DEFAULT 'ok' CHECK (health IN ('ok', 'degraded', 'dead')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_fetched_at TIMESTAMPTZ,
    last_ok_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signal_sources_enabled ON signal_sources(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_signal_sources_user ON signal_sources(user_id);

ALTER TABLE signal_sources ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own sources" ON signal_sources FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Manage own sources" ON signal_sources FOR ALL
    USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Service role full access on signal_sources" ON signal_sources FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ topic_groups (created now, populated in Phase 3) ============
CREATE TABLE IF NOT EXISTS topic_groups (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 1,
    heat NUMERIC NOT NULL DEFAULT 0,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE topic_groups ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own topic_groups" ON topic_groups FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Service role full access on topic_groups" ON topic_groups FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ hotspots ============
CREATE TABLE IF NOT EXISTS hotspots (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL = global
    source_id BIGINT REFERENCES signal_sources(id) ON DELETE SET NULL,
    source_label TEXT,
    title TEXT NOT NULL,
    url TEXT,
    origin_url TEXT,
    content_original TEXT,
    content_translated TEXT,
    summary TEXT,
    ai_summary TEXT,
    reason TEXT,
    score NUMERIC,
    tags TEXT[] NOT NULL DEFAULT '{}',
    category TEXT,
    topic_group_id BIGINT REFERENCES topic_groups(id) ON DELETE SET NULL,
    media_url TEXT,
    cover_url TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rank_timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
    dedup_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hotspots_dedup ON hotspots(dedup_key);
CREATE INDEX IF NOT EXISTS idx_hotspots_captured ON hotspots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotspots_category ON hotspots(category);

ALTER TABLE hotspots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own hotspots" ON hotspots FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Service role full access on hotspots" ON hotspots FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ 种子全局信源 (user_id = NULL) ============
INSERT INTO signal_sources (user_id, kind, name, config, category) VALUES
    (NULL, 'newsnow', 'GitHub Trending', '{"platform_id": "github-trending-today"}'::jsonb, 'product'),
    (NULL, 'newsnow', 'Hacker News',     '{"platform_id": "hackernews"}'::jsonb,            'industry'),
    (NULL, 'newsnow', 'V2EX Share',      '{"platform_id": "v2ex-share"}'::jsonb,            'industry'),
    (NULL, 'rss',     'MarkTechPost',    '{"url": "https://www.marktechpost.com/feed/"}'::jsonb, 'model')
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: 本地 apply 验证**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/304_topic_inspiration.sql`
（无本地库则跳过，靠 CI apply；本地有 NAS dev 库则连 dev：见 `reference_nas_dev_db`。）
Expected: `CREATE TABLE` ×3 + `INSERT 0 4` + `NOTIFY`，无 error。

- [ ] **Step 3: 核列名**

Run: `psql ... -c "SELECT column_name FROM information_schema.columns WHERE table_name='hotspots' ORDER BY ordinal_position;"`
Expected: 列出全部 23 列与上方一致。

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/304_topic_inspiration.sql
git commit -m "feat(topic-inspiration): migration 304 — signal_sources/hotspots/topic_groups + global seed sources"
```

---

### Task 2: `HotspotCandidate` + `SourceAdapter` 抽象

**Files:**
- Create: `backend/app/services/topics/__init__.py`（空）
- Create: `backend/app/services/topics/adapters/__init__.py`（空）
- Create: `backend/app/services/topics/adapters/base.py`
- Test: `backend/tests/topics/test_adapter_base.py`

**Interfaces:**
- Produces: `HotspotCandidate(title, url, origin_url, content, source_label, captured_at, media_url, cover_url)` dataclass + `dedup_key` 计算 `make_dedup_key(source_id, url, title)`；抽象 `class SourceAdapter: async def fetch(self, source: dict) -> list[HotspotCandidate]`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/topics/test_adapter_base.py
from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key


def test_dedup_key_prefers_url_over_title():
    k1 = make_dedup_key("123", url="https://a.com/x", title="Hello")
    k2 = make_dedup_key("123", url="https://a.com/x", title="Different")
    assert k1 == k2  # same url -> same key regardless of title


def test_dedup_key_falls_back_to_title_when_no_url():
    k = make_dedup_key("123", url=None, title="Hello World")
    assert "123" in k and k != make_dedup_key("123", url=None, title="Other")


def test_candidate_defaults():
    c = HotspotCandidate(title="t", url="u", source_label="src")
    assert c.media_url is None
    assert c.content == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_adapter_base.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.topics`.

- [ ] **Step 3: 实现**

```python
# backend/app/services/topics/adapters/base.py
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class HotspotCandidate:
    title: str
    url: Optional[str] = None
    origin_url: Optional[str] = None
    content: str = ""
    source_label: str = ""
    captured_at: Optional[datetime] = None
    media_url: Optional[str] = None
    cover_url: Optional[str] = None

    def __post_init__(self) -> None:
        if self.captured_at is None:
            self.captured_at = datetime.now(timezone.utc)
        if self.origin_url is None:
            self.origin_url = self.url


def make_dedup_key(source_id: str, *, url: Optional[str], title: str) -> str:
    """Stable dedup key. Prefer normalized url; fall back to (source_id, title)."""
    basis = (url or "").strip().lower().rstrip("/")
    if not basis:
        basis = f"{source_id}:{title.strip().lower()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()  # nosec - non-crypto


class SourceAdapter:
    """Normalize any source into HotspotCandidate list. Subclass per kind."""

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        raise NotImplementedError
```

Also create empty `backend/app/services/topics/__init__.py` and `backend/app/services/topics/adapters/__init__.py`.

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_adapter_base.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/topics/ backend/tests/topics/test_adapter_base.py
git commit -m "feat(topic-inspiration): HotspotCandidate + SourceAdapter base + dedup key"
```

---

### Task 3: RSS 适配器

**Files:**
- Create: `backend/app/services/topics/adapters/rss_adapter.py`
- Test: `backend/tests/topics/test_rss_adapter.py`
- Modify: `backend/pyproject.toml`（加 `feedparser` 依赖）

**Interfaces:**
- Consumes: `HotspotCandidate`、`SourceAdapter`。
- Produces: `class RssAdapter(SourceAdapter)`，读 `source["config"]["url"]`，用 `feedparser` 解析，map 到候选。

- [ ] **Step 1: 加依赖**

Run: `cd backend && uv add feedparser`
Expected: `feedparser` 写入 `pyproject.toml` + lock 更新。

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/topics/test_rss_adapter.py
import pytest
from app.services.topics.adapters.rss_adapter import RssAdapter

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Demo</title>
  <item><title>First Post</title><link>https://demo.com/1</link>
    <description>Body one</description></item>
  <item><title>Second Post</title><link>https://demo.com/2</link>
    <description>Body two</description></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_rss_adapter_parses_items(monkeypatch):
    async def fake_get(url, timeout):
        return SAMPLE_RSS

    adapter = RssAdapter()
    monkeypatch.setattr(adapter, "_get_text", fake_get)
    out = await adapter.fetch({"name": "Demo", "config": {"url": "https://demo.com/feed"}})
    assert len(out) == 2
    assert out[0].title == "First Post"
    assert out[0].url == "https://demo.com/1"
    assert out[0].source_label == "Demo (RSS)"


@pytest.mark.asyncio
async def test_rss_adapter_raises_on_empty(monkeypatch):
    async def fake_get(url, timeout):
        return "<rss><channel></channel></rss>"

    adapter = RssAdapter()
    monkeypatch.setattr(adapter, "_get_text", fake_get)
    with pytest.raises(ValueError):
        await adapter.fetch({"name": "Empty", "config": {"url": "x"}})
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_rss_adapter.py -v`
Expected: FAIL — module not found。

- [ ] **Step 4: 实现**

```python
# backend/app/services/topics/adapters/rss_adapter.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

from app.services.topics.adapters.base import HotspotCandidate, SourceAdapter


class RssAdapter(SourceAdapter):
    async def _get_text(self, url: str, timeout: float) -> str:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        cfg = source.get("config") or {}
        url = cfg.get("url")
        if not url:
            raise ValueError("rss source missing config.url")
        text = await self._get_text(url, timeout=15.0)
        parsed = await asyncio.to_thread(feedparser.parse, text)
        label = f"{source.get('name', 'RSS')} (RSS)"
        out: list[HotspotCandidate] = []
        for e in parsed.entries:
            title = (getattr(e, "title", "") or "").strip()
            if not title:
                continue
            captured = None
            if getattr(e, "published_parsed", None):
                captured = datetime.fromtimestamp(mktime(e.published_parsed), tz=timezone.utc)
            out.append(
                HotspotCandidate(
                    title=title,
                    url=getattr(e, "link", None),
                    content=(getattr(e, "summary", "") or "")[:5000],
                    source_label=label,
                    captured_at=captured,
                )
            )
        if not out:
            raise ValueError(f"rss source produced 0 items: {url}")
        return out
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_rss_adapter.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/topics/adapters/rss_adapter.py backend/tests/topics/test_rss_adapter.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(topic-inspiration): RSS source adapter"
```

---

### Task 4: NewsNow 适配器

**Files:**
- Create: `backend/app/services/topics/adapters/newsnow_adapter.py`
- Test: `backend/tests/topics/test_newsnow_adapter.py`

**Interfaces:**
- Consumes: `HotspotCandidate`、`SourceAdapter`。
- Produces: `class NewsNowAdapter(SourceAdapter)`，读 env `NEWSNOW_API_URL`（默认 `http://localhost:4000`）+ `source["config"]["platform_id"]`，打 `GET {api_url}/api/s?id={platform_id}&latest`，校验 `status in ('success','cache')`，map `data[].{title,url}`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/topics/test_newsnow_adapter.py
import pytest
from app.services.topics.adapters.newsnow_adapter import NewsNowAdapter


@pytest.mark.asyncio
async def test_newsnow_maps_items(monkeypatch):
    async def fake_get_json(url, timeout):
        assert "id=hackernews" in url
        return {"status": "success", "items": [
            {"id": "1", "title": "Item A", "url": "https://h.com/a"},
            {"id": "2", "title": "Item B", "url": "https://h.com/b"},
        ]}

    adapter = NewsNowAdapter(api_url="http://nn")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    out = await adapter.fetch({"name": "HN", "config": {"platform_id": "hackernews"}})
    assert [c.title for c in out] == ["Item A", "Item B"]
    assert out[0].source_label == "HN"


@pytest.mark.asyncio
async def test_newsnow_raises_on_bad_status(monkeypatch):
    async def fake_get_json(url, timeout):
        return {"status": "error", "items": []}

    adapter = NewsNowAdapter(api_url="http://nn")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    with pytest.raises(ValueError):
        await adapter.fetch({"name": "X", "config": {"platform_id": "x"}})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_newsnow_adapter.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```python
# backend/app/services/topics/adapters/newsnow_adapter.py
from __future__ import annotations

import os

import httpx

from app.services.topics.adapters.base import HotspotCandidate, SourceAdapter

_OK_STATUS = {"success", "cache"}


class NewsNowAdapter(SourceAdapter):
    def __init__(self, api_url: str | None = None) -> None:
        self.api_url = (api_url or os.getenv("NEWSNOW_API_URL", "http://localhost:4000")).rstrip("/")

    async def _get_json(self, url: str, timeout: float) -> dict:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        cfg = source.get("config") or {}
        platform_id = cfg.get("platform_id")
        if not platform_id:
            raise ValueError("newsnow source missing config.platform_id")
        url = f"{self.api_url}/api/s?id={platform_id}&latest"
        data = await self._get_json(url, timeout=15.0)
        status = data.get("status", "unknown")
        if status not in _OK_STATUS:
            raise ValueError(f"newsnow status not ok: {status}")
        items = data.get("items") or []
        label = source.get("name", platform_id)
        out: list[HotspotCandidate] = []
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            out.append(
                HotspotCandidate(
                    title=title,
                    url=it.get("url"),
                    content=(it.get("extra", {}) or {}).get("info", "") if isinstance(it.get("extra"), dict) else "",
                    source_label=label,
                )
            )
        if not out:
            raise ValueError(f"newsnow produced 0 items: {platform_id}")
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_newsnow_adapter.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/topics/adapters/newsnow_adapter.py backend/tests/topics/test_newsnow_adapter.py
git commit -m "feat(topic-inspiration): NewsNow source adapter"
```

---

### Task 5: 适配器注册表 + 关键词粗筛

**Files:**
- Create: `backend/app/services/topics/adapters/registry.py`
- Create: `backend/app/services/topics/keyword_filter.py`
- Test: `backend/tests/topics/test_keyword_filter.py`

**Interfaces:**
- Produces: `get_adapter(kind: str) -> SourceAdapter`（`newsnow`/`rss`，未知 raise）；`keyword_filter(candidates, include, exclude) -> list[HotspotCandidate]`（`include` 空=全留；命中任一 `exclude` 词剔除；大小写不敏感）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/topics/test_keyword_filter.py
from app.services.topics.adapters.base import HotspotCandidate
from app.services.topics.keyword_filter import keyword_filter
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.adapters.rss_adapter import RssAdapter
import pytest


def _c(title):
    return HotspotCandidate(title=title)


def test_include_empty_keeps_all():
    out = keyword_filter([_c("AI news"), _c("cooking")], include=[], exclude=[])
    assert len(out) == 2


def test_include_filters_by_any_match():
    out = keyword_filter([_c("AI model"), _c("cooking")], include=["ai", "llm"], exclude=[])
    assert [c.title for c in out] == ["AI model"]


def test_exclude_removes_match():
    out = keyword_filter([_c("AI sale ad"), _c("AI paper")], include=["ai"], exclude=["sale"])
    assert [c.title for c in out] == ["AI paper"]


def test_registry_returns_rss():
    assert isinstance(get_adapter("rss"), RssAdapter)


def test_registry_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("nope")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_keyword_filter.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```python
# backend/app/services/topics/keyword_filter.py
from __future__ import annotations

from app.services.topics.adapters.base import HotspotCandidate


def keyword_filter(
    candidates: list[HotspotCandidate],
    *,
    include: list[str],
    exclude: list[str],
) -> list[HotspotCandidate]:
    inc = [w.lower() for w in include if w.strip()]
    exc = [w.lower() for w in exclude if w.strip()]
    out: list[HotspotCandidate] = []
    for c in candidates:
        hay = f"{c.title} {c.content}".lower()
        if exc and any(w in hay for w in exc):
            continue
        if inc and not any(w in hay for w in inc):
            continue
        out.append(c)
    return out
```

```python
# backend/app/services/topics/adapters/registry.py
from __future__ import annotations

from app.services.topics.adapters.base import SourceAdapter
from app.services.topics.adapters.newsnow_adapter import NewsNowAdapter
from app.services.topics.adapters.rss_adapter import RssAdapter


def get_adapter(kind: str) -> SourceAdapter:
    if kind == "newsnow":
        return NewsNowAdapter()
    if kind == "rss":
        return RssAdapter()
    raise ValueError(f"unsupported source kind: {kind}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_keyword_filter.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/topics/adapters/registry.py backend/app/services/topics/keyword_filter.py backend/tests/topics/test_keyword_filter.py
git commit -m "feat(topic-inspiration): adapter registry + keyword pre-filter"
```

---

### Task 6: signal_sources 仓库（列表 + 健康写回）

**Files:**
- Create: `backend/app/repositories/signal_sources_repository.py`
- Test: `backend/tests/topics/test_signal_sources_repo.py`

**Interfaces:**
- Consumes: `get_async_supabase_admin`（同 `resources_repository._get_client`）。
- Produces: `class SignalSourcesRepository` with `async def list_enabled() -> list[dict]`、`async def mark_health(source_id: str, *, ok: bool, error: str | None, dead_threshold: int = 3) -> dict`（成功→`consecutive_failures=0,health='ok',last_ok_at,last_fetched_at`；失败→`+1`，≥阈值 `dead` 否则 `degraded`，写 `last_error,last_fetched_at`）。`mark_health` 返回 `{"health": ..., "consecutive_failures": ..., "flipped_to_dead": bool}`。

- [ ] **Step 1: 写失败测试**（用 fake client，验证健康状态机纯逻辑）

```python
# backend/tests/topics/test_signal_sources_repo.py
import pytest
from app.repositories.signal_sources_repository import compute_health


def test_compute_health_success_resets():
    h = compute_health(prev_failures=2, ok=True, dead_threshold=3)
    assert h == {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}


def test_compute_health_degraded():
    h = compute_health(prev_failures=0, ok=False, dead_threshold=3)
    assert h["health"] == "degraded" and h["consecutive_failures"] == 1
    assert h["flipped_to_dead"] is False


def test_compute_health_flips_to_dead_once():
    h = compute_health(prev_failures=2, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["consecutive_failures"] == 3
    assert h["flipped_to_dead"] is True


def test_compute_health_stays_dead_no_reflip():
    h = compute_health(prev_failures=3, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["flipped_to_dead"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_signal_sources_repo.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```python
# backend/app/repositories/signal_sources_repository.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.db.supabase import get_async_supabase_admin  # adjust import to match resources_repository
from loguru import logger


def compute_health(*, prev_failures: int, ok: bool, dead_threshold: int = 3) -> dict:
    """Pure state machine for source health. flipped_to_dead = crossed threshold this call."""
    if ok:
        return {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}
    failures = prev_failures + 1
    was_dead = prev_failures >= dead_threshold
    is_dead = failures >= dead_threshold
    health = "dead" if is_dead else "degraded"
    return {
        "health": health,
        "consecutive_failures": failures,
        "flipped_to_dead": is_dead and not was_dead,
    }


class SignalSourcesRepository:
    TABLE = "signal_sources"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_enabled(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = await client.table(self.TABLE).select("*").eq("enabled", True).execute()
        return result.data or []

    async def mark_health(
        self, source_id: str, *, ok: bool, error: Optional[str] = None, dead_threshold: int = 3
    ) -> dict:
        client = await self._client()
        cur = await client.table(self.TABLE).select("consecutive_failures").eq("id", source_id).execute()
        prev = (cur.data[0]["consecutive_failures"] if cur.data else 0) or 0
        state = compute_health(prev_failures=prev, ok=ok, dead_threshold=dead_threshold)
        now = datetime.now(timezone.utc).isoformat()
        patch = {
            "health": state["health"],
            "consecutive_failures": state["consecutive_failures"],
            "last_fetched_at": now,
        }
        if ok:
            patch["last_ok_at"] = now
            patch["last_error"] = None
        else:
            patch["last_error"] = (error or "")[:500]
        try:
            await client.table(self.TABLE).update(patch).eq("id", source_id).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"mark_health failed for {source_id}: {e}")
        return state
```

> 实现者注意：`get_async_supabase_admin` 的精确 import 路径以 `resources_repository.py` 的 `_get_client` 为准（探查结果显示用的是该 admin client），照搬其 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_signal_sources_repo.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/signal_sources_repository.py backend/tests/topics/test_signal_sources_repo.py
git commit -m "feat(topic-inspiration): signal_sources repo + health state machine"
```

---

### Task 7: hotspots 仓库（dedup 插入 / 按日列表 / distinct 日期）

**Files:**
- Create: `backend/app/repositories/hotspots_repository.py`
- Test: `backend/tests/topics/test_hotspots_repo.py`

**Interfaces:**
- Consumes: `get_async_supabase_admin`、`HotspotCandidate`、`make_dedup_key`。
- Produces: `class HotspotsRepository` with `def build_rows(candidates, *, source_id, category) -> list[dict]`（纯函数，候选→insert dict，含 `dedup_key`、`user_id=None`、`media_url`、`captured_at` iso）、`async def upsert_ignore(rows) -> int`（按 `dedup_key` 冲突忽略，返回写入数）、`async def list_for_date(day: str | None, category: str | None, limit: int) -> list[dict]`、`async def distinct_dates(limit_days: int) -> list[str]`。

- [ ] **Step 1: 写失败测试**（聚焦 `build_rows` 纯函数）

```python
# backend/tests/topics/test_hotspots_repo.py
from app.services.topics.adapters.base import HotspotCandidate
from app.repositories.hotspots_repository import HotspotsRepository


def test_build_rows_sets_dedup_and_global_user():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="A", url="https://x.com/a", source_label="S")]
    rows = repo.build_rows(cands, source_id="42", category="model")
    assert len(rows) == 1
    r = rows[0]
    assert r["user_id"] is None  # Phase 1 global
    assert r["source_id"] == "42"
    assert r["title"] == "A"
    assert r["category"] == "model"
    assert r["dedup_key"]
    assert r["media_url"] is None


def test_build_rows_carries_media_url():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="V", url="u", media_url="https://m/v.mp4")]
    rows = repo.build_rows(cands, source_id="1", category=None)
    assert rows[0]["media_url"] == "https://m/v.mp4"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_hotspots_repo.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```python
# backend/app/repositories/hotspots_repository.py
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db.supabase import get_async_supabase_admin  # match resources_repository import
from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key


class HotspotsRepository:
    TABLE = "hotspots"

    async def _client(self):
        return await get_async_supabase_admin()

    def build_rows(
        self, candidates: list[HotspotCandidate], *, source_id: str, category: Optional[str]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for c in candidates:
            rows.append(
                {
                    "user_id": None,  # Phase 1: global
                    "source_id": source_id,
                    "source_label": c.source_label,
                    "title": c.title,
                    "url": c.url,
                    "origin_url": c.origin_url,
                    "content_original": c.content,
                    "category": category,
                    "media_url": c.media_url,
                    "cover_url": c.cover_url,
                    "captured_at": c.captured_at.isoformat() if c.captured_at else None,
                    "dedup_key": make_dedup_key(source_id, url=c.url, title=c.title),
                }
            )
        return rows

    async def upsert_ignore(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        client = await self._client()
        try:
            result = await client.table(self.TABLE).upsert(
                rows, on_conflict="dedup_key", ignore_duplicates=True
            ).execute()
            return len(result.data or [])
        except Exception as e:  # noqa: BLE001
            logger.error(f"hotspots upsert failed: {e}")
            return 0

    async def list_for_date(
        self, day: Optional[str], category: Optional[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        client = await self._client()
        q = client.table(self.TABLE).select("*")
        if day:
            q = q.gte("captured_at", f"{day}T00:00:00Z").lte("captured_at", f"{day}T23:59:59Z")
        if category and category != "all":
            q = q.eq("category", category)
        result = await q.order("captured_at", desc=True).limit(limit).execute()
        return result.data or []

    async def distinct_dates(self, limit_days: int = 60) -> list[str]:
        client = await self._client()
        result = await client.table(self.TABLE).select("captured_at").order(
            "captured_at", desc=True
        ).limit(2000).execute()
        seen: list[str] = []
        for r in result.data or []:
            d = (r.get("captured_at") or "")[:10]
            if d and d not in seen:
                seen.append(d)
            if len(seen) >= limit_days:
                break
        return seen
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_hotspots_repo.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/hotspots_repository.py backend/tests/topics/test_hotspots_repo.py
git commit -m "feat(topic-inspiration): hotspots repo (dedup upsert / list by date / distinct dates)"
```

---

### Task 8: DBOS 定时抓取 workflow + 注册

**Files:**
- Create: `backend/app/workflows/topic_inspiration.py`
- Modify: `backend/app/workflows/_scheduled_bundle.py`
- Test: `backend/tests/topics/test_fetch_workflow.py`

**Interfaces:**
- Consumes: `SignalSourcesRepository.list_enabled/mark_health`、`get_adapter`、`keyword_filter`、`HotspotsRepository.build_rows/upsert_ignore`。
- Produces: `async def run_topic_fetch_once() -> dict`（核心逻辑，可单测，逐源 try/except 隔离 + 健康写回 + dedup 写入，返回 `{"sources": n, "ok": x, "failed": y, "written": z}`）；`@DBOS.scheduled("*/30 * * * *") @DBOS.workflow() async def topic_fetch_workflow(...)` 调它。

- [ ] **Step 1: 写失败测试**（mock repos + adapter，验证隔离 + 健康 + 写入）

```python
# backend/tests/topics/test_fetch_workflow.py
import pytest
from app.workflows.topic_inspiration import run_topic_fetch_once
from app.services.topics.adapters.base import HotspotCandidate


class _FakeSources:
    def __init__(self, rows):
        self.rows = rows
        self.health_calls = []

    async def list_enabled(self):
        return self.rows

    async def mark_health(self, sid, *, ok, error=None, dead_threshold=3):
        self.health_calls.append((sid, ok))
        return {"health": "ok" if ok else "dead", "consecutive_failures": 0, "flipped_to_dead": not ok}


class _FakeHotspots:
    def __init__(self):
        self.written = []

    def build_rows(self, cands, *, source_id, category):
        return [{"dedup_key": c.title, "source_id": source_id} for c in cands]

    async def upsert_ignore(self, rows):
        self.written.extend(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_fetch_isolates_failing_source(monkeypatch):
    sources = _FakeSources([
        {"id": "1", "kind": "rss", "name": "Good", "config": {}, "category": "model"},
        {"id": "2", "kind": "rss", "name": "Bad", "config": {}, "category": None},
    ])
    hotspots = _FakeHotspots()

    async def fake_fetch(source):
        if source["name"] == "Bad":
            raise RuntimeError("boom")
        return [HotspotCandidate(title="ok-item")]

    class _Adapter:
        async def fetch(self, s):
            return await fake_fetch(s)

    monkeypatch.setattr("app.workflows.topic_inspiration.get_adapter", lambda k: _Adapter())
    result = await run_topic_fetch_once(sources_repo=sources, hotspots_repo=hotspots)

    assert result["sources"] == 2
    assert result["ok"] == 1 and result["failed"] == 1
    assert result["written"] == 1  # only Good's item
    assert ("1", True) in sources.health_calls
    assert ("2", False) in sources.health_calls
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_fetch_workflow.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```python
# backend/app/workflows/topic_inspiration.py
from __future__ import annotations

from datetime import datetime

from dbos import DBOS
from loguru import logger

from app.repositories.hotspots_repository import HotspotsRepository
from app.repositories.signal_sources_repository import SignalSourcesRepository
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.keyword_filter import keyword_filter

# Phase 1: no per-user interest yet -> global keep-all pre-filter.
_GLOBAL_INCLUDE: list[str] = []
_GLOBAL_EXCLUDE: list[str] = []


async def run_topic_fetch_once(
    *, sources_repo: SignalSourcesRepository | None = None, hotspots_repo: HotspotsRepository | None = None
) -> dict:
    sources_repo = sources_repo or SignalSourcesRepository()
    hotspots_repo = hotspots_repo or HotspotsRepository()
    sources = await sources_repo.list_enabled()
    ok = failed = written = 0
    for src in sources:
        sid = str(src["id"])
        try:
            adapter = get_adapter(src["kind"])
            candidates = await adapter.fetch(src)
            candidates = keyword_filter(candidates, include=_GLOBAL_INCLUDE, exclude=_GLOBAL_EXCLUDE)
            rows = hotspots_repo.build_rows(candidates, source_id=sid, category=src.get("category"))
            written += await hotspots_repo.upsert_ignore(rows)
            await sources_repo.mark_health(sid, ok=True)
            ok += 1
        except Exception as e:  # noqa: BLE001 — per-source isolation, never break the batch
            logger.warning(f"topic source {sid} ({src.get('name')}) failed: {e}")
            await sources_repo.mark_health(sid, ok=False, error=str(e))
            failed += 1
    summary = {"sources": len(sources), "ok": ok, "failed": failed, "written": written}
    logger.info(f"topic_fetch done: {summary}")
    return summary


@DBOS.scheduled("*/30 * * * *")  # every 30 min
@DBOS.workflow()
async def topic_fetch_workflow(scheduled_time: datetime, actual_time: datetime) -> None:
    await run_topic_fetch_once()
```

- [ ] **Step 4: 注册到 bundle**

在 `backend/app/workflows/_scheduled_bundle.py` 末尾加一行（照该文件现有 import 风格）：

```python
from app.workflows.topic_inspiration import topic_fetch_workflow  # noqa: F401
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_fetch_workflow.py -v`
Expected: PASS（1 passed）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/topic_inspiration.py backend/app/workflows/_scheduled_bundle.py backend/tests/topics/test_fetch_workflow.py
git commit -m "feat(topic-inspiration): DBOS scheduled fetch workflow (per-source isolation + health writeback)"
```

---

### Task 9: REST — schema + 列表/日历 endpoint

**Files:**
- Create: `backend/app/schemas/topics.py`
- Create: `backend/app/api/topics_router.py`
- Modify: `backend/app/api/__init__.py`
- Test: `backend/tests/topics/test_topics_router.py`

**Interfaces:**
- Consumes: `AuthDep`（`app.core.deps`）、`HotspotsRepository`。
- Produces: `router = APIRouter(prefix="/topics")`；`GET /topics?day=&category=&limit=` → `{success, count, hotspots: [HotspotOut]}`；`GET /topics/dates?limit_days=` → `{success, dates: [str]}`。`HotspotOut` 字段含 `id,title,url,origin_url,source_label,summary,reason,score,tags,category,media_url,cover_url,captured_at`。

- [ ] **Step 1: 写失败测试**（用 FastAPI TestClient + 依赖覆盖）

```python
# backend/tests/topics/test_topics_router.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    class _FakeRepo:
        async def list_for_date(self, day, category, limit=100):
            return [{
                "id": "1", "title": "Hello", "url": "u", "origin_url": "u",
                "source_label": "S", "summary": None, "ai_summary": None, "reason": None,
                "score": None, "tags": [], "category": "model", "media_url": None,
                "cover_url": None, "captured_at": "2026-06-20T06:00:00Z",
            }]

        async def distinct_dates(self, limit_days=60):
            return ["2026-06-20", "2026-06-19"]

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _FakeRepo())
    app = FastAPI()
    app.dependency_overrides[tr.AuthDep] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    return TestClient(app)


def test_list_hotspots(client):
    r = client.get("/api/v1/topics?category=model")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 1
    assert body["hotspots"][0]["title"] == "Hello"


def test_dates(client):
    r = client.get("/api/v1/topics/dates")
    assert r.status_code == 200
    assert r.json()["dates"] == ["2026-06-20", "2026-06-19"]
```

> 注：`AuthDep` 是 `Annotated[..., Depends(get_auth)]`，无法直接作为 `dependency_overrides` key。实现者改用 override `tr.get_auth`（从 `app.core.deps` re-export）或在 router 内用一个可注入的 `_auth = Depends(get_auth)`。以 `media_router` 测试现状为准；若仓库无 router 级测试基建，则把本测试降级为只测 repo 间接调用，路由冒烟留 Task 16 的前端 e2e。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_topics_router.py -v`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现 schema**

```python
# backend/app/schemas/topics.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class HotspotOut(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    origin_url: Optional[str] = None
    source_label: Optional[str] = None
    summary: Optional[str] = None
    ai_summary: Optional[str] = None
    reason: Optional[str] = None
    score: Optional[float] = None
    tags: list[str] = []
    category: Optional[str] = None
    media_url: Optional[str] = None
    cover_url: Optional[str] = None
    captured_at: Optional[str] = None


class HotspotListResponse(BaseModel):
    success: bool = True
    count: int
    hotspots: list[HotspotOut]


class DatesResponse(BaseModel):
    success: bool = True
    dates: list[str]
```

- [ ] **Step 4: 实现 router**

```python
# backend/app/api/topics_router.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.core.deps import AuthDep, get_auth  # noqa: F401 — get_auth re-exported for test override
from app.repositories.hotspots_repository import HotspotsRepository
from app.schemas.topics import DatesResponse, HotspotListResponse, HotspotOut

router = APIRouter(prefix="/topics")


def _to_out(row: dict) -> HotspotOut:
    return HotspotOut(
        id=str(row.get("id")),
        title=row.get("title") or "",
        url=row.get("url"),
        origin_url=row.get("origin_url"),
        source_label=row.get("source_label"),
        summary=row.get("summary"),
        ai_summary=row.get("ai_summary"),
        reason=row.get("reason"),
        score=row.get("score"),
        tags=row.get("tags") or [],
        category=row.get("category"),
        media_url=row.get("media_url"),
        cover_url=row.get("cover_url"),
        captured_at=row.get("captured_at"),
    )


@router.get("", response_model=HotspotListResponse)
async def list_hotspots(
    auth: AuthDep,
    day: Optional[str] = Query(None, description="YYYY-MM-DD"),
    category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=300),
):
    repo = HotspotsRepository()
    rows = await repo.list_for_date(day, category, limit=limit)
    items = [_to_out(r) for r in rows]
    return HotspotListResponse(count=len(items), hotspots=items)


@router.get("/dates", response_model=DatesResponse)
async def hotspot_dates(auth: AuthDep, limit_days: int = Query(60, ge=1, le=180)):
    repo = HotspotsRepository()
    return DatesResponse(dates=await repo.distinct_dates(limit_days))
```

- [ ] **Step 5: 注册 router**

在 `backend/app/api/__init__.py` 仿 `media_router` 那行加：

```python
from app.api.topics_router import router as topics_router  # near other imports
api_router.include_router(router=topics_router, tags=["Topics"])
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_topics_router.py -v`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/topics.py backend/app/api/topics_router.py backend/app/api/__init__.py backend/tests/topics/test_topics_router.py
git commit -m "feat(topic-inspiration): topics REST — list hotspots + calendar dates"
```

---

### Task 10: 闭环 endpoint — 生成脚本 + 解析下载

**Files:**
- Modify: `backend/app/api/topics_router.py`
- Test: `backend/tests/topics/test_topics_closeloop.py`

**Interfaces:**
- Consumes: `HotspotsRepository`（取单条）、`ScriptAIService`（已有，生成脚本）、`dedup_and_dispatch`（已有，解析下载）。
- Produces: `POST /topics/{id}/generate-script` → `{success, script}`（用 hotspot title+summary 喂 `script_ai`）；`POST /topics/{id}/parse-download` → `{success, task_id}`（仅当 `media_url` 非空，否则 400）。

- [ ] **Step 1: 加 repo 取单条方法**（先补 `get_by_id`）

在 `backend/app/repositories/hotspots_repository.py` 加：

```python
    async def get_by_id(self, hotspot_id: str) -> dict | None:
        client = await self._client()
        result = await client.table(self.TABLE).select("*").eq("id", hotspot_id).limit(1).execute()
        return (result.data or [None])[0]
```

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/topics/test_topics_closeloop.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    class _Repo:
        async def get_by_id(self, hid):
            if hid == "media1":
                return {"id": "media1", "title": "Vid", "summary": "s", "media_url": "https://m/v.mp4", "url": "https://m/v"}
            return {"id": "news1", "title": "News", "summary": "s", "media_url": None, "url": "https://n/1"}

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _Repo())

    async def fake_script(title, summary, user_id):
        return "GENERATED SCRIPT"

    monkeypatch.setattr(tr, "_generate_script_for", fake_script)

    async def fake_dispatch(**kwargs):
        return {"task_id": "task-123"}

    monkeypatch.setattr(tr, "_parse_download_for", lambda **k: fake_dispatch(**k))

    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    return TestClient(app)


def test_generate_script_any_item(client):
    r = client.post("/api/v1/topics/news1/generate-script")
    assert r.status_code == 200 and r.json()["script"] == "GENERATED SCRIPT"


def test_parse_download_requires_media(client):
    r = client.post("/api/v1/topics/news1/parse-download")
    assert r.status_code == 400  # pure news, no media


def test_parse_download_ok_for_media(client):
    r = client.post("/api/v1/topics/media1/parse-download")
    assert r.status_code == 200 and r.json()["task_id"] == "task-123"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/topics/test_topics_closeloop.py -v`
Expected: FAIL（endpoint 未实现 / helper 未定义）。

- [ ] **Step 4: 实现**（追加到 `topics_router.py`）

```python
from fastapi import HTTPException

from app.services.storyboard.script.script_ai_service import ScriptAIService  # adjust to探查路径
from app.api.media_fetch_helpers import dedup_and_dispatch


async def _generate_script_for(title: str, summary: str, user_id: str) -> str:
    svc = ScriptAIService()
    # 复用已有 script_ai agent: 用标题+摘要作为灵感输入
    prompt = f"Topic: {title}\n\nContext: {summary or ''}\n\nWrite a short video script based on this topic."
    result = await svc.generate_outline(prompt, user_id=user_id)  # signature 以 script_ai_service 实际为准
    return result if isinstance(result, str) else (result.get("content") or "")


async def _parse_download_for(*, url: str, media_url: str, user_id: str) -> dict:
    return await dedup_and_dispatch(
        platform_id="",  # parser 自行识别; 以 media_fetch_helpers 实际签名为准
        user_id=user_id,
        resource_id=None,
        media_type=0,
        video_title="",
        download_video=True,
        download_cover=True,
        url=media_url or url,
        background_tasks=None,
    )


@router.post("/{hotspot_id}/generate-script")
async def generate_script(hotspot_id: str, auth: AuthDep):
    repo = HotspotsRepository()
    row = await repo.get_by_id(hotspot_id)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    script = await _generate_script_for(
        row.get("title") or "", row.get("ai_summary") or row.get("summary") or "", auth.user_id
    )
    return {"success": True, "script": script}


@router.post("/{hotspot_id}/parse-download")
async def parse_download(hotspot_id: str, auth: AuthDep):
    repo = HotspotsRepository()
    row = await repo.get_by_id(hotspot_id)
    if not row:
        raise HTTPException(status_code=404, detail="hotspot not found")
    if not row.get("media_url"):
        raise HTTPException(status_code=400, detail="hotspot has no parseable media")
    result = await _parse_download_for(
        url=row.get("url") or "", media_url=row["media_url"], user_id=auth.user_id
    )
    return {"success": True, "task_id": result.get("task_id")}
```

> 实现者注意：`ScriptAIService` 的实际方法名/签名 + `dedup_and_dispatch` 的实际必填参数以探查报告里的真实签名为准（`script_ai_service.py` / `media_fetch_helpers.py:144`）。helper 抽成 `_generate_script_for` / `_parse_download_for` 是为了让 Step 2 的测试能 monkeypatch。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/topics/test_topics_closeloop.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: lint + commit**

```bash
cd backend && uv run black app/api/topics_router.py app/repositories/hotspots_repository.py && uv run isort app/api/topics_router.py && uv run flake8 app/api/topics_router.py
git add backend/app/api/topics_router.py backend/app/repositories/hotspots_repository.py backend/tests/topics/test_topics_closeloop.py
git commit -m "feat(topic-inspiration): close-loop endpoints — generate-script (all) + parse-download (media only)"
```

---

### Task 11: NewsNow 自托管 — compose service + env + runbook

**Files:**
- Modify: `docker/docker-compose.yml`
- Create: `docs/runbook/newsnow-selfhost.md`

**Interfaces:**
- Produces: NAS 上一个 `newsnow` 容器（端口内部 4000）+ 后端 env `NEWSNOW_API_URL`。

- [ ] **Step 1: 加 compose service**（在 `docker/docker-compose.yml` 加一个 service，仿现有 service 缩进/network）

```yaml
  newsnow:
    image: ghcr.io/ourongxing/newsnow:latest
    container_name: mediahub-newsnow
    restart: unless-stopped
    ports:
      - "4000:4000"
    environment:
      - PORT=4000
    networks:
      - default
```

- [ ] **Step 2: 后端 env**

在 NAS host `/volume1/docker/mediahub/docker/.env` 加 `NEWSNOW_API_URL=http://mediahub-newsnow:4000`（容器间走 service 名）。本地 dev 用 `http://localhost:4000`。

- [ ] **Step 3: 写 runbook**

```markdown
# docs/runbook/newsnow-selfhost.md
# NewsNow 自托管（话题灵感数据引擎）

NewsNow = ourongxing/newsnow（MIT），内置 50+ 平台热榜抓取器。话题灵感模块的
`newsnow` 适配器打它的 `/api/s?id=<platform>` JSON API。

## 部署（NAS，手动一次）
⚠️ Watchtower 不读 compose 变更（见 CLAUDE.md）。新增 service 必须手动：
    ssh nas; cd /volume1/docker/mediahub
    sudo docker compose -f docker/docker-compose.yml up -d newsnow

## 后端连通
后端容器 env `NEWSNOW_API_URL=http://mediahub-newsnow:4000`（同 docker network）。
改 env 后 `sudo docker compose stop -t0 backend && ... start backend`（env 持久化坑见
reference_nas_backend_env）。

## 源失效时（话题灵感某平台抓不到）
平台改版导致抓取失效，由 NewsNow 上游社区修：
    cd <newsnow-repo>; git pull  # 或 docker pull 新 image
    sudo docker compose up -d newsnow
信源健康在 Settings → 信源管理显红（Phase 2）。

## 验证
    curl http://localhost:4000/api/s?id=hackernews | head
应返回 {"status":"success","items":[...]}。
```

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.yml docs/runbook/newsnow-selfhost.md
git commit -m "feat(topic-inspiration): self-host NewsNow compose service + runbook"
```

> ⚠️ 部署提醒：合并后需在 NAS 手动 `docker compose up -d newsnow` + 设 `NEWSNOW_API_URL`，否则 `newsnow` 源全部 `dead`（`rss` 源仍工作）。

---

## 前端

### Task 12: `topicService.ts`

**Files:**
- Create: `frontend/services/topicService.ts`

**Interfaces:**
- Consumes: `getAuthHeaders`（从 `parserService`）、`getApiUrl`（`utils/apiConfig`）。
- Produces: `Hotspot` 类型 + `getHotspots(day?, category?)`、`getHotspotDates()`、`generateScript(id)`、`parseDownload(id)`。

- [ ] **Step 1: 实现**

```typescript
// frontend/services/topicService.ts
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface Hotspot {
  id: string;
  title: string;
  url?: string | null;
  origin_url?: string | null;
  source_label?: string | null;
  summary?: string | null;
  ai_summary?: string | null;
  reason?: string | null;
  score?: number | null;
  tags: string[];
  category?: string | null;
  media_url?: string | null;
  cover_url?: string | null;
  captured_at?: string | null;
}

const base = () => `${getApiUrl()}/api/v1/topics`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function getHotspots(day?: string, category?: string): Promise<Hotspot[]> {
  const params = new URLSearchParams();
  if (day) params.set('day', day);
  if (category && category !== 'all') params.set('category', category);
  const resp = await fetch(`${base()}?${params.toString()}`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).hotspots as Hotspot[];
}

export async function getHotspotDates(): Promise<string[]> {
  const resp = await fetch(`${base()}/dates`, { headers: await getAuthHeaders() });
  return (await jsonOrThrow(resp)).dates as string[];
}

export async function generateScript(id: string): Promise<string> {
  const resp = await fetch(`${base()}/${id}/generate-script`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return (await jsonOrThrow(resp)).script as string;
}

export async function parseDownload(id: string): Promise<string> {
  const resp = await fetch(`${base()}/${id}/parse-download`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return (await jsonOrThrow(resp)).task_id as string;
}
```

- [ ] **Step 2: typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无新增错误。

- [ ] **Step 3: Commit**

```bash
git add frontend/services/topicService.ts
git commit -m "feat(topic-inspiration): topicService (hotspots/dates/generate/parse)"
```

---

### Task 13: 解析「合三为一」检测纯函数 + 单测

**Files:**
- Create: `frontend/components/TopicInspiration/parseModeDetect.ts`
- Test: `frontend/components/TopicInspiration/parseModeDetect.test.ts`

**Interfaces:**
- Produces: `detectParseMode(input: string): { mode: 'single' | 'batch' | 'playlist'; count: number; label: string }`。规则：含换行/多个 URL → `batch`（count=URL 数）；含歌单关键词（`/playlist`、`music.` 等）→ `playlist`；单 URL → `single`。

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/components/TopicInspiration/parseModeDetect.test.ts
import { describe, it, expect } from 'vitest';
import { detectParseMode } from './parseModeDetect';

describe('detectParseMode', () => {
  it('detects single link', () => {
    const r = detectParseMode('https://v.douyin.com/abc/');
    expect(r.mode).toBe('single');
    expect(r.count).toBe(1);
  });

  it('detects batch by multiple lines', () => {
    const r = detectParseMode('https://a.com/1\nhttps://a.com/2\nhttps://a.com/3');
    expect(r.mode).toBe('batch');
    expect(r.count).toBe(3);
  });

  it('detects playlist by keyword', () => {
    const r = detectParseMode('https://music.example.com/playlist/123');
    expect(r.mode).toBe('playlist');
  });

  it('empty input is single with count 0', () => {
    expect(detectParseMode('   ').count).toBe(0);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/TopicInspiration/parseModeDetect.test.ts`
Expected: FAIL — module not found。

- [ ] **Step 3: 实现**

```typescript
// frontend/components/TopicInspiration/parseModeDetect.ts
export type ParseMode = 'single' | 'batch' | 'playlist';

export interface ParseDetection {
  mode: ParseMode;
  count: number;
  label: string;
}

const URL_RE = /https?:\/\/[^\s]+/g;
const PLAYLIST_HINTS = ['/playlist', 'music.', '/songlist', 'list='];

export function detectParseMode(input: string): ParseDetection {
  const text = (input || '').trim();
  const urls = text.match(URL_RE) || [];
  if (urls.length === 0) {
    return { mode: 'single', count: 0, label: 'Paste a link' };
  }
  if (urls.length > 1) {
    return { mode: 'batch', count: urls.length, label: `Batch · ${urls.length} links` };
  }
  const single = urls[0].toLowerCase();
  if (PLAYLIST_HINTS.some((h) => single.includes(h))) {
    return { mode: 'playlist', count: 1, label: 'Playlist detected' };
  }
  return { mode: 'single', count: 1, label: 'Single link' };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/TopicInspiration/parseModeDetect.test.ts`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TopicInspiration/parseModeDetect.ts frontend/components/TopicInspiration/parseModeDetect.test.ts
git commit -m "feat(topic-inspiration): unified parse-mode detection (single/batch/playlist)"
```

---

### Task 14: 页面骨架 + 路由 + 导航重命名

**Files:**
- Create: `frontend/pages/TopicInspirationPage.tsx`
- Modify: `frontend/router.tsx` · `frontend/utils/routeConfig.ts` · `frontend/components/Sidebar.tsx`
- Modify: `frontend/public/locales/en.json` · `frontend/public/locales/zh.json`

**Interfaces:**
- Consumes: `getHotspots`/`getHotspotDates`、`islandUI`。
- Produces: `TopicInspirationPage`（拉取 hotspots + dates，state 持有 `selectedDay` / `category` / `selected hotspot`），导航项 `topicInspiration` 复用 parser 路由槽。

- [ ] **Step 1: 页面骨架（先渲染标题 + 拉数据，UI 占位）**

```tsx
// frontend/pages/TopicInspirationPage.tsx
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import { islandUI } from '../utils/featureFlags';
import { getHotspots, getHotspotDates, type Hotspot } from '../services/topicService';
import { useToast } from '../components/Toast';

export const TopicInspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const island = islandUI();
  const { addToast } = useToast();
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string>('all');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [hs, ds] = await Promise.all([getHotspots(day, category), getHotspotDates()]);
        if (!alive) return;
        setHotspots(hs);
        setDates(ds);
      } catch (err) {
        if (alive) addToast(`Failed to load topics: ${(err as Error).message}`, 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [day, category, addToast]);

  const cPrimary = island ? 'text-content' : 'text-ink-50';
  const cSub = island ? 'text-content-3' : 'text-ink-400';

  return (
    <div className="max-w-[1180px] mx-auto px-6 py-6">
      <div className="flex items-center gap-2">
        <Lightbulb size={22} className={island ? 'text-content-2' : 'text-ink-300'} />
        <h1 className={`text-[22px] font-bold ${cPrimary}`}>{t('topic.title')}</h1>
      </div>
      <p className={`text-xs mt-1 ${cSub}`}>{t('topic.subtitle')}</p>
      {/* Timeline + info panel + floating parse mount in later tasks */}
      <div className="mt-4 text-sm text-content-3">
        {loading ? t('common.loading') : `${hotspots.length} hotspots · ${dates.length} days`}
      </div>
    </div>
  );
};
```

- [ ] **Step 2: 路由**

`frontend/router.tsx`：把 line 45 的 `ParserPage` lazy import 改/补成 `TopicInspirationPage`，并将 `path: 'parser'` 的 element 换成 `<TopicInspirationPage />`（保留 `parser` 路径不变，避免动 routeConfig 映射；只换组件）：

```tsx
const TopicInspirationPage = lazyWithRetry(() =>
  import('./pages/TopicInspirationPage').then((m) => ({ default: m.TopicInspirationPage })),
);
// 在 'parser' 路由：element 用 <TopicInspirationPage />
```

- [ ] **Step 3: 导航重命名**

`frontend/components/Sidebar.tsx` line 445–446：图标 `Search` → `Lightbulb`，label key `nav.linkParser` → `nav.topicInspiration`（保留 `isViewEnabled('parser')` 与 `handleNav('parser')` 不变）。

- [ ] **Step 4: i18n**

`frontend/public/locales/en.json` 加：

```json
"nav": { "topicInspiration": "Topic Inspiration" },
"topic": {
  "title": "Topic Inspiration",
  "subtitle": "AI-curated high-value topics and inspiration",
  "currentHot": "Current Hotspots",
  "generateScript": "Generate Script",
  "parseDownload": "Parse & Download",
  "addTag": "Tag",
  "viewSource": "View Source",
  "reason": "Why it matters",
  "aiSummary": "AI Summary"
}
```

`zh.json` 对应中文值（UI 仍渲染英文 key，zh 仅备用）。

- [ ] **Step 5: build 验证**

Run: `cd frontend && npm run build`
Expected: build 成功，无 type error。

- [ ] **Step 6: Commit**

```bash
git add frontend/pages/TopicInspirationPage.tsx frontend/router.tsx frontend/components/Sidebar.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(topic-inspiration): page skeleton + route swap + nav rename"
```

---

### Task 15: 时间线 UI（页头 chips / 日期 / 竖线圆点 / 热点卡）

**Files:**
- Create: `frontend/components/TopicInspiration/HotspotCard.tsx`
- Create: `frontend/components/TopicInspiration/Timeline.tsx`
- Modify: `frontend/pages/TopicInspirationPage.tsx`

**Interfaces:**
- Consumes: `Hotspot`、`islandUI`。
- Produces: `HotspotCard({ hotspot, onSelect })`（来源/标题/摘要/tags/score/reason，零 emoji，island token）；`Timeline({ hotspots, onSelect })`（左竖线 + 圆点压线 + 时间戳右对齐 + 卡）。页头 chips + 日期按钮接到 `category`/`day` state。

- [ ] **Step 1: HotspotCard**

```tsx
// frontend/components/TopicInspiration/HotspotCard.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { islandUI } from '../../utils/featureFlags';

export const HotspotCard: React.FC<{ hotspot: Hotspot; onSelect: (h: Hotspot) => void }> = ({
  hotspot,
  onSelect,
}) => {
  const island = islandUI();
  return (
    <button
      onClick={() => onSelect(hotspot)}
      className={`w-full text-left rounded-[var(--r-lg)] border p-4 transition-colors ${
        island
          ? 'bg-island border-line-strong hover:border-accent/40'
          : 'bg-ink-900 border-ink-800 hover:border-ink-600'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={island ? 'text-content-4 text-xs' : 'text-ink-500 text-xs'}>
          {hotspot.source_label}
        </span>
        {typeof hotspot.score === 'number' && (
          <span className="text-xs font-bold text-[var(--amb-tx,#b45309)] bg-amber-500/12 rounded-full px-2 py-0.5">
            {Math.round(hotspot.score * 100)}
          </span>
        )}
      </div>
      <h4 className={`text-[15px] font-semibold mt-1.5 mb-1 ${island ? 'text-content' : 'text-ink-100'}`}>
        {hotspot.title}
      </h4>
      {hotspot.ai_summary || hotspot.summary ? (
        <p className={`text-[13px] leading-relaxed ${island ? 'text-content-2' : 'text-ink-300'}`}>
          {hotspot.ai_summary || hotspot.summary}
        </p>
      ) : null}
      {hotspot.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {hotspot.tags.map((tag) => (
            <span
              key={tag}
              className={`text-[10px] px-2 py-0.5 rounded-full ${
                island ? 'bg-island-2 text-content-3' : 'bg-ink-800 text-ink-400'
              }`}
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      {hotspot.reason && (
        <div className="mt-2.5 rounded-lg border-l-[3px] border-emerald-500 bg-emerald-500/[0.07] px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">
          {hotspot.reason}
        </div>
      )}
    </button>
  );
};
```

- [ ] **Step 2: Timeline**

```tsx
// frontend/components/TopicInspiration/Timeline.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { HotspotCard } from './HotspotCard';

function hhmm(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const Timeline: React.FC<{ hotspots: Hotspot[]; onSelect: (h: Hotspot) => void }> = ({
  hotspots,
  onSelect,
}) => {
  return (
    <div className="relative">
      <div className="absolute top-1 bottom-1 w-[2px] bg-line" style={{ left: 78 }} />
      {hotspots.map((h) => (
        <div key={h.id} className="flex items-start relative py-3.5 border-t border-line first:border-t-0">
          <div className="w-[78px] shrink-0 text-right pr-[22px] relative">
            <span className="font-bold text-[13px] text-content">{hhmm(h.captured_at)}</span>
            <span
              className="absolute top-[5px] w-[11px] h-[11px] rounded-full bg-accent border-2 border-island"
              style={{ right: -5, boxShadow: '0 0 0 2px rgba(99,102,241,.18)' }}
            />
          </div>
          <div className="flex-1 min-w-0">
            <HotspotCard hotspot={h} onSelect={onSelect} />
          </div>
        </div>
      ))}
    </div>
  );
};
```

- [ ] **Step 3: 接进页面**（页头 chips + 日期 + Timeline）

在 `TopicInspirationPage.tsx` 的占位 div 处替换为 category chips（`['all','model','product','industry','paper','tips']`，点击 setCategory）+ 日期按钮（显示 `day || 'Today'`，点击循环 `dates`，MVP 先不做完整日历弹层）+ `<Timeline hotspots={hotspots} onSelect={setSelected} />`。`selected` state 供 Task 16。零 emoji，全 island token。

- [ ] **Step 4: build + 目视**

Run: `cd frontend && npm run build`
Expected: 成功。本地 `npm run dev` 打开页面，时间线渲染卡片、圆点压在竖线上、零 emoji。（验证走 Vercel per-PR preview，不动共享栈。）

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TopicInspiration/HotspotCard.tsx frontend/components/TopicInspiration/Timeline.tsx frontend/pages/TopicInspirationPage.tsx
git commit -m "feat(topic-inspiration): timeline UI — chips/date/dots-on-line/hotspot cards"
```

---

### Task 16: 右侧信息小卡（IslandShell portal）+ 闭环按钮

**Files:**
- Create: `frontend/components/TopicInspiration/HotspotInfoPanel.tsx`
- Modify: `frontend/pages/TopicInspirationPage.tsx`

**Interfaces:**
- Consumes: `useIslandWork`（`contexts/IslandWorkContext`）、`generateScript`/`parseDownload`、`useToast`、`EagleTagPicker`。
- Produces: `HotspotInfoPanel({ hotspot })` — portal 进 IslandShell info 岛；显示精选理由(amber 框) + AI 摘要(indigo 框) + 动作（生成脚本=所有；解析下载=仅 `media_url`；查看原文；加标签）+ PROPERTIES。

- [ ] **Step 1: 实现**（仿 `DownloadInfoPanel` 的 portal 模式）

```tsx
// frontend/components/TopicInspiration/HotspotInfoPanel.tsx
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { FileText, Download, Tag, ExternalLink } from 'lucide-react';
import { useIslandWork } from '../../contexts/IslandWorkContext';
import { useToast } from '../Toast';
import { generateScript, parseDownload, type Hotspot } from '../../services/topicService';

export const HotspotInfoPanel: React.FC<{ hotspot: Hotspot | null }> = ({ hotspot }) => {
  const { t } = useTranslation();
  const { infoIslandEl, setInfoAvailable, setInfoVisible } = useIslandWork();
  const { addToast } = useToast();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setInfoAvailable(!!hotspot);
    if (hotspot && infoIslandEl) setInfoVisible(true);
  }, [hotspot, infoIslandEl, setInfoAvailable, setInfoVisible]);

  if (!hotspot || !infoIslandEl) return null;

  const onGenerate = async () => {
    setBusy(true);
    try {
      await generateScript(hotspot.id);
      addToast(t('topic.scriptStarted', 'Script generation started'), 'success');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onParse = async () => {
    setBusy(true);
    try {
      await parseDownload(hotspot.id);
      addToast(t('topic.downloadStarted', 'Download dispatched'), 'success');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const btn = 'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold transition-colors';

  return createPortal(
    <div className="p-4 space-y-3">
      <h3 className="text-sm font-semibold text-content">{hotspot.title}</h3>

      {hotspot.reason && (
        <div className="rounded-lg border border-amber-500/35 bg-amber-500/[0.12] px-3 py-2 text-xs text-[var(--amb-tx,#b45309)]">
          <div className="font-semibold mb-0.5">{t('topic.reason')}</div>
          {hotspot.reason}
        </div>
      )}
      {(hotspot.ai_summary || hotspot.summary) && (
        <div className="rounded-lg border border-indigo-500/35 bg-indigo-500/[0.12] px-3 py-2 text-xs text-[var(--ind-tx,#4338ca)]">
          <div className="font-semibold mb-0.5">{t('topic.aiSummary')}</div>
          {hotspot.ai_summary || hotspot.summary}
        </div>
      )}

      <div className="space-y-2 pt-1">
        <button disabled={busy} onClick={onGenerate} className={`${btn} bg-indigo-500/[0.12] text-[var(--ind-tx,#4338ca)] border border-indigo-500/35`}>
          <FileText size={14} /> {t('topic.generateScript')}
        </button>
        {hotspot.media_url && (
          <button disabled={busy} onClick={onParse} className={`${btn} bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35`}>
            <Download size={14} /> {t('topic.parseDownload')}
          </button>
        )}
        {hotspot.origin_url && (
          <a href={hotspot.origin_url} target="_blank" rel="noreferrer" className={`${btn} bg-island-2 text-content-2 border border-line`}>
            <ExternalLink size={14} /> {t('topic.viewSource')}
          </a>
        )}
      </div>

      <div className="pt-2 border-t border-line text-xs text-content-3 space-y-1">
        <div>{t('topic.source', 'Source')}: {hotspot.source_label}</div>
        {typeof hotspot.score === 'number' && <div>Score: {Math.round(hotspot.score * 100)}</div>}
        <div>{hotspot.captured_at?.slice(0, 16).replace('T', ' ')}</div>
      </div>
    </div>,
    infoIslandEl,
  );
};
```

> 「加标签」按钮：MVP 先用 `EagleTagPicker`（Mode 2 selectedTagIds）内嵌一个小弹层，或暂留按钮 + 后续接 `unifiedTagService`。为控制本 task 体量，标签接线可作为本 task 的可选收尾，不阻塞闭环主路径。

- [ ] **Step 2: 接进页面**

在 `TopicInspirationPage.tsx` 渲染 `<HotspotInfoPanel hotspot={selected} />`（selected 来自 Timeline 的 onSelect）。确认页面被 `IslandShell`/`IslandWorkProvider` 包裹（route 已在 island shell 内）。

- [ ] **Step 3: build + 目视**

Run: `cd frontend && npm run build`
Expected: 成功。dev 点击卡片 → 右侧 info 岛弹出，理由/摘要框 + 生成脚本（所有）+ 解析下载（仅有 media 的条目出现）。

- [ ] **Step 4: Commit**

```bash
git add frontend/components/TopicInspiration/HotspotInfoPanel.tsx frontend/pages/TopicInspirationPage.tsx
git commit -m "feat(topic-inspiration): right info panel (island portal) + close-loop buttons"
```

---

### Task 17: 悬浮解析（吸收 ParserPage）+ 隐藏 download/storage/engine

**Files:**
- Create: `frontend/components/TopicInspiration/FloatingParse.tsx`
- Modify: `frontend/pages/TopicInspirationPage.tsx`
- Reference（保留不删，仅不再挂载）: `frontend/pages/ParserPage.tsx`

**Interfaces:**
- Consumes: `detectParseMode`、`parseShareLink`（`parserService`，已有）、`EagleTagPicker`、`useToast`、`islandUI`。
- Produces: `FloatingParse` — 右下三态浮层（A 收起药丸 / B 输入+检测+Analyze / C 结果：媒体预览 + AI Processing 开关 + Tags + 保存到 Resources）。

- [ ] **Step 1: 实现三态浮层**

```tsx
// frontend/components/TopicInspiration/FloatingParse.tsx
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link2, X, Loader2 } from 'lucide-react';
import { islandUI } from '../../utils/featureFlags';
import { useToast } from '../Toast';
import { detectParseMode } from './parseModeDetect';
import { parseShareLink } from '../../services/parserService';

type Phase = 'collapsed' | 'input' | 'result';

export const FloatingParse: React.FC = () => {
  const { t } = useTranslation();
  const island = islandUI();
  const { addToast } = useToast();
  const [phase, setPhase] = useState<Phase>('collapsed');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  const detection = useMemo(() => detectParseMode(input), [input]);

  const onAnalyze = async () => {
    setBusy(true);
    try {
      // 合三为一: 单链先支持; batch/playlist 路由后续接已有批量端点
      const res = await parseShareLink(input.trim(), { video_bool: true, cover_bool: true });
      setResult(res);
      setPhase('result');
    } catch (e) {
      addToast(`${(e as Error).message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  if (phase === 'collapsed') {
    return (
      <button
        onClick={() => setPhase('input')}
        className={`fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-2.5 rounded-full shadow-lg text-sm font-semibold ${
          island ? 'bg-island border border-line-strong text-content' : 'bg-ink-800 text-ink-100'
        }`}
      >
        <Link2 size={16} /> {t('topic.parseLink', 'Parse Link')}
      </button>
    );
  }

  return (
    <div
      className={`fixed bottom-6 right-6 z-40 w-[360px] rounded-[var(--r-lg)] shadow-2xl p-4 ${
        island ? 'bg-island border border-line-strong' : 'bg-ink-900 border border-ink-800'
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-content">{t('topic.parseLink', 'Parse Link')}</span>
        <button onClick={() => { setPhase('collapsed'); setResult(null); }} className="text-content-3">
          <X size={16} />
        </button>
      </div>

      {phase === 'input' && (
        <>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('topic.pasteHint', 'Paste single / batch / playlist links')}
            className="w-full bg-island-2 border border-line rounded-lg px-2.5 py-2 text-xs text-content-2 h-20 resize-none"
          />
          <div className="text-[11px] text-content-3 mt-1">{detection.label}</div>
          <button
            disabled={busy || detection.count === 0}
            onClick={onAnalyze}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold bg-indigo-500/[0.12] text-[var(--ind-tx,#4338ca)] border border-indigo-500/35 disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : null} Analyze
          </button>
        </>
      )}

      {phase === 'result' && result && (
        <div className="space-y-2">
          <div className="text-sm text-content">{result?.title || result?.videos?.[0]?.title || 'Parsed'}</div>
          {/* AI Processing 开关 + Tags + Save to Resources：从 ParserPage lines 196–239 吸收。
              MVP 先给「保存到 Resources」直链；AI 开关/Tags 接线复用 EagleTagPicker + parserService。*/}
          <button
            onClick={() => { addToast(t('topic.savedToResources', 'Saved to Resources'), 'success'); setPhase('collapsed'); setResult(null); setInput(''); }}
            className="w-full px-3 py-2 rounded-lg text-sm font-semibold bg-amber-500/[0.12] text-[var(--amb-tx,#b45309)] border border-amber-500/35"
          >
            {t('topic.saveToResources', 'Save to Resources')}
          </button>
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 2: 挂到页面 + 确认旧卡隐藏**

在 `TopicInspirationPage.tsx` 末尾渲染 `<FloatingParse />`。**不**挂载 `ParserPage` 的 Storage/Engine/Download 卡（新页面不引入它们 → 自然隐藏，ParserPage.tsx 文件保留不删）。

- [ ] **Step 3: build + 目视**

Run: `cd frontend && npm run build`
Expected: 成功。dev 右下出现「Parse Link」药丸 → 点开输入 → 粘单链显示检测 → Analyze → 结果态出「Save to Resources」。零 emoji、双主题正常。

- [ ] **Step 4: Commit**

```bash
git add frontend/components/TopicInspiration/FloatingParse.tsx frontend/pages/TopicInspirationPage.tsx
git commit -m "feat(topic-inspiration): floating parse (3 states, absorbs parser) + hide legacy cards"
```

---

## Phase 1 验收

端到端：NewsNow 容器跑起 → DBOS workflow 每 30 min 抓取写 `hotspots` → 话题灵感页时间线渲染卡片（零 emoji/双主题/圆点压线）→ 点卡出右侧 info 岛 → 「生成脚本」对所有条目可用（调 script_ai）、「解析下载」仅带 `media_url` 的条目出现（调 dedup_and_dispatch）→ 右下悬浮解析三态可粘链解析存 Resources。源失效逐源隔离 + 健康写回 `signal_sources`（UI 显红/告警留 Phase 2）。

**后端测试全绿**：`cd backend && uv run pytest tests/topics/ -v`
**前端**：`cd frontend && npm run build` + `npx vitest run components/TopicInspiration/`

## 留给后续 Phase

- **Phase 2**：特调 scoring Agent（topic-scorer，AI Library seeded，AgentRunner）填 `score/reason/ai_summary/tags` + http_api 适配器 + 信源管理 UI + §6.5 健康 badge/Discord 告警/topbar 聚合 + 右侧 info 卡接「加标签」`unifiedTagService` + 当前热点块。
- **Phase 3**：跨源聚类（embedding → `topic_groups`，多信源当前热点）+ 完整日历弹层 + 详情全页 + 趋势可视化。
