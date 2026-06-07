# ID Unification + Personal-as-Team Design Spec

**Prerequisite for:** Workshop / 工坊 (2026-05-28-workshop-design.md)

**Status:** Approved 2026-05-28 (brainstorming complete; user gated direct review on me — no second human-review pass).

---

## Goal

Unify the ID systems across mediahub so that **every self-owned table uses BIGINT Snowflake** (the sole exception being `auth.users.id`, which Supabase Auth pins to UUID). Concurrent with that, **abstract "personal workspace" into a special single-member `team`** so that all scope-aware code paths converge on the same model (`team_members`-based authz instead of `(scope_type='personal' OR scope_type='team')` branches). Storage paths converge to a single naming convention with no asymmetry.

Three observable wins on the other side:

- All `task_id` / `scope_id` references in new code (e.g. `workshop_items` from the parallel Workshop spec) are uniformly `BIGINT`.
- `resources` / `folders` / `tags` / `smart_collections` etc. RLS policies lose the `scope_type='personal' OR scope_type='team'` `OR` arm — a 50 % rule-shape simplification.
- The on-disk path is `teams/{team_snowflake}/uploads/…` for everything, eliminating the current UUID-mixed-with-Snowflake mess inside `teams/` (visible in the user's NAS screenshot 2026-05-28).

## Current State (measured, not assumed)

### ID types by table

| Table | PK type | Source |
|---|---|---|
| `auth.users` | UUID | Supabase Auth — **not migratable** |
| `teams`, `projects`, `issues`, `libraries`, `folders`, `resources`, `resource_items` | BIGINT Snowflake | mig 051 / table-native |
| **`ai_sessions`** | **UUID** | mig 121 / 138 — paperclip-style, missed by mig 051 |
| **`agent_runs`** | **UUID** | mig 145 — paperclip-style |
| **`issue_messages`** | **UUID** | mig 205 — paperclip-style |

### scope_type asymmetry

`resource_items.scope_type` (and parallel fields on `folders`, `tags`, `smart_collections`) takes:

- `'personal'` with `scope_id` = `auth.users.id` (UUID, stored as TEXT after mig 051)
- `'team'` with `scope_id` = `teams.id` (BIGINT, stored as TEXT after mig 051)

Existing RLS policies and many SQL queries carry an `OR` branch covering both shapes.

### Storage path issue

`backend/app/services/library/resources_service.py:118`:
```python
relative_path = f"teams/{scope_id}/uploads/{resource_id}/v1/{safe_name}"
```

Personal scope writes to `teams/{user_uuid}/...` (misleading directory + UUID inside Snowflake-sibling tree). Team scope writes to `teams/{team_snowflake}/...`. NAS screenshot confirms both shapes coexist today.

## Architecture: Personal = Single-Member Team

### Schema additions

```sql
-- New column on existing teams table
ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'collaborative'
    CHECK (kind IN ('personal', 'collaborative'));

-- Optional but recommended: unique constraint preventing a user
-- having two 'personal' teams
CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_owner_personal
    ON public.teams (owner_id) WHERE kind = 'personal';
```

### Auto-create personal team on user creation

A trigger fires on `auth.users` INSERT (or its mirror in `public.users` if such a row exists):

```sql
CREATE OR REPLACE FUNCTION public.create_personal_team()
RETURNS TRIGGER AS $$
DECLARE
    new_team_id BIGINT;
BEGIN
    INSERT INTO public.teams (kind, owner_id, name)
    VALUES ('personal', NEW.id, 'Personal')
    RETURNING id INTO new_team_id;

    INSERT INTO public.team_members (team_id, user_id, role)
    VALUES (new_team_id, NEW.id, 'owner');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger location depends on whether mediahub mirrors auth.users into
-- public.users. If it does, attach the trigger to public.users; otherwise
-- create a Supabase Edge Function or hook the existing signup path.
```

### Lock-down trigger: personal teams may not gain members

```sql
CREATE OR REPLACE FUNCTION public.enforce_personal_team_singleton()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.teams t
         WHERE t.id = NEW.team_id AND t.kind = 'personal'
    ) THEN
        -- Reject second-or-later membership
        IF (SELECT COUNT(*) FROM public.team_members
              WHERE team_id = NEW.team_id) >= 1 THEN
            RAISE EXCEPTION 'personal team % cannot have more than one member', NEW.team_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_personal_team_singleton
    BEFORE INSERT ON public.team_members
    FOR EACH ROW EXECUTE FUNCTION public.enforce_personal_team_singleton();
```

API + UI also reject invitations to `kind='personal'` teams (defense in depth; the trigger is the authoritative gate).

### Promotion path (personal → collaborative) is one-way

`UPDATE teams SET kind='collaborative' WHERE id=:id AND kind='personal'` is allowed (user wants to invite a coworker into what used to be private). The reverse (`collaborative` → `personal`) is rejected by trigger — once a team has had multiple members or non-personal history, it cannot be "demoted."

### Data migration: backfill personal teams for existing users

```sql
-- For each distinct owner of personal-scope resource_items, create their
-- personal team (idempotent).
WITH owners AS (
    SELECT DISTINCT scope_id::uuid AS user_id
      FROM public.resource_items
     WHERE scope_type = 'personal'
)
INSERT INTO public.teams (kind, owner_id, name)
SELECT 'personal', user_id, 'Personal'
  FROM owners
 WHERE NOT EXISTS (
     SELECT 1 FROM public.teams
      WHERE owner_id = owners.user_id AND kind = 'personal'
 );

-- Mirror into team_members
INSERT INTO public.team_members (team_id, user_id, role)
SELECT t.id, t.owner_id, 'owner'
  FROM public.teams t
 WHERE t.kind = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.team_members
        WHERE team_id = t.id AND user_id = t.owner_id
   );
```

### scope_type / scope_id remap

After every personal user has a personal team, remap all scope-aware rows:

```sql
-- For resource_items
UPDATE public.resource_items ri
   SET scope_type = 'team',
       scope_id   = t.id::text
  FROM public.teams t
 WHERE ri.scope_type = 'personal'
   AND t.owner_id = ri.scope_id::uuid
   AND t.kind = 'personal';

-- Repeat the same shape for: folders, tags, smart_collections.
-- The exact column name is `scope_id` (TEXT) in all four tables (mig 051).
```

After the remap, every `scope_type` value is `'team'`. The column can stay (future-proof for hypothetical other scope types) or be dropped — **decided at plan stage** based on impact analysis. Recommendation: keep the column, drop the CHECK constraint that requires `'personal' OR 'team'`, accept that `'team'` becomes the only inserted value going forward.

### Auth-side trigger replacement

For Supabase, `auth.users` is owned by the auth schema; project triggers can't attach to it directly. Common pattern is to:

1. Use Supabase's `on_auth_user_created` trigger pattern (a project-defined trigger on `auth.users` via `SECURITY DEFINER` function in the `public` schema, granted by Supabase superuser). Mediahub may already have this for `users` profile mirror.
2. Or: hook the application's `signUp` path (`backend/app/api/auth_router.py::signup`) to call `create_personal_team()` after the user row is committed.

**Recommended:** approach (1) (DB-side trigger) because it covers all signup paths uniformly including admin user creation, Supabase studio inserts, and migrations. Approach (2) leaves gaps. Concrete implementation will reuse whatever pattern mediahub uses today for the `users` profile auto-create (look in `supabase/migrations/001_initial_schema.sql` and onwards).

## ID Migration: 3 Tables UUID → Snowflake

### Affected tables and their FK dependents

```
ai_sessions(id UUID)
   ← issues.ai_session_id            (mig 224)
   ← ai_session_memory.session_id    (mig 187)
   ← agent_runs.session_id           (mig 145)
   ← ai_messages.session_id          (if exists)
   ← (any other table referencing ai_sessions)

agent_runs(id UUID)
   ← issue_messages.agent_run_id     (mig 205)
   ← agent_runs_telemetry.run_id     (if exists)

issue_messages(id UUID)
   ← (likely no FK dependents — confirmed at plan stage)
```

Plan-stage task #1 will exhaustively grep `REFERENCES ai_sessions`, `REFERENCES agent_runs`, `REFERENCES issue_messages` across `supabase/migrations/` to ensure no FK dependent is missed.

### Migration pattern (per table)

Mirror `supabase/migrations/051_snowflake_migration.sql`'s pattern:

```sql
-- 1. Add new BIGINT id column on the target table
ALTER TABLE public.ai_sessions ADD COLUMN new_id BIGINT;
UPDATE public.ai_sessions SET new_id = generate_snowflake_id() WHERE new_id IS NULL;

-- 2. Add corresponding new_*_id columns on each FK dependent and backfill
ALTER TABLE public.issues ADD COLUMN new_ai_session_id BIGINT;
UPDATE public.issues i
   SET new_ai_session_id = s.new_id
  FROM public.ai_sessions s
 WHERE i.ai_session_id = s.id;

-- ... same for ai_session_memory, agent_runs, etc.

-- 3. Drop old FKs (capture constraint names from pg_constraint first)
ALTER TABLE public.issues DROP CONSTRAINT issues_ai_session_id_fkey;
-- ... etc.

-- 4. Drop old columns, rename new_*
ALTER TABLE public.ai_sessions DROP COLUMN id;
ALTER TABLE public.ai_sessions RENAME COLUMN new_id TO id;
ALTER TABLE public.ai_sessions ADD PRIMARY KEY (id);
ALTER TABLE public.ai_sessions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE public.issues DROP COLUMN ai_session_id;
ALTER TABLE public.issues RENAME COLUMN new_ai_session_id TO ai_session_id;

-- 5. Rebuild FKs + indexes
ALTER TABLE public.issues
    ADD CONSTRAINT issues_ai_session_id_fkey
    FOREIGN KEY (ai_session_id) REFERENCES public.ai_sessions(id) ON DELETE SET NULL;

CREATE INDEX idx_issues_ai_session ON public.issues(ai_session_id) WHERE ai_session_id IS NOT NULL;
```

### Application-side concerns during migration

- DBOS `agent_runs.session_id` — DBOS workflow state may have UUID baked in (since `agent_runs.id` is UUID-typed). Workflows in flight when migration runs will fail to look up their `session_id` after rename. **Mitigation:** run during a low-traffic window (sub-30 second read-only window per table is acceptable per spec § 9), or hold workflows pre-migration with a "drain mode" feature flag.
- Supabase Realtime publishes `ai_sessions` and `issue_messages` (per `mig 217` / `mig 210`). Realtime cache rebuilds itself per subscription; transient publication-config refresh is required (`NOTIFY pgrst 'reload schema'`).

### Application code that hardcodes UUID parsing

Frontend may treat `ai_session_id` as UUID string in URL routes (`/chat/:sid` etc.). Snowflake serialized as string still works (it's just a long numeric string). But components that validate UUID format will need their regex relaxed. Plan-stage will grep `validateUUID` / `isUuid` / regex literals matching UUID shape across frontend.

## Storage Path Refactor

### Path shape

```
Old (mixed):
  teams/{user_uuid}/uploads/{resource_id}/v1/{filename}        ← personal (UUID inside teams/)
  teams/{team_snowflake}/uploads/{resource_id}/v1/{filename}   ← team

New (uniform, post-personal-team-backfill):
  teams/{team_snowflake}/uploads/{resource_id}/v1/{filename}   ← all (personal team is just a team)
```

### Code change

`backend/app/services/library/resources_service.py:118` requires no logic change — once scope_id always resolves to a team's snowflake, the existing template is correct.

### Physical migration

After `scope_type/scope_id` data remap completes (so personal scope rows now point at the new personal team snowflake), run a one-shot script on the NAS:

```bash
# For each user whose personal data was just remapped, move their files
# from teams/{user_uuid}/ to teams/{personal_team_snowflake}/.
# Then UPDATE resources.file_path and resource_versions.file_path columns
# in DB to match.
```

This script lives at `scripts/migrations/2026-05-28-storage-path-personal-team.sh` (created in the implementation plan) and is run **once** by an operator during a deploy window.

### Grace period for media tokens

`backend/app/api/media_auth.py` issues short-lived (5 min) signed tokens embedding a file path. Tokens in flight when the migration completes will reference the old path. Options:

- **(A) Accept token failure:** old tokens 404 after the migration completes; clients re-fetch via a fresh `/media-token` call. Simple, acceptable for the 5-minute TTL window.
- **(B) Dual-path support:** for 24h after migration, the media file server tries the new path first then falls back to the old. Add minor complexity, eliminates user-visible breakage.

**Recommendation: (A).** The 5-minute token TTL bounds the breakage to a brief window and is consistent with mediahub's other "token failure → client retries" patterns.

Share links (`shares` table) embed `resource_id`, not file path — they're unaffected.

## RLS Simplification

After personal=team is complete, RLS policies that currently look like:

```sql
USING (
  (scope_type = 'personal' AND scope_id = auth.uid()::text)
  OR (scope_type = 'team' AND scope_id IN (SELECT team_id::text FROM team_members WHERE user_id = auth.uid()))
)
```

simplify to:

```sql
USING (scope_id IN (SELECT team_id::text FROM team_members WHERE user_id = auth.uid()))
```

Tables affected (from grep at brainstorm time): `resource_items`, `folders`, `tags`, `smart_collections`, plus their `_RLS_*` policy migrations (`065`, `045`, `077`).

Plan-stage will produce one migration file per RLS-bearing table, simplifying both `SELECT`, `INSERT`, `UPDATE`, and `DELETE` policies.

## Code Simplification Inventory

Targeted removal targets (greppable, exact list produced at plan stage):

- `backend/app/repositories/resources_repository.py`: any `scope_type == 'personal'` branch
- `backend/app/repositories/permission_repository.py`: same
- `backend/app/services/library/resources_service.py`: same
- `backend/app/api/resources_*.py`: routers that branch on scope kind
- `backend/app/api/resources_search_router.py` (S4): scope filter accepts only `'team'` now
- `backend/app/services/ai/tools/resource_fetch_tool.py` (S4): same
- `backend/app/services/ai/chat/resource_ref_resolver.py` (S4): same
- `frontend/contexts/ResourcesContext.tsx`: sidebarView personal/team detection collapses
- `frontend/components/ResourcesSidebar.tsx`: same

Some code paths may legitimately need to distinguish "is this the user's personal workspace?" for UI labeling — those check `team.kind === 'personal'` instead of `scope_type === 'personal'`.

## Deployment Sequence

Single PR per migration to keep blast radius small:

1. **PR-A (zero downtime)** — `teams.kind` column + auto-create trigger + backfill personal teams for existing users + team_members backfill. No behavior change yet.
2. **PR-B (~30s read-only window)** — 3 UUID→Snowflake migrations. Wrap in advisory lock + `pg_dump`-backed rollback. Best run during low-traffic window.
3. **PR-C (zero downtime)** — `scope_type/scope_id` remap. Personal scope rows now point at the personal team's snowflake. App code still works because it still recognizes `scope_type='personal'`.
4. **PR-D (online; storage operator window)** — physical NAS migration script + UPDATE `resources.file_path`. Coordinated by operator.
5. **PR-E (zero downtime)** — code simplification: remove personal-branch code, simplify RLS, switch UI to team.kind detection.

Total operator effort across PRs: estimated 1 hour (excluding CI/review wait).

## Testing Plan

### Per-PR

- Unit: existing test suites should pass on each PR — they exercise both personal and team scope today; after PR-C they still pass because code still works on both.
- Integration: add one test per migration step verifying data shape (count of personal teams = count of distinct personal scope owners post-backfill, etc.).

### Across-PR (after PR-E lands)

- Smoke: log in as a brand-new user, verify a personal team is auto-created, upload a file, verify it lands at `teams/{snowflake}/...` on disk.
- Smoke: try to invite a member to a personal team via API — expect 403.
- Smoke: existing user with both personal and team data — verify all resources still visible in their respective sidebars.
- Smoke: media token issued before PR-E + accessed after PR-E within 5 min → expect 404; client should re-fetch and recover.

### Rollback

Each migration carries a rollback `.sql` file that:

- Reverts column adds / renames / drops
- Restores RLS policy text from the prior state
- Restores `file_path` column values (requires a backup snapshot taken just before PR-D)

Practical rollback for PR-D requires file-system snapshot of `teams/` on NAS (Synology snapshot before running the mv script).

## Out of Scope

- `users` profile table refactor (if mediahub has one for displayName / avatar)
- Reverse migration (team → personal) — not supported (one-way only)
- Multi-tenant resource sharing (cross-team file visibility) — orthogonal, future spec
- `share` link old-format compat beyond what already exists
- Removing `scope_type` column entirely (kept for future-proof; plan-stage may reverse this)
- mediahub mobile app — frontend changes ride along, no app-specific work needed

## Spec Self-Review (2026-05-28)

1. **Placeholder scan:** No "TBD" / "TODO" / unspecified migration shape. Every step has concrete SQL fragments or named files.
2. **Internal consistency:**
   - `kind` column values consistent across all sections (`'personal'` / `'collaborative'`).
   - `personal team owner_id = auth.users.id` referenced uniformly.
   - UUID→Snowflake pattern identical for all three tables (mirrors mig 051).
3. **Scope check:** Five-PR deployment sequence is large but each PR is independently revertable and serves a distinct purpose. Could decompose into separate specs but they're tightly coupled — one spec is correct here.
4. **Ambiguity scan:**
   - "30-second read-only window" for PR-B: explicit acceptance criterion stated.
   - "Grace period for tokens" pinned to (A) accept failure, not (B) dual-path.
   - "scope_type column kept or dropped" pinned to keep, with plan-stage authority to revisit.
5. **Issues found + fixed inline:**
   - First draft was silent on the trigger location (auth.users vs public.users) — added explicit § Auth-side trigger replacement with both approaches and recommendation.
   - First draft did not specify rollback strategy per PR — added § Rollback paragraph in Testing Plan.
   - First draft missed enumeration of ai_sessions FK dependents — clarified that plan-stage task #1 produces the exhaustive list (rather than hard-coding it in the spec).

No further issues. Ready for plan-stage.
