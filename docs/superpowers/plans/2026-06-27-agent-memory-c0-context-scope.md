# Agent Memory — Phase C0: context-scoped consolidation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `/dream` consolidation record each memory's **source context** (the session's `team_id` / `project_id`) and set the row's `scope` accordingly, so Phase C can later promote a private memory to *shared within its own scope* ("延续" — team session → team-shared, project session → project-shared, personal → never shareable). Still **private-only writes**; this only adds context to the rows.

**Architecture:** Phase B consolidated per `(user, agent)` across all recent sessions and wrote `scope='agent_user'`, `team_id/project_id = NULL`. C0 enumerates per `(user, agent, team_id, project_id)` (one consolidation unit per source context), derives `scope` = `project` (if project_id) → `team` (if team_id) → `agent_user`, and records `team_id`/`project_id` on the row. Fingerprints become context-scoped so the same topic in two contexts stays distinct.

**Tech Stack:** SQLAlchemy/asyncpg + DBOS workflow + FastAPI; pytest. Backend lint = black + isort + flake8 (NOT ruff); loguru `{}`/f-strings.

## Global Constraints

- **Still private-only.** `write_memory_row` keeps `visibility='private'` hardcoded; C0 only adds `team_id`/`project_id`/`scope` context. No shared writes (that's C1).
- **Behaviour-equivalent for personal context.** A session with neither team nor project still produces `scope='agent_user'`, `team_id/project_id = NULL` — same as Phase B. Only team/project sessions change.
- **NULL-safe context matching.** The message-load SQL matches the context with `IS NOT DISTINCT FROM` (so a NULL team_id context loads NULL-team sessions, not zero rows).
- **Context-scoped dedup.** `make_fingerprint` includes scope + context id, and `existing_fingerprints` filters by the same context, so promoting/deduping never crosses contexts.
- Snowflake BIGINT ids as str → bind ints where the column is BIGINT. loguru `{}`/f-strings; lint black+isort+flake8.

---

## File Structure

| File | Change |
|------|--------|
| `backend/app/repositories/agent_memory_repository.py` | `write_memory_row` gains `team_id`/`project_id`; `_INSERT_SQL` writes them; `existing_fingerprints` gains context filter |
| `backend/app/services/ai/memory/agent_memory_consolidation.py` | `make_fingerprint` gains `scope`/`scope_id`; `consolidate_pair` threads them |
| `backend/app/workflows/consolidate_agent_memory.py` | enumerate per context; `_consolidate_pair` → `_consolidate_context` (scope derivation + context-scoped write/dedup) |
| `backend/app/api/admin/settings_router.py` | manual trigger consolidates all of a pair's contexts |
| `backend/app/schemas/admin.py` | `ConsolidateResponse` gains `contexts` count (optional) |
| tests: `test_agent_memory_consolidation.py`, `test_consolidate_workflow.py` (extend) | context fingerprint + scope derivation + context-scoped enumerate |

---

### Task 1: Context on the write + dedup + fingerprint

**Files:**
- Modify: `backend/app/repositories/agent_memory_repository.py`
- Modify: `backend/app/services/ai/memory/agent_memory_consolidation.py`
- Test: extend `backend/tests/memory/test_agent_memory_consolidation.py`

**Interfaces:**
- Produces:
  - `write_memory_row(*, owner_user_id, agent_id, scope, kind, title, body_md, when_to_use, fingerprint, team_id: Optional[int] = None, project_id: Optional[int] = None) -> bool` — INSERT now includes `team_id`, `project_id`. Still `'private'`.
  - `existing_fingerprints(*, owner_user_id, agent_id, scope, team_id: Optional[int], project_id: Optional[int]) -> set` — filtered to the same context.
  - `make_fingerprint(owner_user_id, agent_id, scope, scope_id, draft) -> str` — SHA1 of `f"{owner}|{agent}|{scope}|{scope_id}|{title}"`. `scope_id` is the context id as str (project_id or team_id or "").

- [ ] **Step 1: Write the failing tests** (append to `test_agent_memory_consolidation.py`):

```python
def test_make_fingerprint_distinguishes_contexts():
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft, make_fingerprint

    d = MemoryDraft(title="Deploy", body_md="x", when_to_use="w", kind="fact")
    personal = make_fingerprint("u1", "a1", "agent_user", "", d)
    team = make_fingerprint("u1", "a1", "team", "10", d)
    project = make_fingerprint("u1", "a1", "project", "55", d)
    # same topic, different contexts → three distinct fingerprints
    assert len({personal, team, project}) == 3


@pytest.mark.asyncio
async def test_write_memory_row_persists_context():
    from app.repositories.agent_memory_repository import write_memory_row

    captured = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["params"] = params

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.write_scope", return_value=_Scope()
    ):
        ok = await write_memory_row(
            owner_user_id="u1", agent_id="a1", scope="team", kind="fact",
            title="t", body_md="b", when_to_use="w", fingerprint="fp",
            team_id=10, project_id=None,
        )
    assert ok is True
    assert captured["params"]["team_id"] == 10
    assert captured["params"]["project_id"] is None
    assert captured["params"]["scope"] == "team"
```

- [ ] **Step 2: Run → fail.** Run: `cd backend && uv run pytest tests/memory/test_agent_memory_consolidation.py -q`

- [ ] **Step 3: Implement.**

`make_fingerprint` (consolidation service) — new signature:

```python
def make_fingerprint(
    owner_user_id: str, agent_id: str, scope: str, scope_id: str, draft: MemoryDraft
) -> str:
    """SHA1 of ``owner|agent|scope|scope_id|normalised_title`` — the dedup unit
    is *topic within a context*, so the same topic in two contexts stays
    distinct (and can be promoted independently)."""
    normalised = draft.title.strip().lower()
    raw = f"{owner_user_id}|{agent_id}|{scope}|{scope_id}|{normalised}"
    return hashlib.sha1(raw.encode()).hexdigest()
```

Update `consolidate_pair` (consolidation service) to take + thread `scope` and `scope_id` into `make_fingerprint`. Its signature gains `scope: str` and `scope_id: str`; the `make_fingerprint(owner_user_id, agent_id, draft)` call becomes `make_fingerprint(owner_user_id, agent_id, scope, scope_id, draft)`.

`agent_memory_repository.py` — `_INSERT_SQL` + `write_memory_row` + `existing_fingerprints`:

```python
_INSERT_SQL = text(
    """
    INSERT INTO public.agent_memory
        (scope, owner_user_id, agent_id, team_id, project_id, visibility, kind,
         title, body_md, when_to_use, fingerprint)
    VALUES
        (:scope, :owner_user_id, :agent_id, :team_id, :project_id, 'private',
         :kind, :title, :body_md, :when_to_use, :fingerprint)
    """
)

_FINGERPRINTS_SQL = text(
    """
    SELECT fingerprint FROM public.agent_memory
    WHERE owner_user_id = :owner_user_id AND agent_id = :agent_id
      AND scope = :scope
      AND team_id IS NOT DISTINCT FROM :team_id
      AND project_id IS NOT DISTINCT FROM :project_id
      AND status = 'active' AND fingerprint <> ''
    """
)
```

`write_memory_row` gains `team_id: Optional[int] = None, project_id: Optional[int] = None` and binds them. `existing_fingerprints` gains `scope: str, team_id: Optional[int], project_id: Optional[int]` and binds them.

> Implementer: keep `visibility='private'` literal in the INSERT (no param). `team_id`/`project_id` are nullable BIGINT — bind as int or None (the private + shared-requires-team_id CHECK allows a private row to carry team_id). `IS NOT DISTINCT FROM` makes the NULL context match correctly.

- [ ] **Step 4: Run → pass.** Update any existing test calling `make_fingerprint`/`existing_fingerprints` with the old signature to the new one (the Phase B tests in this file). Run: `cd backend && uv run pytest tests/memory/test_agent_memory_consolidation.py -q` (all pass). Lint + commit `feat(memory): record source context (team/project) on agent_memory + context-scoped dedup (C0)`.

---

### Task 2: Context-scoped consolidation workflow + manual trigger

**Files:**
- Modify: `backend/app/workflows/consolidate_agent_memory.py`
- Modify: `backend/app/api/admin/settings_router.py`
- Modify: `backend/app/schemas/admin.py`
- Test: extend `backend/tests/memory/test_consolidate_workflow.py`

**Interfaces:**
- Produces:
  - `_ACTIVE_PAIRS_SQL` groups by `(user_id, agent_id, team_id, project_id)` and returns the context columns; `enumerate_active_pairs_step` returns `[{user_id, agent_id, team_id, project_id}, ...]`.
  - `_consolidate_context(user_id, agent_id, team_id: Optional[int], project_id: Optional[int]) -> dict` — derives `scope` (`project` if project_id else `team` if team_id else `agent_user`) + `scope_id` (str of project_id or team_id or ""); loads messages from sessions matching that exact context (NULL-safe); writes via `write_memory_row(..., scope=scope, team_id=team_id, project_id=project_id, ...)`; dedup via `existing_fingerprints(..., scope, team_id, project_id)`.
  - `consolidate_context_step` (`@DBOS.step`) wraps it; the workflow loops contexts.
  - Manual trigger: `_consolidate_pair(user_id, agent_id)` stays as a convenience that enumerates the pair's contexts and consolidates each, summing counts.

- [ ] **Step 1: Write the failing test** — extend `test_consolidate_workflow.py`: a test that `_consolidate_context` with a project context (`project_id=55, team_id=10`) writes via `write_memory_row` with `scope='project'`, `project_id=55`, `team_id=10`; and a context with neither → `scope='agent_user'`, both NULL. (Mock `db_engine.fetch_all` for messages, `existing_fingerprints`, `write_memory_row`, and the consolidator.) Plus a test that `enumerate_active_pairs_step`'s grouping includes the context (mock fetch_all returns rows with team_id/project_id).

- [ ] **Step 2: Run → fail. Step 3: Implement.**
  - `_ACTIVE_PAIRS_SQL`: `SELECT s.user_id::text, s.agent_id::text, s.team_id, s.project_id, count(m.*) AS msg_count ... GROUP BY s.user_id, s.agent_id, s.team_id, s.project_id`.
  - `_RECENT_MESSAGES_SQL`: add `AND s.team_id IS NOT DISTINCT FROM :team_id AND s.project_id IS NOT DISTINCT FROM :project_id`; params include `team_id`, `project_id`.
  - `_consolidate_context(user_id, agent_id, team_id, project_id)`: derive `scope`/`scope_id`; load messages for the context; `existing_fingerprints(owner_user_id=user_id, agent_id=agent_id, scope=scope, team_id=team_id, project_id=project_id)`; `consolidate_pair(..., scope=scope, scope_id=scope_id, ...)`; `write_memory_row(..., scope=scope, team_id=team_id, project_id=project_id, ...)`. Best-effort → zeros.
  - `consolidate_context_step` = `@DBOS.step` wrapping `_consolidate_context`.
  - Workflow: `enumerate_active_pairs_step()` → for each ctx, `await consolidate_context_step(ctx["user_id"], ctx["agent_id"], ctx["team_id"], ctx["project_id"])`.
  - `_consolidate_pair(user_id, agent_id)` (kept for the admin endpoint): query the pair's distinct contexts, call `_consolidate_context` for each, sum `{written, skipped, contexts}`.
  - Manual endpoint + `ConsolidateResponse` gains `contexts: int = 0`.

- [ ] **Step 4: Run → pass** (new + existing consolidate-workflow tests). Bundle import smoke `import app.workflows._scheduled_bundle`. Lint + commit `feat(memory): consolidate per source context (team/project) + manual trigger over contexts (C0)`.

---

### Task 3: Regression + dev-DB validation + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or consolidat or scheduled" -q` → all pass.
- [ ] **Step 2:** lint full changed set (black --check / isort --check-only / flake8). Bundle import smoke.
- [ ] **Step 3 (controller does this — needs the DB):** validate the context-scoped write against a real DB: insert a private row via `write_memory_row(scope='team', team_id=10, ...)` and one with `scope='project', project_id=55`, confirm both land with the right scope/context + the context-scoped `existing_fingerprints` only returns the matching context, then delete. (Runs in the prod backend container like the Phase A/go-live validation.)
- [ ] **Step 4:** PR:

```bash
git push -u origin feature/agent-memory-c0-context-scope
gh pr create --base master --head feature/agent-memory-c0-context-scope \
  --title "feat(memory): context-scoped consolidation — Phase C0 (records source team/project)" \
  --body "C0 of Phase C. /dream now consolidates per (user, agent, source team/project) and records team_id/project_id + the right scope (project>team>agent_user) on each PRIVATE row, with context-scoped fingerprint dedup. Sets up Phase C promotion to share a private memory *within its own scope* (team session→team-shared, project→project-shared, personal→never). Still private-only writes; behaviour unchanged for personal-context sessions."
```

---

## Self-Review

**Spec coverage:** source context recorded on the row (Task 1 write + Task 2 derivation) ✓; context-scoped dedup (Task 1 fingerprint + existing_fingerprints) ✓; per-context consolidation (Task 2 enumerate + _consolidate_context) ✓; still private-only (INSERT literal 'private') ✓; personal-context behaviour-equivalent (scope='agent_user', NULLs) ✓. Promotion (C1) + panel/recall (C2) out.

**Placeholder scan:** none. Task 2 carries interfaces + the exact SQL/derivation; the implementer writes the step bodies mirroring the Phase B shapes already in the file.

**Type consistency:** `make_fingerprint(owner, agent, scope, scope_id, draft)` (Task 1) is called with the same `scope`/`scope_id` `_consolidate_context` derives (Task 2). `write_memory_row(..., team_id, project_id)` params (Task 1) match the values `_consolidate_context` passes (Task 2). `existing_fingerprints(..., scope, team_id, project_id)` filter (Task 1) matches the context the workflow consolidates (Task 2). `IS NOT DISTINCT FROM` used consistently in both `_FINGERPRINTS_SQL` and `_RECENT_MESSAGES_SQL` for NULL-safe context match.
