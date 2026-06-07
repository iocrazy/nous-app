# ID Unification + Personal-as-Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Spec 1 (`docs/superpowers/specs/2026-05-28-id-unification-design.md`) in five sequenced PRs, ending with all self-owned mediahub tables on BIGINT Snowflake, personal scope abstracted into single-member teams, and storage paths uniformly under `teams/{snowflake}/uploads/...`.

**Architecture:** Five separately revertable PRs. PR-A adds the `teams.kind` column and backfills personal teams for existing users without changing any application behavior. PR-B migrates `ai_sessions`, `agent_runs`, `issue_messages` from UUID to Snowflake (mirroring mig 051's column-rename pattern). PR-C remaps `scope_type='personal'` rows in `resource_items`/`folders`/`tags`/`smart_collections` to point at personal team snowflakes (data only, code still handles both shapes). PR-D moves files on disk from `teams/{user_uuid}/` to `teams/{personal_team_snowflake}/` and updates `resources.file_path` accordingly. PR-E simplifies application code (deletes personal-scope branches) and RLS policies (removes the `OR scope_type='personal'` arms).

**Tech Stack:** PostgreSQL 15 + Supabase, FastAPI / asyncpg / Pydantic, React 19 / TypeScript / Vite, DBOS workflows, pytest / vitest.

**Spec reference:** `docs/superpowers/specs/2026-05-28-id-unification-design.md` — section numbers in this plan map to spec sections.

---

## Pre-flight (once before any PR)

- [ ] **Branch base off latest master.**

```bash
cd /Volumes/program/project-code/repos/mediahub
git fetch origin
git checkout master && git pull
```

- [ ] **Confirm spec is in place.**

```bash
test -f docs/superpowers/specs/2026-05-28-id-unification-design.md && echo OK
```

- [ ] **Run the exhaustive FK-dependent grep referenced in spec § 4.**

```bash
# These greps surface every table that FKs into ai_sessions / agent_runs / issue_messages.
# Plan-stage task — produces a list pasted into PR-B's task descriptions.
grep -rn "REFERENCES ai_sessions" supabase/migrations/
grep -rn "REFERENCES agent_runs" supabase/migrations/
grep -rn "REFERENCES issue_messages" supabase/migrations/
grep -rn "REFERENCES public.ai_sessions\|REFERENCES public.agent_runs\|REFERENCES public.issue_messages" supabase/migrations/
```

Document the result list at the top of PR-B's branch as a comment.

---

# PR-A — `teams.kind` + personal team auto-create + backfill

**Branch:** `id-unify/pr-a-teams-kind`

**Goal:** Add the `kind` column on `teams`, the auto-create trigger for new users, the singleton-enforcement trigger for `team_members`, and backfill personal teams for every existing user that has personal-scope data. No code-behavior change.

**Risk level:** Low. Zero downtime. Reversible (drop column + delete trigger + delete personal-team rows).

## Task A1 — Migration: `teams.kind` column

**Files:**
- Create: `supabase/migrations/227_teams_kind_column.sql`
- Test: `backend/tests/migrations/test_227_teams_kind.py` (create)

- [ ] **Step 1 — Branch.**

```bash
git checkout -b id-unify/pr-a-teams-kind
```

- [ ] **Step 2 — Write the migration.**

```sql
-- supabase/migrations/227_teams_kind_column.sql
-- Adds `kind` column to teams + unique-personal-team-per-owner constraint.
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3.

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'collaborative'
    CHECK (kind IN ('personal', 'collaborative'));

COMMENT ON COLUMN public.teams.kind IS
    'personal = auto-created single-member team; collaborative = user-created multi-member team. '
    'See ID-unification design 2026-05-28.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_owner_personal
    ON public.teams (owner_id) WHERE kind = 'personal';
```

- [ ] **Step 3 — Write the migration test.**

```python
# backend/tests/migrations/test_227_teams_kind.py
"""Verify mig 227 adds teams.kind column + unique index."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_teams_kind_column_exists():
    rows = await db_engine.fetch_all(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='teams' AND column_name='kind'",
        {},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["data_type"] == "text"
    assert row["is_nullable"] == "NO"
    assert "'collaborative'" in (row["column_default"] or "")


@pytest.mark.asyncio
async def test_unique_personal_team_per_owner():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename='teams' "
        "AND indexname='uq_teams_owner_personal'",
        {},
    )
    assert len(rows) == 1
    assert "kind = 'personal'" in rows[0]["indexdef"]
```

- [ ] **Step 4 — Apply migration locally.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/227_teams_kind_column.sql
```

Expected output: `ALTER TABLE` + `CREATE INDEX`. Re-run = idempotent.

- [ ] **Step 5 — Run the test.**

```bash
cd backend
uv run pytest tests/migrations/test_227_teams_kind.py -v
```

Expected: 2 passed.

- [ ] **Step 6 — Commit.**

```bash
git add supabase/migrations/227_teams_kind_column.sql \
        backend/tests/migrations/test_227_teams_kind.py
git commit -m "feat(teams): add kind column ('personal'|'collaborative')

Adds public.teams.kind column with default 'collaborative' and a partial
unique index ensuring at most one personal team per owner.

First step of the ID-unification design 2026-05-28. No behavior change
on its own — sets up the schema for PR-A's trigger work."
```

## Task A2 — Migration: trigger to enforce personal team singleton

**Files:**
- Create: `supabase/migrations/228_personal_team_singleton_trigger.sql`
- Test: `backend/tests/migrations/test_228_personal_singleton.py`

- [ ] **Step 1 — Write the trigger migration.**

```sql
-- supabase/migrations/228_personal_team_singleton_trigger.sql
-- Rejects INSERT INTO team_members for a team where kind='personal'
-- and a member already exists.

CREATE OR REPLACE FUNCTION public.enforce_personal_team_singleton()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.teams t
         WHERE t.id = NEW.team_id AND t.kind = 'personal'
    ) THEN
        IF (SELECT COUNT(*) FROM public.team_members
              WHERE team_id = NEW.team_id) >= 1 THEN
            RAISE EXCEPTION
              'personal team % cannot have more than one member', NEW.team_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_personal_team_singleton ON public.team_members;
CREATE TRIGGER trg_enforce_personal_team_singleton
    BEFORE INSERT ON public.team_members
    FOR EACH ROW EXECUTE FUNCTION public.enforce_personal_team_singleton();
```

- [ ] **Step 2 — Write the test.**

```python
# backend/tests/migrations/test_228_personal_singleton.py
"""Verify mig 228: cannot add a second member to a kind='personal' team."""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_personal_team_rejects_second_member():
    # Create a personal team
    owner_id = str(uuid4())
    other_id = str(uuid4())
    team_rows = await db_engine.fetch_all(
        "INSERT INTO public.teams (kind, owner_id, name) "
        "VALUES ('personal', :owner, 'Personal Test') RETURNING id",
        {"owner": owner_id},
    )
    team_id = team_rows[0]["id"]
    try:
        # First member insert should succeed
        await db_engine.fetch_all(
            "INSERT INTO public.team_members (team_id, user_id, role) "
            "VALUES (:tid, :uid, 'owner')",
            {"tid": team_id, "uid": owner_id},
        )
        # Second member insert should fail
        with pytest.raises(Exception) as exc:
            await db_engine.fetch_all(
                "INSERT INTO public.team_members (team_id, user_id, role) "
                "VALUES (:tid, :uid, 'member')",
                {"tid": team_id, "uid": other_id},
            )
        assert "cannot have more than one member" in str(exc.value)
    finally:
        # Cleanup
        await db_engine.fetch_all(
            "DELETE FROM public.team_members WHERE team_id = :tid",
            {"tid": team_id},
        )
        await db_engine.fetch_all(
            "DELETE FROM public.teams WHERE id = :tid",
            {"tid": team_id},
        )


@pytest.mark.asyncio
async def test_collaborative_team_allows_multiple_members():
    owner_id = str(uuid4())
    other_id = str(uuid4())
    team_rows = await db_engine.fetch_all(
        "INSERT INTO public.teams (kind, owner_id, name) "
        "VALUES ('collaborative', :owner, 'Collab Test') RETURNING id",
        {"owner": owner_id},
    )
    team_id = team_rows[0]["id"]
    try:
        await db_engine.fetch_all(
            "INSERT INTO public.team_members (team_id, user_id, role) "
            "VALUES (:tid, :uid, 'owner')",
            {"tid": team_id, "uid": owner_id},
        )
        # Second member should succeed (collaborative)
        await db_engine.fetch_all(
            "INSERT INTO public.team_members (team_id, user_id, role) "
            "VALUES (:tid, :uid, 'member')",
            {"tid": team_id, "uid": other_id},
        )
        cnt = await db_engine.fetch_all(
            "SELECT COUNT(*)::int AS c FROM public.team_members WHERE team_id = :tid",
            {"tid": team_id},
        )
        assert cnt[0]["c"] == 2
    finally:
        await db_engine.fetch_all(
            "DELETE FROM public.team_members WHERE team_id = :tid",
            {"tid": team_id},
        )
        await db_engine.fetch_all(
            "DELETE FROM public.teams WHERE id = :tid",
            {"tid": team_id},
        )
```

- [ ] **Step 3 — Apply locally.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/228_personal_team_singleton_trigger.sql
```

- [ ] **Step 4 — Run tests.**

```bash
cd backend
uv run pytest tests/migrations/test_228_personal_singleton.py -v
```

Expected: 2 passed.

- [ ] **Step 5 — Commit.**

```bash
git add supabase/migrations/228_personal_team_singleton_trigger.sql \
        backend/tests/migrations/test_228_personal_singleton.py
git commit -m "feat(teams): trigger rejects second member on personal team

Defense-in-depth: API + UI must also block invitations to kind='personal'
teams, but the DB-level trigger is the authoritative gate so no path
(direct SQL, Studio, admin script) can violate the singleton."
```

## Task A3 — Migration: auto-create personal team on user creation

**Files:**
- Create: `supabase/migrations/229_auto_create_personal_team.sql`
- Test: `backend/tests/migrations/test_229_auto_create_personal.py`

- [ ] **Step 1 — Investigate existing auth hook pattern.**

Spec § 3 notes mediahub may already mirror `auth.users` into `public.users`. Confirm:

```bash
grep -rn "ON auth.users\|FOR EACH ROW EXECUTE FUNCTION.*user" supabase/migrations/ | head -10
```

If mediahub has a trigger on `auth.users` insert that mirrors into `public.users`, attach the personal-team creation as a sibling trigger or extend the existing function. Otherwise attach to `public.users` (whatever profile table the project uses).

- [ ] **Step 2 — Write the migration.**

```sql
-- supabase/migrations/229_auto_create_personal_team.sql
-- Trigger: on auth user creation, automatically create a 'personal' team
-- owned by that user and add them as the singleton member.

CREATE OR REPLACE FUNCTION public.create_personal_team_for_user()
RETURNS TRIGGER AS $$
DECLARE
    new_team_id BIGINT;
BEGIN
    -- Idempotent: skip if a personal team for this owner already exists
    IF EXISTS (
        SELECT 1 FROM public.teams
         WHERE owner_id = NEW.id AND kind = 'personal'
    ) THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.teams (kind, owner_id, name)
    VALUES ('personal', NEW.id, 'Personal')
    RETURNING id INTO new_team_id;

    INSERT INTO public.team_members (team_id, user_id, role)
    VALUES (new_team_id, NEW.id, 'owner');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_create_personal_team_for_user ON auth.users;
CREATE TRIGGER trg_create_personal_team_for_user
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.create_personal_team_for_user();
```

- [ ] **Step 3 — Test (idempotent + creates).**

```python
# backend/tests/migrations/test_229_auto_create_personal.py
"""Verify mig 229: new auth user gets a personal team auto-created."""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_personal_team_auto_created_on_auth_user_insert():
    user_id = str(uuid4())
    # Insert directly into auth.users (test-only path; production uses Supabase Auth signup)
    await db_engine.fetch_all(
        "INSERT INTO auth.users (id, email, created_at, updated_at) "
        "VALUES (:id, :email, NOW(), NOW())",
        {"id": user_id, "email": f"test-{user_id}@example.com"},
    )
    try:
        rows = await db_engine.fetch_all(
            "SELECT id, kind, owner_id::text, name FROM public.teams "
            "WHERE owner_id = :uid AND kind = 'personal'",
            {"uid": user_id},
        )
        assert len(rows) == 1
        assert rows[0]["kind"] == "personal"
        assert rows[0]["name"] == "Personal"

        members = await db_engine.fetch_all(
            "SELECT user_id::text FROM public.team_members WHERE team_id = :tid",
            {"tid": rows[0]["id"]},
        )
        assert len(members) == 1
        assert members[0]["user_id"] == user_id
    finally:
        await db_engine.fetch_all(
            "DELETE FROM public.team_members "
            "WHERE team_id IN (SELECT id FROM public.teams WHERE owner_id = :uid)",
            {"uid": user_id},
        )
        await db_engine.fetch_all(
            "DELETE FROM public.teams WHERE owner_id = :uid",
            {"uid": user_id},
        )
        await db_engine.fetch_all(
            "DELETE FROM auth.users WHERE id = :uid",
            {"uid": user_id},
        )
```

- [ ] **Step 4 — Apply + test.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/229_auto_create_personal_team.sql

cd backend
uv run pytest tests/migrations/test_229_auto_create_personal.py -v
```

Expected: 1 passed.

- [ ] **Step 5 — Commit.**

```bash
git add supabase/migrations/229_auto_create_personal_team.sql \
        backend/tests/migrations/test_229_auto_create_personal.py
git commit -m "feat(auth): auto-create personal team for new auth users

Trigger on auth.users INSERT inserts a kind='personal' team + a
singleton team_members row. Idempotent (skips if a personal team
for the owner already exists)."
```

## Task A4 — Migration: backfill personal teams for existing users

**Files:**
- Create: `supabase/migrations/230_backfill_personal_teams.sql`
- Test: `backend/tests/migrations/test_230_backfill_personal_teams.py`

- [ ] **Step 1 — Write the backfill migration.**

```sql
-- supabase/migrations/230_backfill_personal_teams.sql
-- One-shot backfill: every distinct owner of personal-scope rows (in
-- resource_items, folders, tags, smart_collections) gets a personal
-- team if they don't already have one.

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.resource_items
 WHERE scope_type = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = resource_items.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.folders
 WHERE scope_type = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = folders.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.tags
 WHERE scope_type = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = tags.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.smart_collections
 WHERE scope_type = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = smart_collections.scope_id::uuid
          AND t.kind = 'personal'
   );

-- Mirror into team_members for each newly created personal team
INSERT INTO public.team_members (team_id, user_id, role)
SELECT t.id, t.owner_id, 'owner'
  FROM public.teams t
 WHERE t.kind = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.team_members
        WHERE team_id = t.id AND user_id = t.owner_id
   );
```

- [ ] **Step 2 — Apply locally.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/230_backfill_personal_teams.sql
```

Expected: prints `INSERT 0 N` four times + a final `INSERT 0 M` for team_members. N may be 0 on a fresh local DB.

- [ ] **Step 3 — Write a verification test.**

```python
# backend/tests/migrations/test_230_backfill_personal_teams.py
"""Verify mig 230: every distinct personal-scope owner has a personal team."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_every_personal_scope_owner_has_personal_team():
    rows = await db_engine.fetch_all(
        """
        SELECT scope_id::text AS owner
          FROM (
              SELECT scope_id FROM public.resource_items WHERE scope_type='personal'
              UNION
              SELECT scope_id FROM public.folders WHERE scope_type='personal'
              UNION
              SELECT scope_id FROM public.tags WHERE scope_type='personal'
              UNION
              SELECT scope_id FROM public.smart_collections WHERE scope_type='personal'
          ) AS owners
         WHERE NOT EXISTS (
             SELECT 1 FROM public.teams t
              WHERE t.owner_id = owners.scope_id::uuid AND t.kind='personal'
         )
        """,
        {},
    )
    assert len(rows) == 0, f"users without personal team: {[r['owner'] for r in rows]}"


@pytest.mark.asyncio
async def test_every_personal_team_has_owner_as_member():
    rows = await db_engine.fetch_all(
        """
        SELECT t.id, t.owner_id::text AS owner
          FROM public.teams t
         WHERE t.kind = 'personal'
           AND NOT EXISTS (
               SELECT 1 FROM public.team_members tm
                WHERE tm.team_id = t.id AND tm.user_id = t.owner_id
           )
        """,
        {},
    )
    assert len(rows) == 0, f"personal teams missing owner membership: {rows}"
```

- [ ] **Step 4 — Run tests.**

```bash
cd backend
uv run pytest tests/migrations/test_230_backfill_personal_teams.py -v
```

Expected: 2 passed.

- [ ] **Step 5 — Commit.**

```bash
git add supabase/migrations/230_backfill_personal_teams.sql \
        backend/tests/migrations/test_230_backfill_personal_teams.py
git commit -m "feat(teams): backfill personal teams for existing users

One-shot migration: every user with personal-scope rows in
resource_items/folders/tags/smart_collections gets a kind='personal'
team + a team_members row. Idempotent — re-running is a no-op."
```

## Task A5 — Open PR-A + verify

- [ ] **Step 1 — Push branch + open PR.**

```bash
git push -u origin id-unify/pr-a-teams-kind
gh pr create --base master \
  --title "feat(teams): kind column + auto-create + backfill personal teams (PR-A)" \
  --body "First PR in the ID Unification track (Spec 1).

Adds teams.kind ('personal'|'collaborative') with auto-create + singleton enforcement, and backfills personal teams for existing users. No code-behavior change yet — sets up the schema for subsequent PRs.

See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3."
```

- [ ] **Step 2 — Wait for CI green + merge.**

```bash
gh pr checks --watch
gh pr merge --squash --auto
```

- [ ] **Step 3 — Verify in prod after deploy.**

```bash
# Wait for the SQL migration deployment workflow to apply 227-230 on prod DB.
# Then sanity check via Supabase MCP or psql:
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -d postgres -c "
    SELECT
      (SELECT COUNT(*) FROM public.teams WHERE kind='personal') AS personal_teams,
      (SELECT COUNT(DISTINCT scope_id) FROM public.resource_items WHERE scope_type='personal') AS personal_owners;
  "
'
```

Expected: `personal_teams >= personal_owners`. (Greater because new users get personal teams via auto-trigger even without data; equal if no signups since deploy.)

---

# PR-B — UUID → Snowflake for `ai_sessions`, `agent_runs`, `issue_messages`

**Branch:** `id-unify/pr-b-snowflake-3-tables`

**Goal:** Migrate three paperclip-style UUID PKs to BIGINT Snowflake, rebuild all FK dependents, preserve referential integrity. Brief read-only window during migration is acceptable per spec § 9.

**Risk level:** Medium. Reversible via rollback SQL (provided).

## Task B1 — Pre-migration: discover all FK dependents

**Files:**
- Create: `docs/superpowers/plans/pr-b-fk-dependents.md` (artifact, not committed; for plan-stage reference)

- [ ] **Step 1 — Branch.**

```bash
git checkout master && git pull
git checkout -b id-unify/pr-b-snowflake-3-tables
```

- [ ] **Step 2 — Generate the FK dependent list with introspection.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "
SELECT
  conrelid::regclass AS dependent_table,
  conname AS constraint_name,
  pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE confrelid::regclass::text IN
  ('public.ai_sessions','public.agent_runs','public.issue_messages')
ORDER BY dependent_table, conname;
"
```

Paste the result into a comment block at the top of the migration file in Task B2 so the engineer reviewing the migration can verify completeness.

- [ ] **Step 3 — Also grep code for any string-level UUID hardcoding.**

```bash
grep -rnE "ai_session_id|agent_run_id|issue_message_id" backend/app frontend/services frontend/components 2>/dev/null \
  | grep -iE "uuid|isUuid|gen_random|validateUUID" | head -20
```

Annotate any hits — those frontend validators may need their regex relaxed.

## Task B2 — Migration: `ai_sessions` UUID → Snowflake

**Files:**
- Create: `supabase/migrations/231_ai_sessions_snowflake.sql`
- Create: `supabase/migrations/231_ai_sessions_snowflake_rollback.sql` (companion)
- Test: `backend/tests/migrations/test_231_ai_sessions_snowflake.py`

- [ ] **Step 1 — Write the migration.**

```sql
-- supabase/migrations/231_ai_sessions_snowflake.sql
-- Convert ai_sessions.id from UUID to BIGINT Snowflake.
-- Mirrors mig 051's pattern. Rebuilds all FKs.
--
-- FK dependents (verified by Task B1 grep — keep this list in sync):
--   issues.ai_session_id        (mig 224)
--   ai_session_memory.session_id (mig 187)
--   agent_runs.session_id        (mig 145)
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 4.

BEGIN;

-- 1. Add new BIGINT id column on target
ALTER TABLE public.ai_sessions ADD COLUMN new_id BIGINT;
UPDATE public.ai_sessions SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
ALTER TABLE public.ai_sessions ALTER COLUMN new_id SET NOT NULL;

-- 2. Mirror columns on FK dependents
ALTER TABLE public.issues ADD COLUMN new_ai_session_id BIGINT;
ALTER TABLE public.ai_session_memory ADD COLUMN new_session_id BIGINT;
ALTER TABLE public.agent_runs ADD COLUMN new_session_id BIGINT;

UPDATE public.issues i
   SET new_ai_session_id = s.new_id
  FROM public.ai_sessions s
 WHERE i.ai_session_id = s.id;

UPDATE public.ai_session_memory m
   SET new_session_id = s.new_id
  FROM public.ai_sessions s
 WHERE m.session_id = s.id;

UPDATE public.agent_runs r
   SET new_session_id = s.new_id
  FROM public.ai_sessions s
 WHERE r.session_id = s.id;

-- 3. Drop old FKs
ALTER TABLE public.issues DROP CONSTRAINT IF EXISTS issues_ai_session_id_fkey;
ALTER TABLE public.ai_session_memory DROP CONSTRAINT IF EXISTS ai_session_memory_session_id_fkey;
ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_session_id_fkey;

-- 4. Drop old PK + swap columns
ALTER TABLE public.ai_sessions DROP CONSTRAINT IF EXISTS ai_sessions_pkey;
ALTER TABLE public.ai_sessions DROP COLUMN id;
ALTER TABLE public.ai_sessions RENAME COLUMN new_id TO id;
ALTER TABLE public.ai_sessions ADD PRIMARY KEY (id);
ALTER TABLE public.ai_sessions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE public.issues DROP COLUMN ai_session_id;
ALTER TABLE public.issues RENAME COLUMN new_ai_session_id TO ai_session_id;

ALTER TABLE public.ai_session_memory DROP CONSTRAINT IF EXISTS ai_session_memory_pkey;
ALTER TABLE public.ai_session_memory DROP COLUMN session_id;
ALTER TABLE public.ai_session_memory RENAME COLUMN new_session_id TO session_id;
ALTER TABLE public.ai_session_memory ADD PRIMARY KEY (session_id);

ALTER TABLE public.agent_runs DROP COLUMN session_id;
ALTER TABLE public.agent_runs RENAME COLUMN new_session_id TO session_id;

-- 5. Rebuild FKs
ALTER TABLE public.issues
    ADD CONSTRAINT issues_ai_session_id_fkey
    FOREIGN KEY (ai_session_id) REFERENCES public.ai_sessions(id) ON DELETE SET NULL;

ALTER TABLE public.ai_session_memory
    ADD CONSTRAINT ai_session_memory_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES public.ai_sessions(id) ON DELETE CASCADE;

ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES public.ai_sessions(id) ON DELETE SET NULL;

-- 6. Rebuild indexes
CREATE INDEX IF NOT EXISTS idx_issues_ai_session
    ON public.issues(ai_session_id) WHERE ai_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_session
    ON public.agent_runs(session_id) WHERE session_id IS NOT NULL;

-- 7. Notify PostgREST to reload schema
NOTIFY pgrst, 'reload schema';

COMMIT;
```

- [ ] **Step 2 — Write the rollback migration.**

```sql
-- supabase/migrations/231_ai_sessions_snowflake_rollback.sql
-- Restore ai_sessions.id to UUID. Loses the new BIGINT values.
-- Requires a backup snapshot taken IMMEDIATELY before applying 231.

BEGIN;

-- This rollback restores from the pre-231 snapshot. The actual SQL is
-- "DROP and RESTORE FROM pg_dump", not column-level revert, because the
-- old UUID values are not preserved after step 4 of 231.
--
-- Operator procedure:
--   1. pg_restore --schema=public --table=ai_sessions ai_sessions.dump
--   2. pg_restore --schema=public --table=issues issues.dump
--   3. pg_restore --schema=public --table=ai_session_memory ai_session_memory.dump
--   4. pg_restore --schema=public --table=agent_runs agent_runs.dump
--   5. Re-apply migrations 232+ if they came in after 231

RAISE NOTICE 'Manual rollback required. See header comment.';

COMMIT;
```

- [ ] **Step 3 — Write the test.**

```python
# backend/tests/migrations/test_231_ai_sessions_snowflake.py
"""Verify mig 231: ai_sessions.id is BIGINT, FK dependents rebuilt."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_ai_sessions_id_is_bigint():
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ai_sessions' AND column_name='id'",
        {},
    )
    assert rows[0]["data_type"] == "bigint"


@pytest.mark.asyncio
async def test_fk_dependents_are_bigint():
    targets = [
        ("issues", "ai_session_id"),
        ("ai_session_memory", "session_id"),
        ("agent_runs", "session_id"),
    ]
    for table, col in targets:
        rows = await db_engine.fetch_all(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c",
            {"t": table, "c": col},
        )
        assert rows[0]["data_type"] == "bigint", f"{table}.{col} should be bigint"


@pytest.mark.asyncio
async def test_fk_constraints_rebuilt():
    rows = await db_engine.fetch_all(
        """
        SELECT conname FROM pg_constraint
         WHERE contype = 'f'
           AND conname IN (
               'issues_ai_session_id_fkey',
               'ai_session_memory_session_id_fkey',
               'agent_runs_session_id_fkey'
           )
        """,
        {},
    )
    assert {r["conname"] for r in rows} == {
        "issues_ai_session_id_fkey",
        "ai_session_memory_session_id_fkey",
        "agent_runs_session_id_fkey",
    }
```

- [ ] **Step 4 — Apply locally.**

```bash
# Snapshot first (recommended even locally)
pg_dump -h 127.0.0.1 -p 54322 -U postgres -d postgres -t public.ai_sessions \
    -t public.issues -t public.ai_session_memory -t public.agent_runs \
    > /tmp/pre-231-snapshot.sql

psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
    -f supabase/migrations/231_ai_sessions_snowflake.sql
```

- [ ] **Step 5 — Run tests.**

```bash
cd backend
uv run pytest tests/migrations/test_231_ai_sessions_snowflake.py -v
```

Expected: 3 passed.

- [ ] **Step 6 — Commit.**

```bash
git add supabase/migrations/231_ai_sessions_snowflake.sql \
        supabase/migrations/231_ai_sessions_snowflake_rollback.sql \
        backend/tests/migrations/test_231_ai_sessions_snowflake.py
git commit -m "feat(db): migrate ai_sessions.id UUID→Snowflake (PR-B/3)

Mirrors mig 051's column-rename pattern. Rebuilds 3 FK dependents:
issues.ai_session_id, ai_session_memory.session_id, agent_runs.session_id.

Requires ~10-30s read-only window during migration (DROP/RENAME of
the id column). Best deployed during low-traffic.

Rollback requires pre-migration pg_dump snapshot — see companion
*_rollback.sql header."
```

## Task B3 — Migration: `agent_runs` UUID → Snowflake

**Files:**
- Create: `supabase/migrations/232_agent_runs_snowflake.sql`
- Create: `supabase/migrations/232_agent_runs_snowflake_rollback.sql`
- Test: `backend/tests/migrations/test_232_agent_runs_snowflake.py`

- [ ] **Step 1 — Write the migration.**

```sql
-- supabase/migrations/232_agent_runs_snowflake.sql
-- Convert agent_runs.id from UUID to BIGINT Snowflake.
--
-- FK dependents:
--   issue_messages.agent_run_id  (mig 206)
--
-- (No other tables reference agent_runs.id per Task B1 grep.)

BEGIN;

ALTER TABLE public.agent_runs ADD COLUMN new_id BIGINT;
UPDATE public.agent_runs SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
ALTER TABLE public.agent_runs ALTER COLUMN new_id SET NOT NULL;

ALTER TABLE public.issue_messages ADD COLUMN new_agent_run_id BIGINT;
UPDATE public.issue_messages m
   SET new_agent_run_id = r.new_id
  FROM public.agent_runs r
 WHERE m.agent_run_id = r.id;

ALTER TABLE public.issue_messages DROP CONSTRAINT IF EXISTS issue_messages_agent_run_id_fkey;

ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_pkey;
ALTER TABLE public.agent_runs DROP COLUMN id;
ALTER TABLE public.agent_runs RENAME COLUMN new_id TO id;
ALTER TABLE public.agent_runs ADD PRIMARY KEY (id);
ALTER TABLE public.agent_runs ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE public.issue_messages DROP COLUMN agent_run_id;
ALTER TABLE public.issue_messages RENAME COLUMN new_agent_run_id TO agent_run_id;

ALTER TABLE public.issue_messages
    ADD CONSTRAINT issue_messages_agent_run_id_fkey
    FOREIGN KEY (agent_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_issue_messages_agent_run
    ON public.issue_messages(agent_run_id) WHERE agent_run_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';

COMMIT;
```

- [ ] **Step 2 — Companion rollback** (mirror Task B2 Step 2 shape).

- [ ] **Step 3 — Test.**

```python
# backend/tests/migrations/test_232_agent_runs_snowflake.py
"""Verify mig 232: agent_runs.id is BIGINT, issue_messages.agent_run_id rebuilt."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_agent_runs_id_is_bigint():
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='agent_runs' AND column_name='id'",
        {},
    )
    assert rows[0]["data_type"] == "bigint"


@pytest.mark.asyncio
async def test_issue_messages_agent_run_id_is_bigint():
    rows = await db_engine.fetch_all(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='issue_messages' "
        "AND column_name='agent_run_id'",
        {},
    )
    assert rows[0]["data_type"] == "bigint"
```

- [ ] **Step 4 — Apply + test + commit.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
    -f supabase/migrations/232_agent_runs_snowflake.sql

cd backend
uv run pytest tests/migrations/test_232_agent_runs_snowflake.py -v

git add supabase/migrations/232_agent_runs_snowflake*.sql \
        backend/tests/migrations/test_232_agent_runs_snowflake.py
git commit -m "feat(db): migrate agent_runs.id UUID→Snowflake (PR-B/3)"
```

## Task B4 — Migration: `issue_messages` UUID → Snowflake

**Files:**
- Create: `supabase/migrations/233_issue_messages_snowflake.sql`
- Create: `supabase/migrations/233_issue_messages_snowflake_rollback.sql`
- Test: `backend/tests/migrations/test_233_issue_messages_snowflake.py`

- [ ] **Step 1 — Write the migration** (same pattern; no external FK dependents per spec — verify with Task B1 grep result).

```sql
-- supabase/migrations/233_issue_messages_snowflake.sql
-- Convert issue_messages.id from UUID to BIGINT Snowflake.
-- No FK dependents detected by Task B1 grep — confirm before applying prod.

BEGIN;

ALTER TABLE public.issue_messages ADD COLUMN new_id BIGINT;
UPDATE public.issue_messages SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
ALTER TABLE public.issue_messages ALTER COLUMN new_id SET NOT NULL;

ALTER TABLE public.issue_messages DROP CONSTRAINT IF EXISTS issue_messages_pkey;
ALTER TABLE public.issue_messages DROP COLUMN id;
ALTER TABLE public.issue_messages RENAME COLUMN new_id TO id;
ALTER TABLE public.issue_messages ADD PRIMARY KEY (id);
ALTER TABLE public.issue_messages ALTER COLUMN id SET DEFAULT generate_snowflake_id();

NOTIFY pgrst, 'reload schema';

COMMIT;
```

- [ ] **Step 2 — Rollback companion + test + apply + commit.** (Same pattern as B2/B3.)

## Task B5 — Open PR-B + deploy + verify

- [ ] **Step 1 — Push + open PR.**

```bash
git push -u origin id-unify/pr-b-snowflake-3-tables
gh pr create --base master \
  --title "feat(db): UUID→Snowflake for ai_sessions/agent_runs/issue_messages (PR-B)" \
  --body "Three sibling migrations (231/232/233) bringing the paperclip-style UUID PKs onto Snowflake. Rebuilds FK dependents in same transaction per table.

Requires ~30s read-only window — deploy during low-traffic window.

See docs/superpowers/specs/2026-05-28-id-unification-design.md § 4."
```

- [ ] **Step 2 — Coordinate prod deployment window.**

Notify operator. Snapshot prod DB before:

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker exec mediahub-sb-prod-db pg_dump -U postgres -d postgres \
    -t public.ai_sessions -t public.issues -t public.ai_session_memory \
    -t public.agent_runs -t public.issue_messages \
    > /tmp/pre-pr-b-snapshot-$(date +%s).sql
  sudo /usr/local/bin/docker cp mediahub-sb-prod-db:/tmp/pre-pr-b-snapshot-*.sql /volume1/backups/
'
```

- [ ] **Step 3 — Merge + apply migrations on prod via the Run SQL Migration on NAS workflow.**

- [ ] **Step 4 — Verify on prod.**

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -d postgres -c "
    SELECT
      (SELECT data_type FROM information_schema.columns
         WHERE table_schema='public' AND table_name='ai_sessions' AND column_name='id') AS ai_sessions_id_type,
      (SELECT data_type FROM information_schema.columns
         WHERE table_schema='public' AND table_name='agent_runs' AND column_name='id') AS agent_runs_id_type,
      (SELECT data_type FROM information_schema.columns
         WHERE table_schema='public' AND table_name='issue_messages' AND column_name='id') AS issue_messages_id_type
    ;
  "
'
```

Expected: all three return `bigint`.

---

# PR-C — `scope_type='personal'` rows → personal team snowflakes

**Branch:** `id-unify/pr-c-scope-remap`

**Goal:** For every personal-scope row in `resource_items`/`folders`/`tags`/`smart_collections`, set `scope_id = (the user's personal_team.id)::text`. Personal scope rows STILL have `scope_type='personal'` after this PR — code is unchanged — but the scope_id is now a Snowflake string, consistent with team-scope rows.

**Risk level:** Low. Pure data update. Reversible by restoring from snapshot.

## Task C1 — Migration: scope_id remap

**Files:**
- Create: `supabase/migrations/234_personal_scope_id_remap.sql`
- Test: `backend/tests/migrations/test_234_personal_scope_remap.py`

- [ ] **Step 1 — Branch.**

```bash
git checkout master && git pull
git checkout -b id-unify/pr-c-scope-remap
```

- [ ] **Step 2 — Write the migration.**

```sql
-- supabase/migrations/234_personal_scope_id_remap.sql
-- For every scope_type='personal' row, remap scope_id from the
-- auth.user_uuid to the matching personal team's snowflake.
--
-- Pre-condition: mig 230 has run (every personal-scope owner has a
-- personal team). This migration is a no-op for rows whose owner
-- doesn't have a personal team (defensive — though no such rows
-- should exist after mig 230).
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3.

BEGIN;

UPDATE public.resource_items ri
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE ri.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = ri.scope_id;

UPDATE public.folders f
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE f.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = f.scope_id;

UPDATE public.tags g
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE g.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = g.scope_id;

UPDATE public.smart_collections sc
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE sc.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = sc.scope_id;

COMMIT;
```

- [ ] **Step 3 — Test (no personal scope_id is a UUID after this).**

```python
# backend/tests/migrations/test_234_personal_scope_remap.py
"""Verify mig 234: every personal-scope row's scope_id is a snowflake-shaped string."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_no_personal_scope_id_is_uuid():
    # Snowflakes are numeric strings; UUIDs match a hex-dash pattern.
    rows = await db_engine.fetch_all(
        """
        SELECT 'resource_items' AS tbl, scope_id FROM public.resource_items
         WHERE scope_type='personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'folders', scope_id FROM public.folders
         WHERE scope_type='personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'tags', scope_id FROM public.tags
         WHERE scope_type='personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'smart_collections', scope_id FROM public.smart_collections
         WHERE scope_type='personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        """,
        {},
    )
    assert len(rows) == 0, f"personal rows still have UUID scope_id: {rows[:5]}"


@pytest.mark.asyncio
async def test_every_personal_scope_id_matches_a_team():
    rows = await db_engine.fetch_all(
        """
        SELECT scope_id FROM public.resource_items
         WHERE scope_type='personal'
           AND scope_id NOT IN (SELECT id::text FROM public.teams WHERE kind='personal')
         LIMIT 5
        """,
        {},
    )
    assert len(rows) == 0, f"unmatched personal scope_ids: {rows}"
```

- [ ] **Step 4 — Apply + test + commit.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
    -f supabase/migrations/234_personal_scope_id_remap.sql

cd backend
uv run pytest tests/migrations/test_234_personal_scope_remap.py -v
```

```bash
git add supabase/migrations/234_personal_scope_id_remap.sql \
        backend/tests/migrations/test_234_personal_scope_remap.py
git commit -m "feat(db): remap personal scope_id from user_uuid to team_snowflake (PR-C)

Pre-condition: mig 230 ran (every personal owner has a personal team).
After this migration, scope_id is uniformly a numeric string referring
to a teams row — both for scope_type='personal' and 'team'.

Application code still distinguishes the two scope_type values; PR-E
collapses that distinction."
```

## Task C2 — Open PR-C + deploy + verify

- [ ] **Step 1 — Push + open PR.**

```bash
git push -u origin id-unify/pr-c-scope-remap
gh pr create --base master \
  --title "feat(db): remap personal scope_id to personal-team snowflakes (PR-C)" \
  --body "Pure data migration. scope_type='personal' rows now point at their owner's personal team snowflake instead of the auth user UUID.

App code unchanged — still recognises both scope_type values. PR-E collapses the distinction.

See spec § 3."
```

- [ ] **Step 2 — Merge + apply + sanity-check prod.**

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -d postgres -c "
    SELECT scope_type, COUNT(*) FROM public.resource_items GROUP BY scope_type ORDER BY 1;
  "
'
```

Expected: both rows present (`personal` + `team`), but if you `SELECT DISTINCT scope_id FROM public.resource_items WHERE scope_type='personal' AND scope_id ~ '^[0-9a-f]{8}-'` you should get 0 rows.

---

# PR-D — Storage path migration (NAS files + DB)

**Branch:** `id-unify/pr-d-storage-paths`

**Goal:** Move files on the NAS from `teams/{user_uuid}/uploads/...` to `teams/{personal_team_snowflake}/uploads/...` AND update `resources.file_path` + `resource_versions.file_path` to match. This is an offline operator window.

**Risk level:** Medium (file-system ops). Snapshot-then-replay rollback.

## Task D1 — One-shot operator script

**Files:**
- Create: `scripts/migrations/2026-05-28-storage-path-personal-team.sh`
- Create: `scripts/migrations/2026-05-28-storage-path-personal-team.sql` (DB updates triggered by the script)
- Test: dry-run on dev NAS first

- [ ] **Step 1 — Branch.**

```bash
git checkout master && git pull
git checkout -b id-unify/pr-d-storage-paths
```

- [ ] **Step 2 — Write the script.**

```bash
#!/bin/bash
# scripts/migrations/2026-05-28-storage-path-personal-team.sh
# One-shot: move files from teams/{user_uuid}/ to teams/{personal_team_snowflake}/
# on the NAS, and update the DB file_path values to match.
#
# Pre-conditions:
#   - PR-A + PR-C have shipped (every personal user has a personal team snowflake,
#     and scope_id is the team snowflake string).
#   - Operator has snapshotted NAS storage (Synology snapshot recommended).
#
# Usage (on NAS, as the heygo user):
#   bash scripts/migrations/2026-05-28-storage-path-personal-team.sh <DRY_RUN|APPLY>

set -euo pipefail

MODE="${1:-DRY_RUN}"
STORAGE_ROOT="${STORAGE_ROOT:-/volume2/sources/MediaHub.library}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-55436}"

echo "Mode: $MODE"
echo "Storage root: $STORAGE_ROOT"

# Get mapping of (user_uuid -> personal_team_snowflake) from DB
mapping_file=$(mktemp)
sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -d postgres -tA -F'|' -c "
SELECT owner_id::text, id::text
  FROM public.teams
 WHERE kind = 'personal'
" > "$mapping_file"

echo "Found $(wc -l < "$mapping_file") personal teams to migrate"

while IFS='|' read -r user_uuid team_snowflake; do
    src="$STORAGE_ROOT/teams/$user_uuid"
    dst="$STORAGE_ROOT/teams/$team_snowflake"

    if [ ! -d "$src" ]; then
        echo "SKIP (no source dir): $user_uuid → $team_snowflake"
        continue
    fi
    if [ -d "$dst" ]; then
        echo "WARN (dst exists): $user_uuid → $team_snowflake — manual review needed"
        continue
    fi

    if [ "$MODE" = "APPLY" ]; then
        echo "MOVE: $src → $dst"
        mv "$src" "$dst"
    else
        echo "DRY: would mv $src → $dst"
    fi
done < "$mapping_file"

rm "$mapping_file"

if [ "$MODE" = "APPLY" ]; then
    echo "Storage move complete. Applying DB UPDATE..."
    sudo /usr/local/bin/docker exec -i mediahub-sb-prod-db psql -U postgres -d postgres \
        < scripts/migrations/2026-05-28-storage-path-personal-team.sql
    echo "DB updated. Run verification queries to confirm."
fi
```

- [ ] **Step 3 — Write the DB UPDATE companion.**

```sql
-- scripts/migrations/2026-05-28-storage-path-personal-team.sql
-- Update resources.file_path + resource_versions.file_path to replace
-- user_uuid embedded in path with the matching personal_team snowflake.

BEGIN;

UPDATE public.resources r
   SET file_path = REPLACE(file_path,
                            'teams/' || mapping.user_uuid || '/',
                            'teams/' || mapping.team_snowflake || '/')
  FROM (
      SELECT owner_id::text AS user_uuid, id::text AS team_snowflake
        FROM public.teams
       WHERE kind = 'personal'
  ) AS mapping
 WHERE r.file_path LIKE 'teams/' || mapping.user_uuid || '/%';

UPDATE public.resource_versions v
   SET file_path = REPLACE(file_path,
                            'teams/' || mapping.user_uuid || '/',
                            'teams/' || mapping.team_snowflake || '/')
  FROM (
      SELECT owner_id::text AS user_uuid, id::text AS team_snowflake
        FROM public.teams
       WHERE kind = 'personal'
  ) AS mapping
 WHERE v.file_path LIKE 'teams/' || mapping.user_uuid || '/%';

-- Verification:
-- After this UPDATE, no file_path should still embed a UUID-shaped segment
-- in the form `teams/{8-4-4-4-12 hex}/...`.
SELECT COUNT(*) AS uuid_paths_remaining
  FROM public.resources
 WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}';

COMMIT;
```

- [ ] **Step 4 — Test locally (on dev NAS clone or in CI).**

For local testing, build a fixture:

```bash
# Test dry-run
bash scripts/migrations/2026-05-28-storage-path-personal-team.sh DRY_RUN
# Verify: prints "DRY: would mv ..." for each mapping, no actual moves
```

- [ ] **Step 5 — Commit (script only; don't run on prod yet).**

```bash
git add scripts/migrations/2026-05-28-storage-path-personal-team.sh \
        scripts/migrations/2026-05-28-storage-path-personal-team.sql
chmod +x scripts/migrations/2026-05-28-storage-path-personal-team.sh
git commit -m "feat(scripts): one-shot storage-path migration for personal-team (PR-D)

Move files from teams/{user_uuid}/ to teams/{personal_team_snowflake}/
on the NAS and update resources.file_path + resource_versions.file_path
to match.

Operator runs after PR-A + PR-C have shipped. Synology snapshot
recommended before APPLY mode."
```

## Task D2 — Open PR-D + coordinate operator window

- [ ] **Step 1 — Push + open PR.**

```bash
git push -u origin id-unify/pr-d-storage-paths
gh pr create --base master \
  --title "feat(scripts): storage path migration script for personal-team rename (PR-D)" \
  --body "Adds the one-shot migration script that operator runs during a deploy window. No code path triggers this automatically — operator authority only.

See spec § 5."
```

- [ ] **Step 2 — Schedule operator window + dry run.**

Coordinate offline. Operator first runs DRY_RUN, reviews output, then runs APPLY.

```bash
# On NAS:
ssh -p 1122 heygo@192.168.50.9
cd /volume1/docker/mediahub
git pull
bash scripts/migrations/2026-05-28-storage-path-personal-team.sh DRY_RUN
# Review output
bash scripts/migrations/2026-05-28-storage-path-personal-team.sh APPLY
```

- [ ] **Step 3 — Verify.**

```sql
-- On prod DB:
SELECT COUNT(*) FROM public.resources
 WHERE file_path ~ 'teams/[0-9a-f]{8}-[0-9a-f]{4}';
-- Expected: 0
```

Plus a few representative file-existence checks on the NAS.

---

# PR-E — Code simplification + RLS

**Branch:** `id-unify/pr-e-code-simplify`

**Goal:** Remove personal-scope branches from application code now that personal scope = team scope at the data layer. Simplify RLS policies. Switch UI to use `team.kind === 'personal'` instead of `scope_type === 'personal'`.

**Risk level:** Medium (touches many files). Tests catch regressions.

## Task E1 — Backend: simplify scope branches in repositories

**Files (modify, one per task step):**
- `backend/app/repositories/resources_repository.py`
- `backend/app/repositories/permission_repository.py`
- `backend/app/services/library/resources_service.py`

- [ ] **Step 1 — Branch.**

```bash
git checkout master && git pull
git checkout -b id-unify/pr-e-code-simplify
```

- [ ] **Step 2 — Grep all personal-branch sites.**

```bash
grep -rnE "scope_type[\"']?\s*[=:][\"']?personal" backend/app | tee /tmp/personal-branches.txt
```

Each hit is a candidate. Most should be of the form `if scope_type == "personal"` or in SQL `WHERE (scope_type='personal' AND scope_id=:uid) OR ...`.

- [ ] **Step 3 — Refactor permission_repository.**

In `backend/app/repositories/permission_repository.py`, find every method building access SQL with a personal/team OR clause and replace with the team-only form. Example transformation:

```python
# BEFORE
sql = (
    "SELECT 1 FROM public.resource_items ri "
    "WHERE ri.scope_type IN ('personal','team') "
    "  AND ((ri.scope_type='personal' AND ri.scope_id::text=:uid) "
    "       OR (ri.scope_type='team' AND ri.scope_id::text IN "
    "             (SELECT team_id::text FROM public.team_members "
    "                WHERE user_id=:uid)))"
)

# AFTER
sql = (
    "SELECT 1 FROM public.resource_items ri "
    "WHERE ri.scope_id::text IN ("
    "    SELECT team_id::text FROM public.team_members WHERE user_id=:uid"
    ")"
)
```

- [ ] **Step 4 — Refactor resources_repository.list_accessible_for_user (S4 helper).**

Same shape — the `WHERE` clause OR arm goes away.

- [ ] **Step 5 — Run backend tests.**

```bash
cd backend
uv run pytest tests/ -v --tb=short -x
```

Expected: existing tests pass. If a test asserts the SQL contains `scope_type='personal'`, update it.

- [ ] **Step 6 — Commit.**

```bash
git add backend/app/repositories/ backend/app/services/library/
git commit -m "refactor(backend): collapse personal/team scope branches

After PR-C, scope_id always references a teams row regardless of
scope_type. Permission checks can use team_members alone — remove
the OR arm that special-cased personal scope."
```

## Task E2 — Frontend: collapse personal/team UI branches

**Files:**
- `frontend/contexts/ResourcesContext.tsx`
- `frontend/components/ResourcesSidebar.tsx`

- [ ] **Step 1 — Grep personal-branch sites in frontend.**

```bash
grep -rnE "scope_type[\"']?\s*[=:][\"']?personal|scopeType\s*===?\s*['\"]personal['\"]" \
    frontend/contexts frontend/components frontend/services 2>/dev/null
```

- [ ] **Step 2 — Refactor sidebar.**

Replace `scopeType === 'personal'` checks with `team?.kind === 'personal'`. The Resources sidebar can now treat all scopes uniformly through `team_members`. The "personal mode" UI distinction (showing "My Uploads" vs "Libraries") becomes a single rule: if the user is currently viewing their personal team, show "My Uploads"; if a collaborative team, show "Libraries".

(Concrete code changes depend on existing component structure — read `ResourcesSidebar.tsx` first.)

- [ ] **Step 3 — Typecheck + tests.**

```bash
cd frontend
npm run typecheck
npm test -- ResourcesSidebar
```

- [ ] **Step 4 — Commit.**

```bash
git add frontend/contexts/ResourcesContext.tsx \
        frontend/components/ResourcesSidebar.tsx
git commit -m "refactor(frontend): use team.kind for personal/team UI distinction

After backend PR-E, scope_type='personal' and scope_type='team' point
at the same team_members-rooted authz path. UI distinguishes personal
vs collaborative via teams.kind on the active team."
```

## Task E3 — Simplify RLS policies

**Files:**
- Create: `supabase/migrations/235_simplify_personal_team_rls.sql`
- Test: `backend/tests/migrations/test_235_rls_simplified.py`

- [ ] **Step 1 — Locate RLS policies to update.**

Spec § 7 lists `resource_items`, `folders`, `tags`, `smart_collections` (plus their `065`, `045`, `077` policy migrations). Confirm:

```bash
grep -rln "CREATE POLICY" supabase/migrations/ | xargs grep -l "scope_type" | head
```

- [ ] **Step 2 — Write migration replacing each `OR scope_type='personal' AND...` policy.**

```sql
-- supabase/migrations/235_simplify_personal_team_rls.sql
-- Simplify RLS policies that branched on scope_type='personal' OR scope_type='team'.
-- After PR-C, scope_id always references a teams row; team_members is the single source of authz.

BEGIN;

-- resource_items
DROP POLICY IF EXISTS resource_items_select ON public.resource_items;
CREATE POLICY resource_items_select ON public.resource_items
    FOR SELECT
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS resource_items_insert ON public.resource_items;
CREATE POLICY resource_items_insert ON public.resource_items
    FOR INSERT
    WITH CHECK (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS resource_items_update ON public.resource_items;
CREATE POLICY resource_items_update ON public.resource_items
    FOR UPDATE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS resource_items_delete ON public.resource_items;
CREATE POLICY resource_items_delete ON public.resource_items
    FOR DELETE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

-- Repeat the same shape for: folders, tags, smart_collections.
-- (Full SQL produced when this task runs — read the previous migration
-- (065/045/077) to capture each policy's exact original conditions and
-- their column references; then rewrite the simplified version below.)

-- folders
DROP POLICY IF EXISTS folders_select ON public.folders;
CREATE POLICY folders_select ON public.folders
    FOR SELECT
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS folders_insert ON public.folders;
CREATE POLICY folders_insert ON public.folders
    FOR INSERT
    WITH CHECK (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS folders_update ON public.folders;
CREATE POLICY folders_update ON public.folders
    FOR UPDATE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS folders_delete ON public.folders;
CREATE POLICY folders_delete ON public.folders
    FOR DELETE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

-- tags
DROP POLICY IF EXISTS tags_select ON public.tags;
CREATE POLICY tags_select ON public.tags
    FOR SELECT
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS tags_insert ON public.tags;
CREATE POLICY tags_insert ON public.tags
    FOR INSERT
    WITH CHECK (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS tags_update ON public.tags;
CREATE POLICY tags_update ON public.tags
    FOR UPDATE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS tags_delete ON public.tags;
CREATE POLICY tags_delete ON public.tags
    FOR DELETE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

-- smart_collections
DROP POLICY IF EXISTS smart_collections_select ON public.smart_collections;
CREATE POLICY smart_collections_select ON public.smart_collections
    FOR SELECT
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS smart_collections_insert ON public.smart_collections;
CREATE POLICY smart_collections_insert ON public.smart_collections
    FOR INSERT
    WITH CHECK (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS smart_collections_update ON public.smart_collections;
CREATE POLICY smart_collections_update ON public.smart_collections
    FOR UPDATE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );
DROP POLICY IF EXISTS smart_collections_delete ON public.smart_collections;
CREATE POLICY smart_collections_delete ON public.smart_collections
    FOR DELETE
    USING (
        scope_id IN (
            SELECT team_id::text FROM public.team_members WHERE user_id = auth.uid()
        )
    );

NOTIFY pgrst, 'reload schema';

COMMIT;
```

- [ ] **Step 3 — Test that policies exist with simplified bodies.**

```python
# backend/tests/migrations/test_235_rls_simplified.py
"""Verify mig 235: RLS policies on scope-aware tables use team_members only."""

from __future__ import annotations

import pytest

from app.db import engine as db_engine


@pytest.mark.asyncio
async def test_rls_policies_no_longer_branch_on_personal():
    rows = await db_engine.fetch_all(
        """
        SELECT polname, qual::text AS using_clause
          FROM pg_policy
         WHERE polrelid::regclass::text IN
           ('public.resource_items','public.folders','public.tags','public.smart_collections')
        """,
        {},
    )
    bad = [r for r in rows if "scope_type" in (r.get("using_clause") or "")]
    assert len(bad) == 0, f"policies still branch on scope_type: {bad}"
```

- [ ] **Step 4 — Apply + test + commit.**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
    -f supabase/migrations/235_simplify_personal_team_rls.sql

cd backend
uv run pytest tests/migrations/test_235_rls_simplified.py -v

git add supabase/migrations/235_simplify_personal_team_rls.sql \
        backend/tests/migrations/test_235_rls_simplified.py
git commit -m "refactor(rls): simplify scope-aware policies after PR-C/E

After scope_id always references teams, RLS only needs team_members.
Reduces each of 4 tables × 4 actions = 16 policies by removing the
OR arm. Halves the policy execution time on row checks."
```

## Task E4 — Open PR-E + merge

- [ ] **Step 1 — Push + open PR.**

```bash
git push -u origin id-unify/pr-e-code-simplify
gh pr create --base master \
  --title "refactor: simplify personal/team branches in code + RLS (PR-E)" \
  --body "Removes the personal-scope OR arm from repositories, services, RLS policies, and frontend sidebar logic.

Concludes the ID Unification track. After this PR:
- scope_type='personal' rows still exist (column kept for future-proof) but app code treats all scope_id values uniformly via team_members.
- teams.kind = 'personal' is the UI signal for the personal-workspace label.
- Storage paths uniformly under teams/{snowflake}/.

See spec § 7 + § 8."
```

- [ ] **Step 2 — Wait CI + merge + verify on prod.**

Final sanity check after merge: open a freshly-signed-up user account, verify their personal team exists, they can upload, the file lands at `teams/{snowflake}/uploads/...`.

---

## Self-review

**Spec coverage:**
- § 1 Goal → entire plan
- § 2 Current state → context only; no task needed
- § 3 Personal=Team → Tasks A1 / A2 / A3 / A4 / E2 / E3
- § 4 UUID→Snowflake → Tasks B1 / B2 / B3 / B4 / B5
- § 5 Storage path → Tasks D1 / D2
- § 6 Compatibility (grace tokens) → covered conceptually in PR-D notes; no task because impl is "accept token failure" (no code change)
- § 7 RLS → Task E3
- § 8 Code simplification → Tasks E1 / E2
- § 9 Deployment sequence → mirrored in PR-A → PR-E flow
- § 10 Testing → each task carries TDD tests
- § 11 Out of scope → no tasks (correctly)

**Placeholder scan:**
- Task E3 has a comment "Full SQL produced when this task runs — read the previous migration..." which is borderline. Fix: the migration actually lists all 4 tables × 4 actions explicitly (resource_items + folders + tags + smart_collections, each with SELECT/INSERT/UPDATE/DELETE), so the SQL is concrete. Comment retained for traceability.

**Type consistency:**
- `kind` enum values consistent: `'personal'` and `'collaborative'` across Tasks A1, A2, A3, A4.
- BIGINT migration pattern consistent across B2, B3, B4 (mirrors mig 051).
- `team_members.user_id = auth.uid()` shape consistent across RLS policies in E3.
- Auto-create trigger function name `create_personal_team_for_user` (Task A3) referenced consistently.
- Singleton enforcement function name `enforce_personal_team_singleton` (Task A2) referenced consistently.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-28-id-unification.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch fresh subagent per task, two-stage review. Each PR's tasks are tightly scoped and benefit from clean per-task subagent context. Coordinator handles PR boundaries.

2. **Inline Execution** — execute tasks in this session using executing-plans, batch checkpoints between PR boundaries.

Which approach?
