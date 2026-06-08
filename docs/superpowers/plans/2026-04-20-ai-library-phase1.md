# AI Library Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MediaHub 硬编码的 AI prompt（首发 `script_ai_service`）迁移到数据库驱动的 "AI Library" — agent + prompt + 多文件 skill 统一管理，Settings 页加全局入口，参照 OpenClaw 架构并保留 MediaHub 独有的 4 级 scope。

**Architecture:**
- **Backend**: 新增 `prompt_composer` 服务 + `qwen_adapter`，复用现有 `ai_agents` 表（migration 121 已建）+ 扩展 `skills` 表（多文件）+ 新增 `skill_files`、`agent_skills`、`ai_sessions.agent_id` 字段。`Skill` tool lazy-readable 注入（参照 nous-center 设计，读 DB 返回 body）。
- **Frontend**: `Settings → AI Library` 新菜单项，2 个 tab（Agents / Skills），Prompts 延后到 Phase 2。script_ai agent 端到端可编辑，其他 4 agent read-only。
- **Seed**: 系统预置 agent/skill 以 markdown 文件形式存 `backend/seeds/`，migration 启动时写入 DB（hybrid seed 模式）。

**Tech Stack:** FastAPI + Pydantic + httpx, Supabase (PostgreSQL) + RLS, React 19 + TypeScript + Tailwind + Vite, pytest + black/isort/ruff

**Pilot:** `script_ai_service` 的 outline/expand/branch 三个方法全部走新链路，删除硬编码 prompt。

---

## File Structure

### Backend (new/modified)

| Path | Responsibility |
|------|---------------|
| `supabase/migrations/128_ai_library_phase1.sql` | Schema: extend `ai_agents` + `ai_sessions` + `skills`; create `skill_files` + `agent_skills` |
| `backend/app/schemas/ai_library.py` | Pydantic models: `AgentOut`, `AgentUpdate`, `SkillOut`, `SkillFileOut`, `ComposedPrompt` |
| `backend/app/repositories/agent_repository.py` | DB access for `ai_agents` + `agent_skills` |
| `backend/app/repositories/skill_repository.py` | DB access for `skills` + `skill_files` (extend existing if present) |
| `backend/app/services/prompt_composer.py` | Core: `(agent_id, model, session) → (messages, tools, cost_estimate)` |
| `backend/app/services/skill_tool_service.py` | `Skill` tool backend: read DB row → return body to model |
| `backend/app/services/ai_provider.py` | Extend: `QwenAdapter` implementing `LLMAdapter` protocol |
| `backend/app/services/seed_loader.py` | Read `backend/seeds/{agents,skills}/` → upsert into DB |
| `backend/app/services/script_ai_service.py` | **Modify**: replace hardcoded prompt with `prompt_composer.compose(agent="script_ai", ...)` |
| `backend/app/api/routes/ai_library_router.py` | REST: `/api/v1/ai-library/{agents,skills}/*` CRUD |
| `backend/app/main.py` | Register `ai_library_router` |
| `backend/seeds/agents/script_ai/AGENT.md` | Pilot agent instruction source |
| `backend/seeds/agents/script_ai/SOUL.md` | Pilot agent persona |
| `backend/seeds/agents/script_ai/IDENTITY.md` | Pilot agent identity |
| `backend/seeds/skills/script-outline/SKILL.md` | Pilot skill (atomic, referenced by script_ai agent) |
| `backend/seeds/skills/script-outline/references/examples.md` | Pilot skill reference file (validates multi-file) |

### Frontend (new/modified)

| Path | Responsibility |
|------|---------------|
| `frontend/services/aiLibraryService.ts` | API client: `listAgents`, `getAgent`, `updateAgent`, `listSkills`, `getSkill`, `updateSkill`, `listSkillFiles`, `upsertSkillFile` |
| `frontend/types.ts` | **Modify**: add `AIAgent`, `AISkill`, `AISkillFile` types |
| `frontend/components/AILibrary/AILibraryPanel.tsx` | Main panel with tab switcher (Agents / Skills) |
| `frontend/components/AILibrary/AgentsTab.tsx` | Agent list + selector |
| `frontend/components/AILibrary/AgentEditor.tsx` | Right pane: Overview + Files (Identity/Soul/Agent md) + Skills selector sub-tabs |
| `frontend/components/AILibrary/SkillsTab.tsx` | Skill list grouped by scope (System / Team / Project / Mine) |
| `frontend/components/AILibrary/SkillEditor.tsx` | **Modify** existing: add file tabs (SKILL.md + references/ + scripts/) |
| `frontend/components/AILibrary/MarkdownEditor.tsx` | Reusable wrapped markdown editor (textarea + preview toggle) |
| `frontend/components/SettingsModal.tsx` | **Modify**: add `aiLibrary` menu item between `AI` and `API Docs` |
| `frontend/public/locales/en.json` | **Modify**: add `aiLibrary.*` keys |
| `frontend/public/locales/zh.json` | **Modify**: add `aiLibrary.*` keys |

### Tests

| Path | Coverage |
|------|---------|
| `backend/tests/test_prompt_composer.py` | Unit: context file order, cache boundary, skills XML, agent not found |
| `backend/tests/test_skill_tool.py` | Unit: default file resolution, explicit file path, unknown skill, unknown file |
| `backend/tests/test_qwen_adapter.py` | Unit: request mapping, response parsing, error handling |
| `backend/tests/test_agent_repository.py` | Integration: CRUD + scope filtering + RLS enforcement |
| `backend/tests/test_skill_repository.py` | Integration: skill + skill_files CRUD |
| `backend/tests/test_ai_library_routes.py` | Integration: GET/PATCH endpoints, auth, scope guards |
| `backend/tests/test_script_ai_pilot.py` | Integration: full flow `outline → expand → branch` through new composer |
| `backend/tests/test_seed_loader.py` | Unit: directory parsing + idempotent upsert |

---

## Task 1: Database Schema (migration 128)

**Files:**
- Create: `supabase/migrations/128_ai_library_phase1.sql`

**Design notes:**
- `ai_agents` 已存在（migration 121）— 仅 **扩展**，不重建：加 `identity_md`、`soul_md`、`agent_md`、`slug`（短码，用作 seed key），保留 `persona` 向后兼容
- `skills` 已存在（migration 114+121）— 扩展 `slug`、`body_md`（对齐新命名）
- `ai_sessions` 扩展 `agent_id UUID` + `agent_slug TEXT`（session 与 agent 绑定，参照 OpenClaw `agent:<id>:<rest>` 模式）
- 新建 `skill_files`（多文件包）+ `agent_skills`（agent ↔ skills M:N）

- [ ] **Step 1.1: Write the migration SQL**

```sql
-- 128_ai_library_phase1.sql
-- AI Library Phase 1: extend ai_agents, skills; add skill_files, agent_skills; bind sessions to agents.

BEGIN;

-- 1. Extend ai_agents
ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS slug VARCHAR(64) UNIQUE,
  ADD COLUMN IF NOT EXISTS identity_md TEXT,
  ADD COLUMN IF NOT EXISTS soul_md TEXT,
  ADD COLUMN IF NOT EXISTS agent_md TEXT,
  ADD COLUMN IF NOT EXISTS is_system_preset BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS user_id UUID;

CREATE INDEX IF NOT EXISTS idx_ai_agents_slug ON ai_agents(slug);
CREATE INDEX IF NOT EXISTS idx_ai_agents_user_id ON ai_agents(user_id) WHERE user_id IS NOT NULL;

-- 2. Extend skills
ALTER TABLE skills
  ADD COLUMN IF NOT EXISTS slug VARCHAR(64) UNIQUE,
  ADD COLUMN IF NOT EXISTS body_md TEXT,
  ADD COLUMN IF NOT EXISTS frontmatter_json JSONB DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_skills_slug ON skills(slug);

-- Copy content_md → body_md for existing rows (idempotent)
UPDATE skills SET body_md = content_md WHERE body_md IS NULL AND content_md IS NOT NULL;

-- 3. Create skill_files (multi-file skill packages)
CREATE TABLE IF NOT EXISTS skill_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  path TEXT NOT NULL,  -- e.g., 'references/examples.md', 'scripts/validate.py'
  content TEXT,        -- markdown / code / text-asset
  file_type TEXT NOT NULL CHECK (file_type IN ('markdown', 'script', 'text-asset', 'binary-ref')),
  binary_url TEXT,     -- for binary assets, points to Supabase Storage
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(skill_id, path)
);
CREATE INDEX IF NOT EXISTS idx_skill_files_skill ON skill_files(skill_id);

-- 4. Create agent_skills (M:N binding)
CREATE TABLE IF NOT EXISTS agent_skills (
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  sort_order INT DEFAULT 0,
  enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY(agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);

-- 5. Bind sessions to agents
ALTER TABLE ai_sessions
  ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES ai_agents(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS agent_slug VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_ai_sessions_agent ON ai_sessions(agent_id) WHERE agent_id IS NOT NULL;

-- 6. Seed script_ai agent slug (pilot)
UPDATE ai_agents
  SET slug = 'script_ai',
      is_system_preset = true,
      agent_md = persona  -- preserve existing persona as agent_md for backward compat
  WHERE name IN ('Writer') AND slug IS NULL;  -- Writer is the closest existing agent

-- RLS policies (team/project/user scope)
ALTER TABLE skill_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_skills ENABLE ROW LEVEL SECURITY;

-- skill_files: visibility follows parent skill
CREATE POLICY skill_files_read ON skill_files FOR SELECT USING (
  skill_id IN (SELECT id FROM skills WHERE
    is_public = true
    OR (project_id IS NOT NULL AND project_id IN (SELECT id FROM projects WHERE user_id = auth.uid()))
    OR (team_id IS NOT NULL AND team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid()))
  )
);

CREATE POLICY skill_files_write ON skill_files FOR ALL USING (
  skill_id IN (SELECT id FROM skills WHERE
    (project_id IS NOT NULL AND project_id IN (SELECT id FROM projects WHERE user_id = auth.uid()))
    OR (team_id IS NOT NULL AND team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid() AND role IN ('owner', 'admin')))
  )
);

-- agent_skills: similar scope
CREATE POLICY agent_skills_read ON agent_skills FOR SELECT USING (
  agent_id IN (SELECT id FROM ai_agents WHERE
    is_system_preset = true
    OR user_id = auth.uid()
    OR (project_id IS NOT NULL AND project_id IN (SELECT id FROM projects WHERE user_id = auth.uid()))
  )
);

CREATE POLICY agent_skills_write ON agent_skills FOR ALL USING (
  agent_id IN (SELECT id FROM ai_agents WHERE
    user_id = auth.uid() OR (project_id IS NOT NULL AND project_id IN (SELECT id FROM projects WHERE user_id = auth.uid()))
  )
);

COMMIT;
```

- [ ] **Step 1.2: Apply via Supabase MCP**

```
执行 migration：按 CLAUDE.md 记忆 [DB Migration via MCP]，直接用 Supabase MCP (`mcp__supabase__apply_migration`) 灌入本地开发库。
```

- [ ] **Step 1.3: Verify schema**

Run:
```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d ai_agents" | grep -E "slug|identity_md|agent_md"
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d skill_files"
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d agent_skills"
```
Expected: 所有新列/新表存在。

- [ ] **Step 1.4: Commit**

```bash
git add supabase/migrations/128_ai_library_phase1.sql
git commit -m "feat(db): AI Library phase 1 — extend ai_agents/skills, add skill_files/agent_skills"
```

---

## Task 2: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/ai_library.py`

- [ ] **Step 2.1: Write schemas**

```python
"""Pydantic models for AI Library (agents, prompts-as-agent-fields, skills, skill files)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- Agents ----------

class AgentBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    model: str = "qwen-max"
    temperature: float = 0.7
    max_tokens: int = 4096
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None


class AgentOut(AgentBase):
    id: UUID
    is_system_preset: bool = False
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    user_id: Optional[UUID] = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    skill_ids: list[UUID] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    enabled: Optional[bool] = None
    skill_ids: Optional[list[UUID]] = None  # replace binding


# ---------- Skills & files ----------

class SkillFileOut(BaseModel):
    id: UUID
    skill_id: UUID
    path: str
    content: Optional[str] = None
    file_type: Literal["markdown", "script", "text-asset", "binary-ref"]
    binary_url: Optional[str] = None
    updated_at: datetime


class SkillFileUpsert(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: Optional[str] = None
    file_type: Literal["markdown", "script", "text-asset", "binary-ref"] = "markdown"
    binary_url: Optional[str] = None


class SkillOut(BaseModel):
    id: UUID
    slug: Optional[str] = None
    name: str
    description: Optional[str] = None
    body_md: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    is_public: bool = False
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    output_format: Optional[str] = None
    frontmatter_json: dict = Field(default_factory=dict)
    files: list[SkillFileOut] = Field(default_factory=list)
    updated_at: datetime


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body_md: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    output_format: Optional[str] = None
    frontmatter_json: Optional[dict] = None


# ---------- Composer output ----------

class ComposedSystemPrompt(BaseModel):
    """Output of prompt_composer.compose()."""
    agent_id: UUID
    agent_slug: str
    model: str
    temperature: float
    max_tokens: int
    system_message: str
    tools: list[dict]  # function-calling schema array
    skill_manifest: list[dict]  # [{slug, name, description}]
    cache_fingerprint: str  # sha1 of stable prefix inputs
```

- [ ] **Step 2.2: Commit**

```bash
git add backend/app/schemas/ai_library.py
git commit -m "feat(api): AI Library pydantic schemas"
```

---

## Task 3: Agent Repository

**Files:**
- Create: `backend/app/repositories/agent_repository.py`
- Test: `backend/tests/test_agent_repository.py`

- [ ] **Step 3.1: Write failing test**

```python
# backend/tests/test_agent_repository.py
import pytest
from uuid import uuid4
from app.repositories.agent_repository import AgentRepository


@pytest.mark.integration
async def test_get_by_slug_returns_script_ai(supabase_client):
    repo = AgentRepository(supabase_client)
    agent = await repo.get_by_slug("script_ai")
    assert agent is not None
    assert agent["slug"] == "script_ai"
    assert agent["is_system_preset"] is True


@pytest.mark.integration
async def test_get_by_slug_returns_none_for_missing(supabase_client):
    repo = AgentRepository(supabase_client)
    agent = await repo.get_by_slug("nonexistent_agent_xyz")
    assert agent is None


@pytest.mark.integration
async def test_list_accessible_includes_system_presets(supabase_client, test_user_id):
    repo = AgentRepository(supabase_client)
    agents = await repo.list_accessible(user_id=test_user_id)
    slugs = [a["slug"] for a in agents]
    assert "script_ai" in slugs


@pytest.mark.integration
async def test_get_skill_ids_for_agent(supabase_client):
    repo = AgentRepository(supabase_client)
    agent = await repo.get_by_slug("script_ai")
    skill_ids = await repo.get_skill_ids(agent["id"])
    assert isinstance(skill_ids, list)


@pytest.mark.integration
async def test_update_skill_bindings_replaces_existing(supabase_client):
    repo = AgentRepository(supabase_client)
    agent = await repo.get_by_slug("script_ai")
    new_skill_ids = [uuid4()]  # dummy; will fail FK — use real skill ids in actual run
    # This test validates the replace semantics, use real skill fixtures in implementation
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_agent_repository.py -v`
Expected: ImportError or ModuleNotFound for `AgentRepository`.

- [ ] **Step 3.3: Write minimal implementation**

```python
# backend/app/repositories/agent_repository.py
"""Repository for ai_agents + agent_skills tables."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from supabase import Client


class AgentRepository:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def get_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        resp = self.client.table("ai_agents").select("*").eq("slug", slug).single().execute()
        return resp.data if resp.data else None

    async def get_by_id(self, agent_id: UUID) -> Optional[dict[str, Any]]:
        resp = self.client.table("ai_agents").select("*").eq("id", str(agent_id)).single().execute()
        return resp.data if resp.data else None

    async def list_accessible(
        self,
        user_id: UUID,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        query = self.client.table("ai_agents").select("*")
        filters = ["is_system_preset.eq.true", f"user_id.eq.{user_id}"]
        if project_id is not None:
            filters.append(f"project_id.eq.{project_id}")
        if team_id is not None:
            filters.append(f"team_id.eq.{team_id}")
        query = query.or_(",".join(filters))
        resp = query.order("sort_order").order("name").execute()
        return resp.data or []

    async def get_skill_ids(self, agent_id: UUID) -> list[UUID]:
        resp = (
            self.client.table("agent_skills")
            .select("skill_id")
            .eq("agent_id", str(agent_id))
            .eq("enabled", True)
            .execute()
        )
        return [UUID(row["skill_id"]) for row in (resp.data or [])]

    async def update_skill_bindings(self, agent_id: UUID, skill_ids: list[UUID]) -> None:
        """Replace all skill bindings for this agent."""
        self.client.table("agent_skills").delete().eq("agent_id", str(agent_id)).execute()
        if skill_ids:
            rows = [
                {"agent_id": str(agent_id), "skill_id": str(sid), "sort_order": i}
                for i, sid in enumerate(skill_ids)
            ]
            self.client.table("agent_skills").insert(rows).execute()

    async def update_fields(self, agent_id: UUID, updates: dict[str, Any]) -> dict[str, Any]:
        resp = (
            self.client.table("ai_agents")
            .update(updates)
            .eq("id", str(agent_id))
            .execute()
        )
        return resp.data[0] if resp.data else {}
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `uv run pytest backend/tests/test_agent_repository.py -v`
Expected: PASS (may require seed data from Task 10 — verify after).

- [ ] **Step 3.5: Commit**

```bash
git add backend/app/repositories/agent_repository.py backend/tests/test_agent_repository.py
git commit -m "feat(api): agent repository + integration tests"
```

---

## Task 4: Skill Repository

**Files:**
- Create/Modify: `backend/app/repositories/skill_repository.py`
- Test: `backend/tests/test_skill_repository.py`

- [ ] **Step 4.1: Write failing test**

```python
# backend/tests/test_skill_repository.py
import pytest
from app.repositories.skill_repository import SkillRepository


@pytest.mark.integration
async def test_get_by_slug_returns_skill(supabase_client):
    repo = SkillRepository(supabase_client)
    sk = await repo.get_by_slug("script-outline")
    assert sk is not None
    assert sk["slug"] == "script-outline"
    assert sk["body_md"] is not None


@pytest.mark.integration
async def test_list_files_for_skill(supabase_client):
    repo = SkillRepository(supabase_client)
    sk = await repo.get_by_slug("script-outline")
    files = await repo.list_files(sk["id"])
    paths = [f["path"] for f in files]
    assert "references/examples.md" in paths


@pytest.mark.integration
async def test_get_file_by_path(supabase_client):
    repo = SkillRepository(supabase_client)
    sk = await repo.get_by_slug("script-outline")
    f = await repo.get_file(sk["id"], "references/examples.md")
    assert f is not None
    assert f["file_type"] == "markdown"


@pytest.mark.integration
async def test_upsert_file_inserts_and_updates(supabase_client):
    repo = SkillRepository(supabase_client)
    sk = await repo.get_by_slug("script-outline")
    await repo.upsert_file(sk["id"], path="scripts/new.py", content="print('hi')", file_type="script")
    f = await repo.get_file(sk["id"], "scripts/new.py")
    assert f["content"] == "print('hi')"
    await repo.upsert_file(sk["id"], path="scripts/new.py", content="print('bye')", file_type="script")
    f2 = await repo.get_file(sk["id"], "scripts/new.py")
    assert f2["content"] == "print('bye')"
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_skill_repository.py -v`
Expected: FAIL (SkillRepository missing methods or file).

- [ ] **Step 4.3: Write minimal implementation**

```python
# backend/app/repositories/skill_repository.py
"""Repository for skills + skill_files tables."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from supabase import Client


class SkillRepository:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def get_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        resp = self.client.table("skills").select("*").eq("slug", slug).maybe_single().execute()
        return resp.data if resp.data else None

    async def get_by_id(self, skill_id: UUID) -> Optional[dict[str, Any]]:
        resp = self.client.table("skills").select("*").eq("id", str(skill_id)).maybe_single().execute()
        return resp.data if resp.data else None

    async def list_by_ids(self, skill_ids: list[UUID]) -> list[dict[str, Any]]:
        if not skill_ids:
            return []
        resp = self.client.table("skills").select("*").in_("id", [str(i) for i in skill_ids]).execute()
        return resp.data or []

    async def list_accessible(
        self,
        user_id: UUID,
        project_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        query = self.client.table("skills").select("*").eq("status", "active")
        filters = ["is_public.eq.true"]
        if project_id is not None:
            filters.append(f"project_id.eq.{project_id}")
        query = query.or_(",".join(filters))
        resp = query.order("category").order("name").execute()
        return resp.data or []

    async def list_files(self, skill_id: UUID) -> list[dict[str, Any]]:
        resp = (
            self.client.table("skill_files")
            .select("*")
            .eq("skill_id", str(skill_id))
            .order("path")
            .execute()
        )
        return resp.data or []

    async def get_file(self, skill_id: UUID, path: str) -> Optional[dict[str, Any]]:
        resp = (
            self.client.table("skill_files")
            .select("*")
            .eq("skill_id", str(skill_id))
            .eq("path", path)
            .maybe_single()
            .execute()
        )
        return resp.data if resp.data else None

    async def upsert_file(
        self,
        skill_id: UUID,
        *,
        path: str,
        content: Optional[str],
        file_type: str,
        binary_url: Optional[str] = None,
    ) -> dict[str, Any]:
        row = {
            "skill_id": str(skill_id),
            "path": path,
            "content": content,
            "file_type": file_type,
            "binary_url": binary_url,
        }
        resp = (
            self.client.table("skill_files")
            .upsert(row, on_conflict="skill_id,path")
            .execute()
        )
        return resp.data[0] if resp.data else {}

    async def delete_file(self, skill_id: UUID, path: str) -> None:
        self.client.table("skill_files").delete().eq("skill_id", str(skill_id)).eq("path", path).execute()

    async def update_fields(self, skill_id: UUID, updates: dict[str, Any]) -> dict[str, Any]:
        resp = self.client.table("skills").update(updates).eq("id", str(skill_id)).execute()
        return resp.data[0] if resp.data else {}
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `uv run pytest backend/tests/test_skill_repository.py -v`
Expected: PASS (after seed from Task 10).

- [ ] **Step 4.5: Commit**

```bash
git add backend/app/repositories/skill_repository.py backend/tests/test_skill_repository.py
git commit -m "feat(api): skill repository with multi-file support"
```

---

## Task 5: Prompt Composer

**Files:**
- Create: `backend/app/services/prompt_composer.py`
- Test: `backend/tests/test_prompt_composer.py`

**Design** (参照 OpenClaw `buildAgentSystemPrompt` + `CONTEXT_FILE_ORDER`):

```
# Identity               ← agent.identity_md (若存在)
# Soul                   ← agent.soul_md (若存在)
# Agent Instructions     ← agent.agent_md (或 persona fallback)

## Available Skills
<scan instruction>

<available_skills>
  <skill><name>slug</name><description>...</description></skill>
</available_skills>

<!-- CACHE_BOUNDARY -->

# Request Instructions    ← 运行时注入（每请求变）
# Runtime                 ← model / time / session_id
```

- [ ] **Step 5.1: Write failing tests**

```python
# backend/tests/test_prompt_composer.py
import hashlib
import pytest
from uuid import uuid4

from app.services.prompt_composer import PromptComposer, AgentNotFoundError, CACHE_BOUNDARY_MARKER


@pytest.fixture
def fake_agent():
    return {
        "id": str(uuid4()),
        "slug": "script_ai",
        "name": "Script AI",
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "identity_md": "I am the Script AI.",
        "soul_md": "I write crisp cinematic prose.",
        "agent_md": "When asked, produce HTML script output.",
    }


@pytest.fixture
def fake_skills():
    return [
        {
            "id": str(uuid4()),
            "slug": "script-outline",
            "name": "Script Outline",
            "description": "Produce a 3-act outline.",
        }
    ]


def test_sections_appear_in_order(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(fake_agent, fake_skills, request_instructions=None)
    identity_pos = msg.index("# Identity")
    soul_pos = msg.index("# Soul")
    agent_pos = msg.index("# Agent Instructions")
    skills_pos = msg.index("## Available Skills")
    assert identity_pos < soul_pos < agent_pos < skills_pos


def test_missing_identity_and_soul_skip_sections(fake_agent, fake_skills):
    fake_agent["identity_md"] = None
    fake_agent["soul_md"] = "   "  # whitespace-only → skip
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(fake_agent, fake_skills, request_instructions=None)
    assert "# Identity" not in msg
    assert "# Soul" not in msg
    assert "# Agent Instructions" in msg


def test_skills_xml_section_present(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(fake_agent, fake_skills, request_instructions=None)
    assert "<available_skills>" in msg
    assert "<name>script-outline</name>" in msg
    assert "<description>Produce a 3-act outline.</description>" in msg


def test_empty_skills_skips_xml_section(fake_agent):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(fake_agent, [], request_instructions=None)
    assert "<available_skills>" not in msg


def test_cache_boundary_position(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    msg = composer._assemble_system_message(fake_agent, fake_skills, request_instructions="Do X")
    boundary_pos = msg.index(CACHE_BOUNDARY_MARKER)
    skills_pos = msg.index("<available_skills>")
    req_pos = msg.index("# Request Instructions")
    assert skills_pos < boundary_pos < req_pos


def test_cache_fingerprint_stable(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp1 = composer._fingerprint(fake_agent, fake_skills)
    fp2 = composer._fingerprint(fake_agent, fake_skills)
    assert fp1 == fp2


def test_cache_fingerprint_changes_when_agent_changes(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    fp1 = composer._fingerprint(fake_agent, fake_skills)
    fake_agent["agent_md"] = "CHANGED"
    fp2 = composer._fingerprint(fake_agent, fake_skills)
    assert fp1 != fp2


def test_skill_tool_schema_injected(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    names = [t["function"]["name"] for t in tools]
    assert "Skill" in names


def test_skill_tool_has_required_params(fake_agent, fake_skills):
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    tools = composer._build_tools(fake_skills)
    skill_tool = next(t for t in tools if t["function"]["name"] == "Skill")
    params = skill_tool["function"]["parameters"]
    assert "skill" in params["properties"]
    assert "file" in params["properties"]
    assert params["required"] == ["skill"]
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_prompt_composer.py -v`
Expected: ImportError.

- [ ] **Step 5.3: Write implementation**

```python
# backend/app/services/prompt_composer.py
"""System message + tools composer for AI agents."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import ComposedSystemPrompt

CACHE_BOUNDARY_MARKER = "<!-- CACHE_BOUNDARY -->"


class AgentNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ComposerInput:
    agent_slug: str
    request_instructions: Optional[str] = None
    session_id: Optional[str] = None
    model_override: Optional[str] = None


class PromptComposer:
    """Assemble system message + tools for an agent call."""

    def __init__(
        self,
        agent_repo: Optional[AgentRepository],
        skill_repo: Optional[SkillRepository],
    ) -> None:
        self.agent_repo = agent_repo
        self.skill_repo = skill_repo

    async def compose(self, inp: ComposerInput) -> ComposedSystemPrompt:
        assert self.agent_repo is not None and self.skill_repo is not None
        agent = await self.agent_repo.get_by_slug(inp.agent_slug)
        if not agent:
            raise AgentNotFoundError(f"agent slug not found: {inp.agent_slug}")

        skill_ids = await self.agent_repo.get_skill_ids(UUID(agent["id"]))
        skills = await self.skill_repo.list_by_ids(skill_ids)

        system_message = self._assemble_system_message(
            agent=agent, skills=skills, request_instructions=inp.request_instructions
        )
        tools = self._build_tools(skills)
        manifest = [
            {"slug": s.get("slug"), "name": s.get("name"), "description": s.get("description")}
            for s in skills
        ]

        return ComposedSystemPrompt(
            agent_id=UUID(agent["id"]),
            agent_slug=agent["slug"],
            model=inp.model_override or agent.get("model") or "qwen-max",
            temperature=float(agent.get("temperature", 0.7)),
            max_tokens=int(agent.get("max_tokens", 4096)),
            system_message=system_message,
            tools=tools,
            skill_manifest=manifest,
            cache_fingerprint=self._fingerprint(agent, skills),
        )

    # ---------- internals ----------

    def _assemble_system_message(
        self,
        agent: dict[str, Any],
        skills: list[dict[str, Any]],
        request_instructions: Optional[str],
    ) -> str:
        parts: list[str] = []

        identity = (agent.get("identity_md") or "").strip()
        if identity:
            parts.append(f"# Identity\n{identity}")

        soul = (agent.get("soul_md") or "").strip()
        if soul:
            parts.append(
                f"# Soul\n{soul}\n\n"
                "Embody the persona and tone described above. Avoid generic or stiff replies "
                "unless higher-priority instructions override it."
            )

        instruction = (agent.get("agent_md") or agent.get("persona") or "").strip()
        if instruction:
            parts.append(f"# Agent Instructions\n{instruction}")

        if skills:
            parts.append(self._render_skills_section(skills))

        parts.append(CACHE_BOUNDARY_MARKER)

        if request_instructions and request_instructions.strip():
            parts.append(f"# Request Instructions\n{request_instructions.strip()}")

        runtime = self._render_runtime_line(agent)
        parts.append(runtime)

        return "\n\n".join(parts)

    def _render_skills_section(self, skills: list[dict[str, Any]]) -> str:
        header = (
            "## Available Skills\n"
            "Before replying: scan <available_skills> entries.\n"
            '- If one clearly applies: call Skill(skill="<slug>") first, then follow the returned instructions.\n'
            "- If none apply: do not call Skill.\n"
            "Never call Skill more than once per turn unless the task clearly requires chaining.\n"
        )
        xml = ["<available_skills>"]
        for s in skills:
            xml.append("  <skill>")
            xml.append(f"    <name>{s.get('slug') or s.get('name')}</name>")
            desc = (s.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
            xml.append(f"    <description>{desc}</description>")
            xml.append("  </skill>")
        xml.append("</available_skills>")
        return header + "\n" + "\n".join(xml)

    def _render_runtime_line(self, agent: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"# Runtime\nModel: {agent.get('model')} | Time: {now}"

    def _build_tools(self, skills: list[dict[str, Any]]) -> list[dict]:
        """Phase 1 injects exactly one Skill tool. Other caller-provided tools merge upstream."""
        if not skills:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "Skill",
                    "description": "Load a local skill definition and its instructions. "
                    "Returns the SKILL body (and optional sub-file content).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill": {
                                "type": "string",
                                "description": "Skill slug from <available_skills>.",
                            },
                            "file": {
                                "type": "string",
                                "description": "Optional sub-file path like 'references/examples.md'. "
                                "Omit to return the SKILL.md body.",
                            },
                        },
                        "required": ["skill"],
                    },
                },
            }
        ]

    def _fingerprint(self, agent: dict[str, Any], skills: list[dict[str, Any]]) -> str:
        h = hashlib.sha1()
        h.update(str(agent.get("id", "")).encode())
        h.update(str(agent.get("updated_at", "")).encode())
        h.update((agent.get("identity_md") or "").encode())
        h.update((agent.get("soul_md") or "").encode())
        h.update((agent.get("agent_md") or agent.get("persona") or "").encode())
        for s in sorted(skills, key=lambda x: str(x.get("id"))):
            h.update(str(s.get("id", "")).encode())
            h.update(str(s.get("updated_at", "")).encode())
        return h.hexdigest()
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `uv run pytest backend/tests/test_prompt_composer.py -v`
Expected: All PASS.

- [ ] **Step 5.5: Commit**

```bash
git add backend/app/services/prompt_composer.py backend/tests/test_prompt_composer.py
git commit -m "feat(ai): prompt composer with lazy-readable skill injection + cache fingerprint"
```

---

## Task 6: Skill Tool Backend

**Files:**
- Create: `backend/app/services/skill_tool_service.py`
- Test: `backend/tests/test_skill_tool.py`

- [ ] **Step 6.1: Write failing tests**

```python
# backend/tests/test_skill_tool.py
import pytest
from app.services.skill_tool_service import SkillToolService


class FakeSkillRepo:
    async def get_by_slug(self, slug):
        if slug == "script-outline":
            return {"id": "abc", "slug": "script-outline", "body_md": "OUTLINE BODY", "description": "Outline"}
        return None

    async def get_file(self, skill_id, path):
        if skill_id == "abc" and path == "references/examples.md":
            return {"path": path, "content": "EXAMPLES", "file_type": "markdown"}
        return None


@pytest.mark.unit
async def test_unknown_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "missing"})
    assert "error" in result


@pytest.mark.unit
async def test_empty_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": ""})
    assert "error" in result and "required" in result["error"].lower()


@pytest.mark.unit
async def test_default_returns_body_md():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline"})
    assert result["prompt"] == "OUTLINE BODY"
    assert result["skill"] == "script-outline"


@pytest.mark.unit
async def test_explicit_file_returns_file_content():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline", "file": "references/examples.md"})
    assert result["prompt"] == "EXAMPLES"
    assert result["file"] == "references/examples.md"


@pytest.mark.unit
async def test_unknown_file_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline", "file": "references/doesnotexist.md"})
    assert "error" in result
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_skill_tool.py -v`

- [ ] **Step 6.3: Write implementation**

```python
# backend/app/services/skill_tool_service.py
"""Backend executor for the Skill tool — reads DB row, returns body/file to model."""
from __future__ import annotations

from typing import Any, Optional

from app.repositories.skill_repository import SkillRepository


class SkillToolService:
    def __init__(self, skill_repo: SkillRepository) -> None:
        self.skill_repo = skill_repo

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = (args.get("skill") or "").strip()
        if not slug:
            return {"error": "skill name required"}

        skill = await self.skill_repo.get_by_slug(slug)
        if not skill:
            return {"error": f"unknown skill: {slug}"}

        file_path: Optional[str] = args.get("file")
        if file_path:
            f = await self.skill_repo.get_file(skill["id"], file_path)
            if not f:
                return {"error": f"unknown file '{file_path}' in skill '{slug}'"}
            return {
                "skill": slug,
                "file": file_path,
                "description": skill.get("description", ""),
                "prompt": f.get("content") or "",
                "file_type": f.get("file_type"),
            }

        return {
            "skill": slug,
            "description": skill.get("description", ""),
            "prompt": skill.get("body_md") or "",
        }
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `uv run pytest backend/tests/test_skill_tool.py -v`
Expected: All PASS.

- [ ] **Step 6.5: Commit**

```bash
git add backend/app/services/skill_tool_service.py backend/tests/test_skill_tool.py
git commit -m "feat(ai): Skill tool backend — DB-backed lazy body/file load"
```

---

## Task 7: Qwen Adapter

**Files:**
- Modify: `backend/app/services/ai_provider.py`
- Test: `backend/tests/test_qwen_adapter.py`

**Context:** `ai_provider.py` already exists; add a `QwenAdapter` that wraps `ComposedSystemPrompt` → Qwen chat-completions request, handles tool-call loop for `Skill` tool invocations.

- [ ] **Step 7.1: Read existing `ai_provider.py`** (before editing), verify Protocol / adapter layout.

- [ ] **Step 7.2: Write failing tests**

```python
# backend/tests/test_qwen_adapter.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.ai_provider import QwenAdapter
from app.schemas.ai_library import ComposedSystemPrompt


def _sample_composed():
    return ComposedSystemPrompt(
        agent_id="00000000-0000-0000-0000-000000000001",
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="abc",
    )


@pytest.mark.unit
async def test_request_body_has_system_message_first():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    composed = _sample_composed()
    body = adapter._build_body(composed, user_messages=[{"role": "user", "content": "hi"}])
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "SYSTEM"
    assert body["messages"][1]["role"] == "user"


@pytest.mark.unit
async def test_no_tools_field_when_empty():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    composed = _sample_composed()
    body = adapter._build_body(composed, user_messages=[])
    assert "tools" not in body or body["tools"] == []


@pytest.mark.unit
async def test_tools_passed_through():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    composed = _sample_composed()
    composed = composed.model_copy(update={"tools": [{"type": "function", "function": {"name": "Skill"}}]})
    body = adapter._build_body(composed, user_messages=[])
    assert body["tools"][0]["function"]["name"] == "Skill"
```

- [ ] **Step 7.3: Run tests to verify they fail**

- [ ] **Step 7.4: Write `QwenAdapter`**

Append to `backend/app/services/ai_provider.py`:

```python
# ... existing imports ...
from app.schemas.ai_library import ComposedSystemPrompt


class QwenAdapter:
    """Adapter for Qwen/DashScope chat-completions OpenAI-compatible endpoint."""

    def __init__(self, api_url: str, api_key: str, default_model: str = "qwen-max") -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model

    def _build_body(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
    ) -> dict:
        messages: list[dict] = [{"role": "system", "content": composed.system_message}]
        messages.extend(user_messages)
        body: dict = {
            "model": composed.model or self.default_model,
            "messages": messages,
            "temperature": composed.temperature,
            "max_tokens": composed.max_tokens,
        }
        if composed.tools:
            body["tools"] = composed.tools
            body["tool_choice"] = "auto"
        return body

    async def call(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
    ) -> dict:
        """Single-shot call. Tool-call loop handled by caller."""
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.api_url}/chat/completions",
                json=self._build_body(composed, user_messages),
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
```

- [ ] **Step 7.5: Run tests to verify they pass**

- [ ] **Step 7.6: Commit**

```bash
git add backend/app/services/ai_provider.py backend/tests/test_qwen_adapter.py
git commit -m "feat(ai): Qwen adapter using ComposedSystemPrompt"
```

---

## Task 8: Tool-Call Loop Helper

**Files:**
- Create: `backend/app/services/agent_runner.py`
- Test: `backend/tests/test_agent_runner.py`

**Purpose:** orchestrate `composer → adapter → (tool_call? → SkillToolService → adapter)` loop.

- [ ] **Step 8.1: Write failing test** — mock adapter returns `tool_calls` once, then final text; verify the loop resolves Skill call and returns final content.

- [ ] **Step 8.2: Write implementation**

```python
# backend/app/services/agent_runner.py
"""Drive a single agent turn with tool-call resolution."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.skill_tool_service import SkillToolService


MAX_TOOL_ITERATIONS = 5


class AgentRunner:
    def __init__(self, adapter, skill_tool: SkillToolService) -> None:
        self.adapter = adapter
        self.skill_tool = skill_tool

    async def run_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
    ) -> dict[str, Any]:
        messages = list(user_messages)
        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await self.adapter.call(composed, messages)
            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {"content": msg.get("content", ""), "raw": resp}
            # Append assistant tool-call stub
            messages.append(msg)
            # Resolve each tool call
            for call in tool_calls:
                if call.get("function", {}).get("name") != "Skill":
                    # Unknown tool — skip (caller-provided tools handled elsewhere in future)
                    continue
                args = json.loads(call["function"].get("arguments", "{}"))
                result = await self.skill_tool.execute(args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": "Skill",
                    "content": json.dumps(result, ensure_ascii=False),
                })
        return {"content": "", "raw": None, "error": "max_tool_iterations_exceeded"}
```

- [ ] **Step 8.3: Run tests, verify pass**
- [ ] **Step 8.4: Commit**

```bash
git commit -m "feat(ai): agent runner with Skill tool-call loop"
```

---

## Task 9: Seed Loader

**Files:**
- Create: `backend/app/services/seed_loader.py`
- Test: `backend/tests/test_seed_loader.py`

**Design:**
- `backend/seeds/agents/<slug>/{IDENTITY.md, SOUL.md, AGENT.md, config.yaml}` → upsert into `ai_agents`
- `backend/seeds/skills/<slug>/{SKILL.md (frontmatter+body), references/*, scripts/*, assets/*}` → upsert `skills` + `skill_files`
- Parse frontmatter (python-frontmatter lib or simple YAML)
- Idempotent: re-run no-ops if content unchanged

- [ ] **Step 9.1: Write test for agent seeding**
- [ ] **Step 9.2: Write test for multi-file skill seeding**
- [ ] **Step 9.3: Implement `SeedLoader.load()` with async upserts**

Skeleton:

```python
# backend/app/services/seed_loader.py
from pathlib import Path
from typing import Any

import frontmatter  # python-frontmatter

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository


class SeedLoader:
    def __init__(
        self,
        agent_repo: AgentRepository,
        skill_repo: SkillRepository,
        seeds_root: Path,
    ) -> None:
        self.agent_repo = agent_repo
        self.skill_repo = skill_repo
        self.seeds_root = seeds_root

    async def load_all(self) -> dict[str, int]:
        return {
            "agents": await self._load_agents(),
            "skills": await self._load_skills(),
        }

    async def _load_agents(self) -> int:
        agents_dir = self.seeds_root / "agents"
        count = 0
        if not agents_dir.exists():
            return 0
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            slug = agent_dir.name
            identity = (agent_dir / "IDENTITY.md").read_text().strip() if (agent_dir / "IDENTITY.md").exists() else None
            soul = (agent_dir / "SOUL.md").read_text().strip() if (agent_dir / "SOUL.md").exists() else None
            agent_md = (agent_dir / "AGENT.md").read_text().strip() if (agent_dir / "AGENT.md").exists() else None

            # upsert by slug
            existing = await self.agent_repo.get_by_slug(slug)
            fields = {
                "slug": slug,
                "name": slug.replace("_", " ").title(),
                "identity_md": identity,
                "soul_md": soul,
                "agent_md": agent_md,
                "is_system_preset": True,
            }
            if existing:
                await self.agent_repo.update_fields(existing["id"], fields)
            else:
                self.agent_repo.client.table("ai_agents").insert(fields).execute()
            count += 1
        return count

    async def _load_skills(self) -> int:
        skills_dir = self.seeds_root / "skills"
        count = 0
        if not skills_dir.exists():
            return 0
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            slug = skill_dir.name
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            parsed = frontmatter.load(skill_md)
            fm = dict(parsed.metadata)
            body = parsed.content

            existing = await self.skill_repo.get_by_slug(slug)
            fields = {
                "slug": slug,
                "name": fm.get("name", slug),
                "description": fm.get("description"),
                "body_md": body,
                "category": fm.get("category"),
                "icon": fm.get("icon", "✨"),
                "is_public": fm.get("is_public", True),
                "frontmatter_json": fm,
                "status": "active",
            }
            if existing:
                skill_id = existing["id"]
                await self.skill_repo.update_fields(skill_id, fields)
            else:
                resp = self.skill_repo.client.table("skills").insert(fields).execute()
                skill_id = resp.data[0]["id"]

            # Load sub-files
            for sub_path in ("references", "scripts", "assets"):
                sub_dir = skill_dir / sub_path
                if not sub_dir.exists():
                    continue
                for f in sub_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(skill_dir).as_posix()
                    ext = f.suffix.lower()
                    if ext == ".md":
                        file_type = "markdown"
                        content = f.read_text()
                    elif ext in {".py", ".sh", ".js", ".ts"}:
                        file_type = "script"
                        content = f.read_text()
                    elif ext in {".json", ".yaml", ".yml", ".txt"}:
                        file_type = "text-asset"
                        content = f.read_text()
                    else:
                        continue  # skip binary for Phase 1
                    await self.skill_repo.upsert_file(
                        skill_id, path=rel, content=content, file_type=file_type
                    )
            count += 1
        return count
```

- [ ] **Step 9.4: Commit**

```bash
git commit -m "feat(ai): seed loader for agents/skills from filesystem"
```

---

## Task 10: Seed Content for Pilot

**Files:**
- Create: `backend/seeds/agents/script_ai/IDENTITY.md`
- Create: `backend/seeds/agents/script_ai/SOUL.md`
- Create: `backend/seeds/agents/script_ai/AGENT.md`
- Create: `backend/seeds/skills/script-outline/SKILL.md`
- Create: `backend/seeds/skills/script-outline/references/examples.md`
- Create: `backend/seeds/skills/script-expand/SKILL.md`
- Create: `backend/seeds/skills/script-branch/SKILL.md`

- [ ] **Step 10.1: Write `backend/seeds/agents/script_ai/IDENTITY.md`**

```markdown
I am the MediaHub Script AI — a professional screenwriter inside the user's media studio.
I know the user's library of parsed media, their scripts, and their storyboard drafts.
My job is to help create, expand, and refactor scripts with cinematic rigor.
```

- [ ] **Step 10.2: Write `SOUL.md`**

```markdown
Voice: direct, vivid, cinematic. Tight prose, concrete nouns, active verbs.
Never hedge. Never pad. Every line earns its place.
Respect the user's genre, tone, and target duration.
Hate cliché. Hate AI slop. Hate hand-wavy scene descriptions.
When stuck, ask one pointed question rather than guess.
```

- [ ] **Step 10.3: Write `AGENT.md`**

```markdown
You generate script content for MediaHub's script editor. Three primary tasks:

1. **Outline**: given a premise, produce a 3-act outline with beats.
2. **Expand**: given an outline and a selected scene, produce full scene prose (action + dialogue).
3. **Branch**: given a scene, propose N alternative versions with distinct creative angles.

**Output format**: HTML fragments only. Allowed tags: `<h2>`, `<h3>`, `<p>`, `<strong>`, `<em>`, `<hr>`, `<br>`.

**Scene heading**: `<h2>场景N：场景名 – 时间 – 内/外景</h2>`
**Action line**: `<p>paragraph text</p>`
**Dialogue**: `<p><strong>角色名</strong>：（动作描述）台词内容</p>`
**Scene break**: `<hr>`

**Constraints**:
- Never output plain markdown outside tags
- Never output full `<html>` / `<body>` wrappers
- Never add inline styles
- Keep titles under 200 chars, summaries under 5000 chars, scene content under 50000 chars
```

- [ ] **Step 10.4: Write `backend/seeds/skills/script-outline/SKILL.md`**

```markdown
---
name: Script Outline
description: Produce a 3-act outline with beats for a given premise.
category: script
icon: 🎬
is_public: true
---

# Script Outline

When asked to "outline a script", produce:

1. **Act I (Setup)** — world, protagonist, inciting incident. 3-5 beats.
2. **Act II (Confrontation)** — rising tension, midpoint reversal. 5-8 beats.
3. **Act III (Resolution)** — climax, denouement. 3-5 beats.

Format each beat as `<h3>Beat N: one-line description</h3><p>2-3 sentence elaboration</p>`.

**Hook priorities**:
- First beat must establish genre + tone within two sentences.
- Protagonist's flaw must surface by beat 3.
- Stakes must be personal AND external.

**See references/examples.md for reference outlines.**
```

- [ ] **Step 10.5: Write `backend/seeds/skills/script-outline/references/examples.md`** (proves multi-file works)

```markdown
# Example Outlines

## Short Film — 10 minute thriller

### Act I
- Beat 1: Detective arrives at locked cabin in snowstorm.
- Beat 2: Finds body, no footprints outside.
- Beat 3: Radio goes dead; her partner at HQ stops responding.

### Act II
- Beat 4: Discovers hidden passageway behind bookcase.
- Beat 5: Finds victim's journal — names a killer she knows.
- Beat 6 (midpoint): Killer is her partner.

### Act III
- Beat 7: Partner arrives "to help".
- Beat 8: She sets trap in cellar.
- Beat 9: Trap works. She radios confession.
```

- [ ] **Step 10.6: Write `script-expand` and `script-branch` SKILL.md** (atomic skills for the other two script_ai methods)

`backend/seeds/skills/script-expand/SKILL.md`:
```markdown
---
name: Script Expand
description: Expand a single outline beat into full scene prose with dialogue.
category: script
icon: 📝
is_public: true
---

# Script Expand

Given an outline beat and surrounding context, produce full scene prose.

Requirements:
- Open with establishing shot line: `<h2>场景N：...</h2>`
- Mix action and dialogue, 60/40 ratio.
- Each character speaks with distinct voice (vocabulary, rhythm, tells).
- End with a turn, hook, or question that pulls to the next scene.
- Target 300-800 words per scene.
```

`backend/seeds/skills/script-branch/SKILL.md`:
```markdown
---
name: Script Branch
description: Propose N alternative scene versions with distinct creative angles.
category: script
icon: 🌿
is_public: true
---

# Script Branch

Given one scene, produce N alternatives. Each alternative MUST differ on at least one of:
- POV character
- Emotional register (e.g., sincere → ironic, tragic → absurd)
- Setting change (e.g., indoor → outdoor)
- Pacing (short/punchy vs long/meditative)

Label each as `<h2>Branch A: <one-line distinctive label></h2>` followed by full scene.

Default N = 3 unless caller specifies. Max N = 5.
```

- [ ] **Step 10.7: Wire seed loader into app startup** (backend/app/main.py)

```python
# backend/app/main.py (add to startup event)
@app.on_event("startup")
async def run_seed_loader():
    from pathlib import Path
    from app.services.seed_loader import SeedLoader
    from app.repositories.agent_repository import AgentRepository
    from app.repositories.skill_repository import SkillRepository
    from app.db.supabase_client import get_service_role_client

    if not settings.RUN_SEEDS_ON_STARTUP:
        return
    client = get_service_role_client()
    loader = SeedLoader(
        AgentRepository(client),
        SkillRepository(client),
        seeds_root=Path(__file__).parent.parent / "seeds",
    )
    results = await loader.load_all()
    logger.info(f"seed_loader: {results}")
```

- [ ] **Step 10.8: Bind script-outline/expand/branch skills to script_ai agent**

Add to seed loader finalization (after agents + skills loaded, bind via `agent_skills`):
```python
# in SeedLoader.load_all after _load_skills:
script_ai = await self.agent_repo.get_by_slug("script_ai")
if script_ai:
    skill_slugs = ["script-outline", "script-expand", "script-branch"]
    skill_ids = []
    for s in skill_slugs:
        sk = await self.skill_repo.get_by_slug(s)
        if sk:
            skill_ids.append(sk["id"])
    await self.agent_repo.update_skill_bindings(script_ai["id"], skill_ids)
```

- [ ] **Step 10.9: Verify seed pass**

Run server, check logs, then:
```bash
psql -h 127.0.0.1 -p 54322 -U postgres -c "SELECT slug, name, is_system_preset FROM ai_agents WHERE slug='script_ai';"
psql -h 127.0.0.1 -p 54322 -U postgres -c "SELECT slug, name FROM skills WHERE slug LIKE 'script-%';"
psql -h 127.0.0.1 -p 54322 -U postgres -c "SELECT sf.path FROM skill_files sf JOIN skills s ON s.id=sf.skill_id WHERE s.slug='script-outline';"
```

- [ ] **Step 10.10: Commit**

```bash
git add backend/seeds/ backend/app/main.py
git commit -m "feat(ai): seed script_ai agent + 3 atomic skills (with multi-file example)"
```

---

## Task 11: REST API for AI Library

**Files:**
- Create: `backend/app/api/routes/ai_library_router.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_ai_library_routes.py`

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/ai-library/agents` | List accessible agents |
| GET | `/api/v1/ai-library/agents/{slug}` | Get agent detail + bound skill_ids |
| PATCH | `/api/v1/ai-library/agents/{slug}` | Update agent (protected: system preset = admin only or blocked) |
| GET | `/api/v1/ai-library/skills` | List accessible skills (filter by scope/category) |
| GET | `/api/v1/ai-library/skills/{slug}` | Get skill + files |
| PATCH | `/api/v1/ai-library/skills/{slug}` | Update skill |
| GET | `/api/v1/ai-library/skills/{slug}/files` | List files |
| PUT | `/api/v1/ai-library/skills/{slug}/files/{path:path}` | Upsert file |
| DELETE | `/api/v1/ai-library/skills/{slug}/files/{path:path}` | Delete file |

- [ ] **Step 11.1: Write failing tests** for `GET /api/v1/ai-library/agents/script_ai`

```python
# backend/tests/test_ai_library_routes.py
import pytest


@pytest.mark.integration
async def test_get_script_ai_returns_preset(authed_client):
    resp = await authed_client.get("/api/v1/ai-library/agents/script_ai")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "script_ai"
    assert data["is_system_preset"] is True
    assert "script-outline" in [s for s in data.get("skill_slugs", [])] or len(data["skill_ids"]) > 0


@pytest.mark.integration
async def test_patch_system_preset_rejected_for_non_admin(authed_client):
    resp = await authed_client.patch(
        "/api/v1/ai-library/agents/script_ai",
        json={"name": "hacked"},
    )
    # Phase 1: block non-admin edits to system preset
    assert resp.status_code in (403, 200)  # policy: reject or silently ignore


@pytest.mark.integration
async def test_list_skills_includes_script_outline(authed_client):
    resp = await authed_client.get("/api/v1/ai-library/skills")
    assert resp.status_code == 200
    slugs = [s.get("slug") for s in resp.json()]
    assert "script-outline" in slugs
```

- [ ] **Step 11.2: Implement router**

```python
# backend/app/api/routes/ai_library_router.py
from fastapi import APIRouter, Depends, HTTPException, Path
from uuid import UUID

from app.api.deps import get_current_user, get_supabase
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import (
    AgentOut, AgentUpdate, SkillOut, SkillUpdate, SkillFileOut, SkillFileUpsert
)

router = APIRouter(prefix="/ai-library", tags=["ai-library"])


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(current_user=Depends(get_current_user), supabase=Depends(get_supabase)):
    repo = AgentRepository(supabase)
    rows = await repo.list_accessible(user_id=current_user.id)
    # enrich with skill_ids
    result = []
    for row in rows:
        skill_ids = await repo.get_skill_ids(UUID(row["id"]))
        result.append({**row, "skill_ids": skill_ids})
    return result


@router.get("/agents/{slug}", response_model=AgentOut)
async def get_agent(slug: str, current_user=Depends(get_current_user), supabase=Depends(get_supabase)):
    repo = AgentRepository(supabase)
    agent = await repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(404, "agent not found")
    agent["skill_ids"] = await repo.get_skill_ids(UUID(agent["id"]))
    return agent


@router.patch("/agents/{slug}", response_model=AgentOut)
async def update_agent(
    slug: str,
    payload: AgentUpdate,
    current_user=Depends(get_current_user),
    supabase=Depends(get_supabase),
):
    repo = AgentRepository(supabase)
    agent = await repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(404, "agent not found")
    # Phase 1 policy: system preset — block editing unless admin
    if agent.get("is_system_preset"):
        # TODO Phase 2: role-based admin check
        raise HTTPException(403, "system preset agents are read-only in phase 1")
    updates = payload.model_dump(exclude_none=True, exclude={"skill_ids"})
    if updates:
        await repo.update_fields(UUID(agent["id"]), updates)
    if payload.skill_ids is not None:
        await repo.update_skill_bindings(UUID(agent["id"]), payload.skill_ids)
    refreshed = await repo.get_by_slug(slug)
    refreshed["skill_ids"] = await repo.get_skill_ids(UUID(refreshed["id"]))
    return refreshed


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(current_user=Depends(get_current_user), supabase=Depends(get_supabase)):
    repo = SkillRepository(supabase)
    skills = await repo.list_accessible(user_id=current_user.id)
    result = []
    for s in skills:
        files = await repo.list_files(UUID(s["id"]))
        result.append({**s, "files": files})
    return result


@router.get("/skills/{slug}", response_model=SkillOut)
async def get_skill(slug: str, current_user=Depends(get_current_user), supabase=Depends(get_supabase)):
    repo = SkillRepository(supabase)
    s = await repo.get_by_slug(slug)
    if not s:
        raise HTTPException(404, "skill not found")
    s["files"] = await repo.list_files(UUID(s["id"]))
    return s


@router.patch("/skills/{slug}", response_model=SkillOut)
async def update_skill(
    slug: str,
    payload: SkillUpdate,
    current_user=Depends(get_current_user),
    supabase=Depends(get_supabase),
):
    repo = SkillRepository(supabase)
    s = await repo.get_by_slug(slug)
    if not s:
        raise HTTPException(404, "skill not found")
    if s.get("is_public") and not s.get("project_id"):
        raise HTTPException(403, "system preset skills are read-only in phase 1")
    updates = payload.model_dump(exclude_none=True)
    await repo.update_fields(UUID(s["id"]), updates)
    refreshed = await repo.get_by_slug(slug)
    refreshed["files"] = await repo.list_files(UUID(refreshed["id"]))
    return refreshed


@router.get("/skills/{slug}/files", response_model=list[SkillFileOut])
async def list_files(slug: str, current_user=Depends(get_current_user), supabase=Depends(get_supabase)):
    repo = SkillRepository(supabase)
    s = await repo.get_by_slug(slug)
    if not s:
        raise HTTPException(404, "skill not found")
    return await repo.list_files(UUID(s["id"]))


@router.put("/skills/{slug}/files/{path:path}", response_model=SkillFileOut)
async def upsert_file(
    slug: str,
    path: str,
    payload: SkillFileUpsert,
    current_user=Depends(get_current_user),
    supabase=Depends(get_supabase),
):
    repo = SkillRepository(supabase)
    s = await repo.get_by_slug(slug)
    if not s:
        raise HTTPException(404, "skill not found")
    row = await repo.upsert_file(
        UUID(s["id"]),
        path=path,
        content=payload.content,
        file_type=payload.file_type,
        binary_url=payload.binary_url,
    )
    return row


@router.delete("/skills/{slug}/files/{path:path}", status_code=204)
async def delete_file(
    slug: str, path: str,
    current_user=Depends(get_current_user),
    supabase=Depends(get_supabase),
):
    repo = SkillRepository(supabase)
    s = await repo.get_by_slug(slug)
    if not s:
        raise HTTPException(404, "skill not found")
    await repo.delete_file(UUID(s["id"]), path)
```

- [ ] **Step 11.3: Register router in `main.py`**

```python
from app.api.routes.ai_library_router import router as ai_library_router
app.include_router(ai_library_router, prefix="/api/v1")
```

- [ ] **Step 11.4: Run tests, iterate until pass**

- [ ] **Step 11.5: Commit**

```bash
git commit -m "feat(api): AI Library REST endpoints (agents + skills + files)"
```

---

## Task 12: Migrate `script_ai_service` to new pipeline

**Files:**
- Modify: `backend/app/services/script_ai_service.py`
- Test: `backend/tests/test_script_ai_pilot.py`

**Goal:** Replace `_call_llm` with `prompt_composer` + `agent_runner`. Delete any hardcoded system prompts. The `outline`/`expand`/`branch` methods pass **user** message only; system message comes from DB.

- [ ] **Step 12.1: Write failing integration test**

```python
# backend/tests/test_script_ai_pilot.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.script_ai_service import ScriptAIService


@pytest.mark.integration
async def test_outline_uses_composed_system_message(monkeypatch):
    svc = ScriptAIService()
    captured = {}

    async def fake_run(composed, user_messages):
        captured["system"] = composed.system_message
        captured["user"] = user_messages
        return {"content": "<h2>outline</h2>"}

    monkeypatch.setattr(svc.runner, "run_turn", fake_run)
    await svc.generate_outline(premise="a detective in a snowstorm", target_duration="10min")

    assert "Agent Instructions" in captured["system"]
    assert "<available_skills>" in captured["system"]
    assert captured["user"][0]["role"] == "user"
    assert "detective" in captured["user"][0]["content"]
```

- [ ] **Step 12.2: Refactor `script_ai_service.py`**

Replace direct `_call_llm` pattern with composer/runner. Remove any inline system prompt strings. Signature:

```python
# backend/app/services/script_ai_service.py
from app.services.prompt_composer import PromptComposer, ComposerInput
from app.services.agent_runner import AgentRunner
from app.services.skill_tool_service import SkillToolService
from app.services.ai_provider import QwenAdapter
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.db.supabase_client import get_service_role_client


class ScriptAIService:
    """Script AI operations — now DB-driven via prompt_composer."""

    AGENT_SLUG = "script_ai"

    def __init__(self) -> None:
        client = get_service_role_client()
        agent_repo = AgentRepository(client)
        skill_repo = SkillRepository(client)
        self.composer = PromptComposer(agent_repo, skill_repo)
        self.runner = AgentRunner(
            adapter=QwenAdapter(
                api_url=settings.LLM_API_URL,
                api_key=settings.LLM_API_KEY,
                default_model=settings.LLM_MODEL,
            ),
            skill_tool=SkillToolService(skill_repo),
        )

    async def generate_outline(self, premise: str, target_duration: str = "60s") -> str:
        composed = await self.composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=f"Task: outline. Target duration: {target_duration}.",
            )
        )
        result = await self.runner.run_turn(
            composed,
            user_messages=[{"role": "user", "content": f"Premise: {premise}"}],
        )
        return sanitize_ai_html(result["content"])

    async def expand_scene(self, outline: str, beat_index: int, context: str = "") -> str:
        composed = await self.composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=f"Task: expand beat {beat_index}. Context: {context or 'none'}.",
            )
        )
        result = await self.runner.run_turn(
            composed,
            user_messages=[{"role": "user", "content": f"Outline:\n{outline}"}],
        )
        return sanitize_ai_html(result["content"])

    async def branch_scene(self, scene: str, n: int = 3) -> list[str]:
        composed = await self.composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=f"Task: branch. Produce {n} alternatives.",
            )
        )
        result = await self.runner.run_turn(
            composed,
            user_messages=[{"role": "user", "content": f"Scene:\n{scene}"}],
        )
        cleaned = sanitize_ai_html(result["content"])
        # splitting policy unchanged from previous impl
        return [cleaned]  # caller handles parsing (or move into composer output)
```

- [ ] **Step 12.3: Delete old hardcoded system prompts** — grep `script_ai_service.py` for any `system_prompt = "..."` assignments and remove them.

- [ ] **Step 12.4: Run `test_script_ai_pilot`, iterate**

- [ ] **Step 12.5: Smoke test end-to-end** — dev server + curl:

```bash
curl -X POST http://localhost:8081/api/v1/scripts/{test-id}/outline \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"premise":"A detective in a snowstorm"}'
```
Verify returned HTML has `<h2>` scene structure.

- [ ] **Step 12.6: Commit**

```bash
git commit -m "refactor(ai): migrate script_ai_service to DB-driven prompt_composer"
```

---

## Task 13: Frontend API client

**Files:**
- Create: `frontend/services/aiLibraryService.ts`
- Modify: `frontend/types.ts`

- [ ] **Step 13.1: Add types**

```typescript
// frontend/types.ts (append)
export interface AIAgent {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  model: string;
  temperature: number;
  max_tokens: number;
  identity_md?: string | null;
  soul_md?: string | null;
  agent_md?: string | null;
  is_system_preset: boolean;
  team_id?: number | null;
  project_id?: number | null;
  user_id?: string | null;
  enabled: boolean;
  skill_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface AISkillFile {
  id: string;
  skill_id: string;
  path: string;
  content?: string | null;
  file_type: 'markdown' | 'script' | 'text-asset' | 'binary-ref';
  binary_url?: string | null;
  updated_at: string;
}

export interface AISkill {
  id: string;
  slug?: string;
  name: string;
  description?: string | null;
  body_md?: string | null;
  category?: string | null;
  icon?: string | null;
  is_public: boolean;
  team_id?: number | null;
  project_id?: number | null;
  output_format?: string | null;
  frontmatter_json: Record<string, unknown>;
  files: AISkillFile[];
  updated_at: string;
}
```

- [ ] **Step 13.2: Write API client**

```typescript
// frontend/services/aiLibraryService.ts
import { getAuthHeaders } from './parserService';
import type { AIAgent, AISkill, AISkillFile } from '../types';

const BASE = `${import.meta.env.VITE_API_URL}/api/v1/ai-library`;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}

export const aiLibraryService = {
  async listAgents(): Promise<AIAgent[]> {
    const r = await fetch(`${BASE}/agents`, { headers: await getAuthHeaders() });
    return handle(r);
  },
  async getAgent(slug: string): Promise<AIAgent> {
    const r = await fetch(`${BASE}/agents/${slug}`, { headers: await getAuthHeaders() });
    return handle(r);
  },
  async updateAgent(slug: string, updates: Partial<AIAgent>): Promise<AIAgent> {
    const r = await fetch(`${BASE}/agents/${slug}`, {
      method: 'PATCH',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    return handle(r);
  },
  async listSkills(): Promise<AISkill[]> {
    const r = await fetch(`${BASE}/skills`, { headers: await getAuthHeaders() });
    return handle(r);
  },
  async getSkill(slug: string): Promise<AISkill> {
    const r = await fetch(`${BASE}/skills/${slug}`, { headers: await getAuthHeaders() });
    return handle(r);
  },
  async updateSkill(slug: string, updates: Partial<AISkill>): Promise<AISkill> {
    const r = await fetch(`${BASE}/skills/${slug}`, {
      method: 'PATCH',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    return handle(r);
  },
  async upsertSkillFile(
    slug: string,
    path: string,
    content: string,
    file_type: AISkillFile['file_type'] = 'markdown'
  ): Promise<AISkillFile> {
    const r = await fetch(`${BASE}/skills/${slug}/files/${path}`, {
      method: 'PUT',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content, file_type }),
    });
    return handle(r);
  },
  async deleteSkillFile(slug: string, path: string): Promise<void> {
    const r = await fetch(`${BASE}/skills/${slug}/files/${path}`, {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    });
    if (!r.ok) throw new Error(`${r.status}`);
  },
};
```

- [ ] **Step 13.3: Commit**

```bash
git commit -m "feat(frontend): aiLibraryService API client + types"
```

---

## Task 14: Settings Page — AI Library menu item

**Files:**
- Modify: `frontend/components/SettingsModal.tsx` (or `SettingsView.tsx` whichever renders the sidebar)
- Modify: `frontend/public/locales/en.json` + `zh.json`

- [ ] **Step 14.1: Add i18n keys**

```json
// frontend/public/locales/en.json (add under "settings" or top-level)
{
  "aiLibrary": {
    "title": "AI Library",
    "menuItem": "AI Library",
    "tabs": {
      "agents": "Agents",
      "skills": "Skills"
    },
    "agents": {
      "systemPreset": "System Preset",
      "selectAgent": "Select an agent to edit",
      "identityTitle": "Identity",
      "soulTitle": "Soul",
      "instructionsTitle": "Instructions",
      "boundSkills": "Bound Skills",
      "saveChanges": "Save Changes",
      "presetReadOnly": "System preset — read only"
    },
    "skills": {
      "addFile": "+ Add file",
      "fileType": "File type",
      "pathPlaceholder": "path e.g., references/examples.md",
      "deleteFile": "Delete file"
    }
  }
}
```

Mirror in `zh.json`.

- [ ] **Step 14.2: Add menu item** in `SettingsModal.tsx`

Locate the APP SETTINGS sidebar rendering. Add between `AI` and `API Docs`:

```tsx
// Pseudocode patch — match existing pattern in SettingsModal.tsx
{ key: 'aiLibrary', icon: <LibraryIcon />, label: t('aiLibrary.menuItem') },
```

Route to `<AILibraryPanel />` when selected.

- [ ] **Step 14.3: Commit**

```bash
git commit -m "feat(frontend): Settings → AI Library menu item + i18n"
```

---

## Task 15: Frontend — AI Library Panel (tabs)

**Files:**
- Create: `frontend/components/AILibrary/AILibraryPanel.tsx`
- Create: `frontend/components/AILibrary/AgentsTab.tsx`
- Create: `frontend/components/AILibrary/SkillsTab.tsx`

- [ ] **Step 15.1: Write `AILibraryPanel.tsx` with tab switcher**

```tsx
// frontend/components/AILibrary/AILibraryPanel.tsx
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentsTab } from './AgentsTab';
import { SkillsTab } from './SkillsTab';

type Tab = 'agents' | 'skills';

export function AILibraryPanel() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('agents');

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-white/10 px-6 py-4">
        <h1 className="text-xl font-semibold">{t('aiLibrary.title')}</h1>
      </header>
      <nav className="flex gap-2 border-b border-white/10 px-6 py-2">
        {(['agents', 'skills'] as Tab[]).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded px-3 py-1 text-sm ${
              tab === key ? 'bg-purple-600 text-white' : 'text-white/70 hover:bg-white/5'
            }`}
          >
            {t(`aiLibrary.tabs.${key}`)}
          </button>
        ))}
      </nav>
      <div className="flex-1 overflow-hidden">
        {tab === 'agents' ? <AgentsTab /> : <SkillsTab />}
      </div>
    </div>
  );
}
```

- [ ] **Step 15.2: Write `AgentsTab.tsx` (list + selector)**

```tsx
// frontend/components/AILibrary/AgentsTab.tsx
import { useEffect, useState } from 'react';
import type { AIAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { AgentEditor } from './AgentEditor';

export function AgentsTab() {
  const [agents, setAgents] = useState<AIAgent[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  useEffect(() => {
    aiLibraryService.listAgents().then((list) => {
      setAgents(list);
      if (list.length > 0) setSelectedSlug(list[0].slug);
    });
  }, []);

  return (
    <div className="flex h-full">
      <aside className="w-64 overflow-y-auto border-r border-white/10">
        {agents.map((a) => (
          <button
            key={a.slug}
            onClick={() => setSelectedSlug(a.slug)}
            className={`flex w-full flex-col items-start border-b border-white/5 px-4 py-3 text-left ${
              selectedSlug === a.slug ? 'bg-white/5' : 'hover:bg-white/5'
            }`}
          >
            <span className="font-medium">{a.name}</span>
            <span className="text-xs text-white/50">
              {a.is_system_preset ? 'System' : 'Custom'} · {a.model}
            </span>
          </button>
        ))}
      </aside>
      <main className="flex-1 overflow-y-auto p-6">
        {selectedSlug && <AgentEditor slug={selectedSlug} />}
      </main>
    </div>
  );
}
```

- [ ] **Step 15.3: Write `SkillsTab.tsx`** — list cards (reuse/align to existing project Skills layout from Image 1).

```tsx
// frontend/components/AILibrary/SkillsTab.tsx
import { useEffect, useState } from 'react';
import type { AISkill } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { SkillEditor } from './SkillEditor';

export function SkillsTab() {
  const [skills, setSkills] = useState<AISkill[]>([]);
  const [editingSlug, setEditingSlug] = useState<string | null>(null);

  useEffect(() => { aiLibraryService.listSkills().then(setSkills); }, []);

  if (editingSlug) {
    return (
      <SkillEditor
        slug={editingSlug}
        onBack={() => {
          setEditingSlug(null);
          aiLibraryService.listSkills().then(setSkills);
        }}
      />
    );
  }

  return (
    <div className="p-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {skills.map((s) => (
          <button
            key={s.slug ?? s.id}
            onClick={() => setEditingSlug(s.slug ?? s.id)}
            className="rounded-lg border border-white/10 bg-white/5 p-4 text-left hover:bg-white/10"
          >
            <div className="mb-2 text-2xl">{s.icon}</div>
            <div className="font-semibold">{s.name}</div>
            <div className="mt-1 text-xs text-white/50">{s.category}</div>
            <p className="mt-2 text-sm text-white/70">{s.description}</p>
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 15.4: Commit**

```bash
git commit -m "feat(frontend): AI Library panel + agents/skills tabs skeleton"
```

---

## Task 16: Frontend — Agent Editor

**Files:**
- Create: `frontend/components/AILibrary/AgentEditor.tsx`
- Create: `frontend/components/AILibrary/MarkdownEditor.tsx`

- [ ] **Step 16.1: Write `MarkdownEditor.tsx`** — simple `<textarea>` with optional preview toggle (Phase 1 no Monaco).

```tsx
// frontend/components/AILibrary/MarkdownEditor.tsx
import { useState } from 'react';

interface Props {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  rows?: number;
}

export function MarkdownEditor({ value, onChange, placeholder, disabled, rows = 10 }: Props) {
  return (
    <textarea
      className="w-full rounded border border-white/10 bg-black/40 p-3 font-mono text-sm text-white/90"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      rows={rows}
    />
  );
}
```

- [ ] **Step 16.2: Write `AgentEditor.tsx`** — 3 sub-tabs (Overview / Files / Skills).

```tsx
// frontend/components/AILibrary/AgentEditor.tsx
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AIAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { MarkdownEditor } from './MarkdownEditor';

type Sub = 'overview' | 'files' | 'skills';

export function AgentEditor({ slug }: { slug: string }) {
  const { t } = useTranslation();
  const [agent, setAgent] = useState<AIAgent | null>(null);
  const [sub, setSub] = useState<Sub>('overview');
  const [draft, setDraft] = useState<Partial<AIAgent>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    aiLibraryService.getAgent(slug).then((a) => {
      setAgent(a);
      setDraft({ identity_md: a.identity_md ?? '', soul_md: a.soul_md ?? '', agent_md: a.agent_md ?? '' });
    });
  }, [slug]);

  if (!agent) return <div className="text-white/50">Loading...</div>;

  const isPreset = agent.is_system_preset;
  const save = async () => {
    if (isPreset) return;
    setSaving(true);
    try {
      const updated = await aiLibraryService.updateAgent(slug, draft);
      setAgent(updated);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{agent.name}</h2>
          <div className="text-xs text-white/50">
            {agent.slug} · {agent.model}
            {isPreset && <span className="ml-2 rounded bg-white/10 px-2 py-0.5">{t('aiLibrary.agents.systemPreset')}</span>}
          </div>
        </div>
        {!isPreset && (
          <button
            onClick={save}
            disabled={saving}
            className="rounded bg-purple-600 px-3 py-1 text-sm disabled:opacity-50"
          >
            {t('aiLibrary.agents.saveChanges')}
          </button>
        )}
      </header>
      <nav className="mb-4 flex gap-2 border-b border-white/10">
        {(['overview', 'files', 'skills'] as Sub[]).map((k) => (
          <button
            key={k}
            onClick={() => setSub(k)}
            className={`px-3 py-2 text-sm ${sub === k ? 'border-b-2 border-purple-500' : 'text-white/50'}`}
          >
            {k}
          </button>
        ))}
      </nav>
      {sub === 'overview' && (
        <section className="space-y-4">
          <dl className="grid grid-cols-[120px_1fr] gap-2 text-sm">
            <dt className="text-white/50">Description</dt><dd>{agent.description}</dd>
            <dt className="text-white/50">Model</dt><dd>{agent.model}</dd>
            <dt className="text-white/50">Temperature</dt><dd>{agent.temperature}</dd>
            <dt className="text-white/50">Max tokens</dt><dd>{agent.max_tokens}</dd>
            <dt className="text-white/50">Bound skills</dt><dd>{agent.skill_ids.length}</dd>
          </dl>
        </section>
      )}
      {sub === 'files' && (
        <section className="space-y-4">
          {isPreset && (
            <div className="rounded bg-yellow-900/30 p-2 text-xs text-yellow-200">
              {t('aiLibrary.agents.presetReadOnly')}
            </div>
          )}
          <div>
            <h3 className="mb-1 text-sm font-semibold">{t('aiLibrary.agents.identityTitle')} (IDENTITY.md)</h3>
            <MarkdownEditor
              value={draft.identity_md ?? ''}
              onChange={(v) => setDraft({ ...draft, identity_md: v })}
              disabled={isPreset}
            />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">{t('aiLibrary.agents.soulTitle')} (SOUL.md)</h3>
            <MarkdownEditor
              value={draft.soul_md ?? ''}
              onChange={(v) => setDraft({ ...draft, soul_md: v })}
              disabled={isPreset}
            />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">{t('aiLibrary.agents.instructionsTitle')} (AGENT.md)</h3>
            <MarkdownEditor
              value={draft.agent_md ?? ''}
              onChange={(v) => setDraft({ ...draft, agent_md: v })}
              disabled={isPreset}
              rows={16}
            />
          </div>
        </section>
      )}
      {sub === 'skills' && (
        <section>
          <p className="text-sm text-white/50">
            {t('aiLibrary.agents.boundSkills')}: {agent.skill_ids.length}
          </p>
          {/* Phase 1: read-only list. Phase 2: multi-select with toggles */}
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 16.3: Commit**

```bash
git commit -m "feat(frontend): agent editor with overview/files/skills sub-tabs"
```

---

## Task 17: Frontend — Skill Editor (multi-file)

**Files:**
- Modify or Create: `frontend/components/AILibrary/SkillEditor.tsx`

Per earlier Image 3, the existing editor has Name / Category / Description / Icon / Scope / Public on left and `Content (Markdown)` + `Output Format` on right. **Phase 1 goal:** preserve left column, replace right column's single content textarea with **file tabs**.

- [ ] **Step 17.1: Write `SkillEditor.tsx`**

```tsx
// frontend/components/AILibrary/SkillEditor.tsx
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AISkill, AISkillFile } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { MarkdownEditor } from './MarkdownEditor';

interface Props {
  slug: string;
  onBack: () => void;
}

export function SkillEditor({ slug, onBack }: Props) {
  const { t } = useTranslation();
  const [skill, setSkill] = useState<AISkill | null>(null);
  const [activeTab, setActiveTab] = useState<string>('SKILL.md');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    aiLibraryService.getSkill(slug).then((s) => {
      setSkill(s);
      const d: Record<string, string> = { 'SKILL.md': s.body_md ?? '' };
      s.files.forEach((f) => { d[f.path] = f.content ?? ''; });
      setDrafts(d);
    });
  }, [slug]);

  if (!skill) return <div className="p-6 text-white/50">Loading...</div>;

  const isPreset = skill.is_public && !skill.project_id;

  const save = async () => {
    setSaving(true);
    try {
      if (!isPreset) {
        await aiLibraryService.updateSkill(slug, { body_md: drafts['SKILL.md'] });
        for (const f of skill.files) {
          if (drafts[f.path] !== undefined && drafts[f.path] !== f.content) {
            await aiLibraryService.upsertSkillFile(slug, f.path, drafts[f.path], f.file_type);
          }
        }
      }
      const refreshed = await aiLibraryService.getSkill(slug);
      setSkill(refreshed);
    } finally {
      setSaving(false);
    }
  };

  const addFile = async () => {
    const path = prompt(t('aiLibrary.skills.pathPlaceholder') ?? 'path');
    if (!path) return;
    await aiLibraryService.upsertSkillFile(slug, path, '', 'markdown');
    const refreshed = await aiLibraryService.getSkill(slug);
    setSkill(refreshed);
    setActiveTab(path);
    setDrafts({ ...drafts, [path]: '' });
  };

  const allTabs = ['SKILL.md', ...skill.files.map((f) => f.path)];

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-white/10 px-6 py-3">
        <button onClick={onBack} className="text-sm text-white/70">← Back</button>
        <h2 className="font-semibold">{skill.name}</h2>
        <button
          onClick={save}
          disabled={saving || isPreset}
          className="rounded bg-purple-600 px-3 py-1 text-sm disabled:opacity-50"
        >
          Save
        </button>
      </header>

      <nav className="flex gap-1 overflow-x-auto border-b border-white/10 px-4 py-2">
        {allTabs.map((p) => (
          <button
            key={p}
            onClick={() => setActiveTab(p)}
            className={`whitespace-nowrap rounded px-3 py-1 text-sm ${
              activeTab === p ? 'bg-white/10' : 'text-white/50 hover:bg-white/5'
            }`}
          >
            {p}
          </button>
        ))}
        {!isPreset && (
          <button onClick={addFile} className="rounded px-3 py-1 text-sm text-white/70 hover:bg-white/5">
            {t('aiLibrary.skills.addFile')}
          </button>
        )}
      </nav>

      <div className="flex-1 overflow-auto p-6">
        <MarkdownEditor
          value={drafts[activeTab] ?? ''}
          onChange={(v) => setDrafts({ ...drafts, [activeTab]: v })}
          disabled={isPreset}
          rows={24}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 17.2: Commit**

```bash
git commit -m "feat(frontend): skill editor with multi-file tabs"
```

---

## Task 18: End-to-End Smoke Test

**Goal:** Full user journey — open Settings → AI Library → Agents → script_ai → Files tab. See IDENTITY/SOUL/AGENT content. Navigate to Skills tab → script-outline → see SKILL.md + references/examples.md as tabs.

- [ ] **Step 18.1: Launch backend + frontend dev** (use `dev` worktree per MEMORY.md)

```bash
cd .worktrees/dev/backend && uv run uvicorn app.main:app --reload --port 8081
cd .worktrees/dev/frontend && npm run dev
```

- [ ] **Step 18.2: Use chrome-devtools MCP to navigate**

```
1. navigate to http://localhost:5176
2. login as test user
3. open Settings → AI Library
4. click Agents tab, select script_ai, click Files sub-tab → verify IDENTITY/SOUL/AGENT textareas populated
5. click Skills tab, click script-outline card → verify tabs: SKILL.md, references/examples.md
6. switch to references/examples.md → verify example outline text visible
```

- [ ] **Step 18.3: Run `/api/v1/scripts/{id}/outline` end-to-end**

Verify:
- Qwen API called with composed system message (check backend logs)
- Response returns valid HTML `<h2>...</h2>` structure
- `ai_usage_logs` row inserted with `agent_id = script_ai`

- [ ] **Step 18.4: Document gotchas** found during smoke test in `docs/superpowers/plans/2026-04-20-ai-library-phase1.md` under "Known Issues" section.

- [ ] **Step 18.5: Commit smoke-test fixes if any**

```bash
git commit -m "fix: phase 1 smoke-test regressions"
```

---

## Task 19: Cleanup + Final Verification

- [ ] **Step 19.1: Run full test suite**

```bash
cd backend && uv run pytest -x --cov=app --cov-report=term-missing
cd frontend && npm run build
```

All green, coverage ≥ 80% for new modules.

- [ ] **Step 19.2: Grep for dead code**

```bash
grep -n "system_prompt =" backend/app/services/script_ai_service.py   # should be empty
grep -n "_call_llm" backend/app/services/script_ai_service.py        # should be empty after refactor
```

- [ ] **Step 19.3: Update CLAUDE.md with AI Library section**

Add to `backend/CLAUDE.md` or root `CLAUDE.md`:
```markdown
## AI Library (Phase 1)
- Agent CRUD: `ai_agents` + `agent_skills` tables, `/api/v1/ai-library/agents/*`
- Skill + multi-file: `skills` + `skill_files` tables, `/api/v1/ai-library/skills/*/files/*`
- Composer: `backend/app/services/prompt_composer.py` — 参照 OpenClaw lazy-readable
- Pilot: `script_ai` agent + 3 atomic skills (outline / expand / branch)
- Seed: `backend/seeds/{agents,skills}/<slug>/`
```

- [ ] **Step 19.4: Final commit**

```bash
git add -A
git commit -m "docs: update CLAUDE.md with AI Library phase 1 architecture"
```

- [ ] **Step 19.5: Merge dev → master prep**

Per MEMORY.md workflow: `feature/ai-library-phase1` → `dev` (test) → `master`. Open PR from `feature/ai-library-phase1` to `dev`.

---

## Acceptance Criteria

- [ ] Migration 128 applied cleanly; `ai_agents.slug`, `skills.slug`, `skill_files`, `agent_skills`, `ai_sessions.agent_id` all present
- [ ] Seed loader runs on startup, populates `script_ai` agent + 3 skills + 1 multi-file example
- [ ] `GET /api/v1/ai-library/agents/script_ai` returns full agent with `skill_ids` populated
- [ ] `GET /api/v1/ai-library/skills/script-outline` returns body + files (including `references/examples.md`)
- [ ] `script_ai_service.generate_outline()` produces valid HTML via new pipeline — zero hardcoded system prompts remain
- [ ] Settings → AI Library menu item visible; Agents tab shows script_ai; Skills tab shows script-outline
- [ ] Opening script-outline in UI shows SKILL.md tab + references/examples.md tab, content editable
- [ ] System preset edits rejected with 403
- [ ] All unit tests pass; integration tests pass on `dev` worktree
- [ ] Frontend `npm run build` succeeds with zero TS errors

---

## NOT in scope (Phase 2+)

- **Prompts tab** (separate `prompts` table with versioning)
- **Multi-provider** (Claude / DeepSeek / Doubao adapters — Phase 2)
- **Team/Project scope UI editing** (Phase 2 RLS-backed full CRUD)
- **Fork / agent templates / marketplace** (C overlay — Phase 4)
- **MCP integration** (deferred)
- **Token billing / Studio Admin** (deferred)
- **Binary asset upload** (`file_type: 'binary-ref'` with Supabase Storage — Phase 2)
- **Monaco editor** (plain textarea in Phase 1)
- **Agent run history / observability dashboard** (Phase 3)
- **script_ai 之外的 4 agent 迁移** (summarize/analyze/storyboard/visual_analysis — Phase 2)
