# AI Library Phase 2 — PR 2.1: Seed Loader Production Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose and patch the Phase 1 production incident where `SeedLoader` silently failed to populate `ai_agents` / `skills` / `skill_files` / `agent_skills` on container startup, forcing manual MCP seed injection. Ship actionable diagnostic logging + an admin recovery endpoint so the next incident is debuggable without SSH-to-NAS archaeology.

**Architecture:** Follow "measure, don't guess" — we do NOT add a speculative retry/backoff fix because the 3 candidate root causes (Supabase cold-start, `__file__` path mismatch, RLS/service-role permission) have different remediations. Instead:

1. Enhance `seed_loader.py` to emit structured diagnostic logs (seeds_root path, existence, item count, per-step success/failure with Supabase error code + status extracted).
2. Add lifespan entry log so we can tell whether the block was even reached.
3. Add `POST /api/v1/ai-library/admin/reload-seeds` (admin-gated) so ops can re-run loader post-deploy without redeploying.
4. Targeted fix (retry w/ backoff, path fallback, RLS bypass, etc.) deferred to follow-up PR 2.1.1 *if* the logs from this PR prove it's needed.

**Tech Stack:** Python 3.13, FastAPI, loguru, Supabase Python SDK (postgrest-py), pytest + pytest-asyncio. Existing test infra in `backend/tests/test_seed_loader.py` (mock-based, no DB).

---

## File Structure

**Modify:**
- `backend/app/services/seed_loader.py` — add diagnostic logging + `load_all()` returns timing/error details instead of silently catching per-step failures
- `backend/app/main.py:63-78` — emit startup entry/exit breadcrumb logs around seed_loader call
- `backend/app/api/ai_library_router.py` — append admin reload-seeds endpoint + reuse `_require_admin` pattern from `supabase_auth_router.py:28-58`
- `backend/tests/test_seed_loader.py` — add tests for new diagnostic code paths

**Create:**
- `backend/tests/test_ai_library_admin_routes.py` — tests for the new reload endpoint

**Do NOT touch (out of scope):**
- `backend/seeds/**` content (already correct)
- `AgentRepository` / `SkillRepository` internals
- `Dockerfile` (paths verified already correct)

---

## Task 1: Add lifespan entry/exit breadcrumb logs

**Why first:** Tells us whether `lifespan()` even reached the seed block in production. Zero-risk change.

**Files:**
- Modify: `backend/app/main.py:63-78`

- [ ] **Step 1: Inspect current lifespan block**

Re-read `backend/app/main.py` lines 63-78 to confirm the current shape:

```python
# Load AI Library seeds (agents + skills) from backend/seeds/
# Wrapped defensively: a seed failure must not block server startup.
try:
    from app.repositories.agent_repository import AgentRepository
    from app.repositories.skill_repository import SkillRepository
    from app.services.seed_loader import SeedLoader

    seed_loader = SeedLoader(
        agent_repo=AgentRepository(),
        skill_repo=SkillRepository(),
        seeds_root=Path(__file__).resolve().parent.parent / "seeds",
    )
    seed_results = await seed_loader.load_all()
    logger.info(f"seed_loader: {seed_results}")
except Exception as e:
    logger.exception(f"seed_loader failed: {e}")
```

- [ ] **Step 2: Modify the block to emit entry + computed path breadcrumbs**

Replace lines 63-78 with:

```python
# Load AI Library seeds (agents + skills) from backend/seeds/.
# Wrapped defensively: a seed failure must not block server startup.
# Breadcrumb logs below are load-bearing for post-incident diagnosis —
# keep the entry/exit pair even if the body is refactored.
seeds_root = Path(__file__).resolve().parent.parent / "seeds"
logger.info(
    f"seed_loader: entering (seeds_root={seeds_root}, "
    f"exists={seeds_root.exists()})"
)
try:
    from app.repositories.agent_repository import AgentRepository
    from app.repositories.skill_repository import SkillRepository
    from app.services.seed_loader import SeedLoader

    seed_loader = SeedLoader(
        agent_repo=AgentRepository(),
        skill_repo=SkillRepository(),
        seeds_root=seeds_root,
    )
    seed_results = await seed_loader.load_all()
    logger.info(f"seed_loader: exiting, results={seed_results}")
except Exception as e:
    logger.exception(f"seed_loader: exiting with exception: {e}")
```

- [ ] **Step 3: Verify import still works**

Run:
```bash
cd backend && uv run python -c "from app.main import lifespan; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(ai): log seed_loader entry + seeds_root existence at lifespan startup

Adds explicit entry/exit breadcrumbs so post-incident log grep can
distinguish (a) lifespan never reached the block, (b) seeds dir missing,
(c) block threw, (d) block succeeded. Zero behavior change."
```

---

## Task 2: Extract a structured error helper

**Why:** Supabase / postgrest errors carry a `code`, `message`, `details`, `hint` on the `APIError` class, but `logger.exception(e)` prints only `str(e)`. We want the structured fields so "RLS denied" vs "network timeout" vs "schema mismatch" is obvious at a glance.

**Files:**
- Modify: `backend/app/services/seed_loader.py`
- Modify: `backend/tests/test_seed_loader.py`

- [ ] **Step 1: Write the failing test for `_format_error` helper**

Append to `backend/tests/test_seed_loader.py`:

```python
# ─── _format_error ────────────────────────────────────────────────────


def test_format_error_plain_exception() -> None:
    """Plain Exception → message only, no code/status."""
    from app.services.seed_loader import _format_error

    err = _format_error(ValueError("boom"))
    assert err["type"] == "ValueError"
    assert err["message"] == "boom"
    assert err.get("code") is None
    assert err.get("status") is None


def test_format_error_postgrest_apierror_shape() -> None:
    """Postgrest-style error with code/message/details/hint → all extracted."""
    from app.services.seed_loader import _format_error

    class _FakePostgrestError(Exception):
        code = "42501"
        message = "new row violates row-level security policy"
        details = "for table ai_agents"
        hint = None

        def __str__(self) -> str:
            return self.message

    err = _format_error(_FakePostgrestError())
    assert err["type"] == "_FakePostgrestError"
    assert err["message"] == "new row violates row-level security policy"
    assert err["code"] == "42501"
    assert err["details"] == "for table ai_agents"


def test_format_error_httpx_status() -> None:
    """Exception with `response.status_code` attr → status extracted."""
    from app.services.seed_loader import _format_error

    class _FakeResp:
        status_code = 503

    class _FakeHttpErr(Exception):
        response = _FakeResp()

    err = _format_error(_FakeHttpErr("service unavailable"))
    assert err["status"] == 503
```

- [ ] **Step 2: Run the tests — expect failures (helper doesn't exist)**

Run:
```bash
cd backend && uv run pytest tests/test_seed_loader.py::test_format_error_plain_exception \
    tests/test_seed_loader.py::test_format_error_postgrest_apierror_shape \
    tests/test_seed_loader.py::test_format_error_httpx_status -v
```
Expected: 3 FAIL with `ImportError: cannot import name '_format_error'`.

- [ ] **Step 3: Implement `_format_error` in seed_loader.py**

Insert after the constants block (after line 17 `SCRIPT_AI_SKILL_SLUGS = [...]`) in `backend/app/services/seed_loader.py`:

```python
def _format_error(exc: BaseException) -> dict[str, Any]:
    """Extract structured fields from a Supabase/postgrest/httpx exception.

    Loguru `logger.exception` already captures the traceback; this helper
    pulls out the diagnostic bits that a log-grep can match on:
    Postgres SQLSTATE code, HTTP status, hint, details.

    Safe on any Exception subclass — missing attrs are simply omitted.
    """
    info: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    for attr in ("code", "details", "hint"):
        v = getattr(exc, attr, None)
        if v is not None:
            info[attr] = v
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            info["status"] = status
    return info
```

- [ ] **Step 4: Run the tests — expect all 3 pass**

Run:
```bash
cd backend && uv run pytest tests/test_seed_loader.py::test_format_error_plain_exception \
    tests/test_seed_loader.py::test_format_error_postgrest_apierror_shape \
    tests/test_seed_loader.py::test_format_error_httpx_status -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/seed_loader.py backend/tests/test_seed_loader.py
git commit -m "feat(ai): add _format_error helper for structured Supabase exception logging

Extracts Postgres SQLSTATE code, HTTP status, hint, details from the
exception types the loader can encounter (APIError, httpx.HTTPStatusError,
plain Exception). Used by next commits to make seed_loader failures
grep-able by root cause."
```

---

## Task 3: Enhance `load_all()` to never hide per-step failures

**Current bug:** `_load_agents` iterates agents and calls `_upsert_agent` which calls repo methods. If one agent insert fails on RLS, the exception bubbles up all the way and the whole `load_all()` aborts — but the caller in `main.py` catches and logs *once*, so we lose which agent / skill failed. We want per-step errors collected into the return dict.

**Files:**
- Modify: `backend/app/services/seed_loader.py`
- Modify: `backend/tests/test_seed_loader.py`

- [ ] **Step 1: Write the failing test — one agent fails, the other inserts**

Append to `backend/tests/test_seed_loader.py`:

```python
# ─── load_all error aggregation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_load_all_aggregates_per_agent_errors(tmp_path: Path) -> None:
    """Agent A inserts OK, agent B fails → load_all returns errors list
    with agent_b entry AND still reports agents=1 counted successfully."""
    (tmp_path / "agents" / "agent_a").mkdir(parents=True)
    (tmp_path / "agents" / "agent_a" / "IDENTITY.md").write_text("a")
    (tmp_path / "agents" / "agent_b").mkdir(parents=True)
    (tmp_path / "agents" / "agent_b" / "IDENTITY.md").write_text("b")

    agent_repo = _make_agent_repo(get_by_slug_result=None)

    # First insert succeeds, second raises
    call_count = {"n": 0}

    async def _flaky_get_client():
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated RLS denial")

        class _OK:
            def table(self, _):
                class _Q:
                    def insert(self, _row):
                        return self

                    async def execute(self):
                        class _R:
                            data = [{"id": str(uuid4())}]

                        return _R()

                return _Q()

        return _OK()

    agent_repo._get_client = _flaky_get_client  # type: ignore[method-assign]
    skill_repo = _make_skill_repo()

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    results = await loader.load_all()

    assert results["agents"] == 1
    assert "errors" in results
    agent_errors = [e for e in results["errors"] if e["scope"] == "agent"]
    assert len(agent_errors) == 1
    assert agent_errors[0]["slug"] == "agent_b"
    assert agent_errors[0]["error"]["type"] == "RuntimeError"
    assert "RLS" in agent_errors[0]["error"]["message"]
```

- [ ] **Step 2: Run — expect FAIL (no `errors` key yet)**

Run:
```bash
cd backend && uv run pytest tests/test_seed_loader.py::test_load_all_aggregates_per_agent_errors -v
```
Expected: FAIL with `KeyError: 'errors'` or similar.

- [ ] **Step 3: Refactor `_load_agents` + `_load_skills` + `load_all` to collect errors**

In `backend/app/services/seed_loader.py`, modify `load_all` (lines 33-41):

```python
async def load_all(self) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    agents_loaded = await self._load_agents(errors)
    skills_loaded = await self._load_skills(errors)
    bindings_set = await self._bind_script_ai_skills(errors)
    return {
        "agents": agents_loaded,
        "skills": skills_loaded,
        "agent_skill_bindings": bindings_set,
        "errors": errors,
    }
```

Modify `_load_agents` (lines 45-57) to accept `errors` and catch per-iteration:

```python
async def _load_agents(self, errors: list[dict[str, Any]]) -> int:
    agents_dir = self.seeds_root / "agents"
    if not agents_dir.exists():
        logger.warning(f"seed_loader: agents dir missing ({agents_dir})")
        return 0
    count = 0
    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        slug = agent_dir.name
        try:
            fields = self._read_agent_fields(agent_dir, slug)
            await self._upsert_agent(slug, fields)
            count += 1
        except Exception as e:
            err = {"scope": "agent", "slug": slug, "error": _format_error(e)}
            errors.append(err)
            logger.exception(f"seed_loader: agent '{slug}' failed: {err['error']}")
    return count
```

Modify `_load_skills` (lines 94-126) similarly:

```python
async def _load_skills(self, errors: list[dict[str, Any]]) -> int:
    skills_dir = self.seeds_root / "skills"
    if not skills_dir.exists():
        logger.warning(f"seed_loader: skills dir missing ({skills_dir})")
        return 0
    count = 0
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        slug = skill_dir.name
        try:
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                logger.warning(f"seed_loader: skipping {slug} (no SKILL.md)")
                continue
            parsed = frontmatter.load(skill_md_path)
            fm = dict(parsed.metadata)
            body = parsed.content

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

            skill_id = await self._upsert_skill(slug, fields)
            await self._load_skill_subfiles(skill_id, skill_dir)
            count += 1
        except Exception as e:
            err = {"scope": "skill", "slug": slug, "error": _format_error(e)}
            errors.append(err)
            logger.exception(f"seed_loader: skill '{slug}' failed: {err['error']}")
    return count
```

Modify `_bind_script_ai_skills` (lines 172-184) similarly:

```python
async def _bind_script_ai_skills(self, errors: list[dict[str, Any]]) -> int:
    try:
        agent = await self.agent_repo.get_by_slug("script_ai")
        if not agent:
            logger.info("seed_loader: script_ai agent not found, skipping bindings")
            return 0
        skill_ids: list[int] = []
        for slug in SCRIPT_AI_SKILL_SLUGS:
            sk = await self.skill_repo.get_by_slug(slug)
            if sk:
                skill_ids.append(int(sk["id"]))
        await self.agent_repo.update_skill_bindings(UUID(agent["id"]), skill_ids)
        logger.info(f"seed_loader: bound {len(skill_ids)} skills to script_ai")
        return len(skill_ids)
    except Exception as e:
        err = {"scope": "binding", "slug": "script_ai", "error": _format_error(e)}
        errors.append(err)
        logger.exception(f"seed_loader: script_ai binding failed: {err['error']}")
        return 0
```

- [ ] **Step 4: Run the new test — expect PASS**

Run:
```bash
cd backend && uv run pytest tests/test_seed_loader.py::test_load_all_aggregates_per_agent_errors -v
```
Expected: PASS.

- [ ] **Step 5: Run the full test_seed_loader suite — expect all existing tests still pass**

Run:
```bash
cd backend && uv run pytest tests/test_seed_loader.py -v
```
Expected: all tests PASS (the earlier tests call `_load_agents` / `_load_skills` / `_bind_script_ai_skills` directly — they'll need the `errors` arg added; update those call sites.)

If failures: update the 6 existing test call sites (search for `loader._load_agents()`, `loader._load_skills()`, `loader._bind_script_ai_skills()`) to pass a fresh `[]` list. Example:

```python
errors: list = []
count = await loader._load_agents(errors)
```

Then re-run.

- [ ] **Step 6: Type annotation update**

The `load_all()` return type signature at `backend/app/services/seed_loader.py:33` used to be `dict[str, int]`. Change it to `dict[str, Any]` since `errors` is a list. `Any` is already imported from `typing`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/seed_loader.py backend/tests/test_seed_loader.py
git commit -m "feat(ai): collect per-item errors in seed_loader instead of aborting on first failure

Prior behavior: first RLS denial / network blip aborted the entire seed
batch and surfaced one generic exception. New behavior: each agent /
skill / binding is try-wrapped, and load_all() returns an errors[] list
with scope/slug/error-fields alongside success counts.

This is the load-bearing diagnostic change — tells us exactly which
rows Supabase rejected and why, without adding speculative retry logic."
```

---

## Task 4: Admin reload-seeds endpoint

**Why:** When Phase 1 shipped, ops had to open Supabase MCP and hand-write 5 rows. That's fragile and slow. An admin-gated POST endpoint lets us re-trigger `load_all()` from anywhere (curl / Postman / admin UI) without restarting the container.

**Files:**
- Modify: `backend/app/api/ai_library_router.py`
- Create: `backend/tests/test_ai_library_admin_routes.py`

- [ ] **Step 1: Re-read the existing admin-guard pattern**

Confirm the pattern we'll mirror in `backend/app/api/supabase_auth_router.py:28-58`:
- Dependency function `_require_admin(auth: AuthDep)` checks `team_members.role IN ('admin', 'owner')` via service-role client
- Raises `HTTPException(403)` on failure
- Raises `HTTPException(403)` on unexpected error (fail-closed)

We will copy this helper into `ai_library_router.py` as a local helper (not extracted to shared util yet — YAGNI).

- [ ] **Step 2: Write the failing test — admin can reload, non-admin gets 403**

Create `backend/tests/test_ai_library_admin_routes.py`:

```python
"""Tests for the admin-gated reload-seeds endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


@pytest.fixture
def fake_auth():
    """A fake AuthContext-like object the endpoint can read user_id from."""

    class _FakeAuth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "admin@example.com"

    return _FakeAuth()


def test_reload_seeds_rejects_non_admin(client: TestClient, fake_auth):
    """Authenticated user without admin role → 403."""
    app = client.app

    # AuthDep = Annotated[AuthContext, Depends(get_auth)] — override the
    # inner `get_auth` callable, not the AuthDep alias.
    from app.core.deps import get_auth

    async def _override_auth():
        return fake_auth

    app.dependency_overrides[get_auth] = _override_auth

    # Mock the admin role check to return "no admin row"
    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        new=AsyncMock(return_value=_FakeClient(admin_rows=[])),
    ):
        resp = client.post("/api/v1/ai-library/admin/reload-seeds")
        assert resp.status_code == 403
        assert "Admin" in resp.json()["detail"]

    app.dependency_overrides.clear()


def test_reload_seeds_runs_loader_for_admin(client: TestClient, fake_auth):
    """Admin user → SeedLoader.load_all() called, results returned as JSON."""
    app = client.app

    from app.core.deps import get_auth

    async def _override_auth():
        return fake_auth

    app.dependency_overrides[get_auth] = _override_auth

    fake_results = {
        "agents": 1,
        "skills": 3,
        "agent_skill_bindings": 3,
        "errors": [],
    }

    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        new=AsyncMock(return_value=_FakeClient(admin_rows=[{"role": "admin"}])),
    ), patch(
        "app.api.ai_library_router.SeedLoader"
    ) as mock_loader_cls:
        mock_loader_cls.return_value.load_all = AsyncMock(return_value=fake_results)
        resp = client.post("/api/v1/ai-library/admin/reload-seeds")
        assert resp.status_code == 200
        assert resp.json() == fake_results
        mock_loader_cls.return_value.load_all.assert_awaited_once()

    app.dependency_overrides.clear()


class _FakeClient:
    """Minimal fake supabase client that returns a pre-baked team_members result."""

    def __init__(self, admin_rows: list) -> None:
        self._admin_rows = admin_rows

    def table(self, _name):
        return self

    def select(self, _cols):
        return self

    def eq(self, _k, _v):
        return self

    def in_(self, _k, _v):
        return self

    def limit(self, _n):
        return self

    async def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self._admin_rows
        return r
```

- [ ] **Step 3: Run — expect FAIL (endpoint doesn't exist)**

Run:
```bash
cd backend && uv run pytest tests/test_ai_library_admin_routes.py -v
```
Expected: FAIL with 404 on the POST (endpoint not registered).

- [ ] **Step 4: Add the endpoint to ai_library_router.py**

Append to the bottom of `backend/app/api/ai_library_router.py` (after the last endpoint definition):

```python
# ---------------------------------------------------------------------------
# Admin: reload seeds from disk
# ---------------------------------------------------------------------------


from pathlib import Path  # noqa: E402 — module top imports limited by Phase 1 pattern

from app.db.supabase_client import get_async_supabase_admin  # noqa: E402
from app.services.seed_loader import SeedLoader  # noqa: E402


async def _require_admin(auth: AuthDep) -> None:
    """Gate admin endpoints to users with admin/owner role in team_members.

    Mirrors the check in ``supabase_auth_router._require_admin``. Fails
    closed (returns 403) on any lookup error so a broken DB never
    accidentally grants admin privileges.
    """
    try:
        client = await get_async_supabase_admin()
        result = (
            await client.table("team_members")
            .select("role")
            .eq("user_id", auth.user_id)
            .in_("role", ["admin", "owner"])
            .limit(1)
            .execute()
        )
        if not result.data:
            logger.warning(
                f"AI Library admin endpoint denied for user {auth.user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI Library admin role check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


@router.post("/admin/reload-seeds", response_model=Dict[str, Any])
async def reload_seeds(auth: AuthDep) -> Dict[str, Any]:
    """Re-run SeedLoader.load_all() against backend/seeds/.

    Admin-only. Returns the same dict shape as the startup path:
    ``{"agents": N, "skills": M, "agent_skill_bindings": K, "errors": [...]}``.

    Intended for recovery after a startup seed failure — see
    ``docs/superpowers/plans/2026-04-21-ai-library-phase2-seed-loader-fix.md``.
    """
    await _require_admin(auth)
    agent_repo, skill_repo = _repos()
    # Path math: this file is at backend/app/api/ai_library_router.py,
    # so .parent.parent.parent / "seeds" points at backend/seeds/.
    seeds_root = Path(__file__).resolve().parent.parent.parent / "seeds"
    logger.info(f"reload_seeds: invoked by {auth.user_id}, seeds_root={seeds_root}")
    loader = SeedLoader(
        agent_repo=agent_repo,
        skill_repo=skill_repo,
        seeds_root=seeds_root,
    )
    results = await loader.load_all()
    logger.info(f"reload_seeds: completed, {results}")
    return results
```

- [ ] **Step 5: Run — expect PASS**

Run:
```bash
cd backend && uv run pytest tests/test_ai_library_admin_routes.py -v
```
Expected: 2 PASS.

- [ ] **Step 6: Run the full AI Library test suite**

Run:
```bash
cd backend && uv run pytest tests/test_ai_library_routes.py tests/test_ai_library_admin_routes.py tests/test_seed_loader.py -v
```
Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/ai_library_router.py backend/tests/test_ai_library_admin_routes.py
git commit -m "feat(ai): add POST /ai-library/admin/reload-seeds endpoint

Admin-gated (team_members.role in admin/owner), calls
SeedLoader.load_all() and returns the aggregated results dict.

Recovery mechanism for the Phase 1 startup-seed failure so ops can
re-trigger seed population without redeploying the container."
```

---

## Task 5: Manual production diagnostic run

**Why last:** This is not a code task — it's the post-merge verification that confirms the plan's premise ("we added logs, now we can see what went wrong"). Record findings in the commit message of the follow-up PR 2.1.1 if a targeted fix is needed.

**Files:** none (this is ops work)

- [ ] **Step 1: After PR merges + deploy completes, SSH NAS and grep logs**

Run on NAS (user's terminal — you cannot do this for them, print the commands they should run):

```bash
ssh user@nas-host -p 2222
cd /volume1/docker/mediahub/docker
sudo docker compose logs --tail=200 backend | grep -i seed_loader
```

Expected output cases:
1. **"seed_loader: entering"** absent → lifespan never reached the block (check earlier startup errors — import failure? DrissionPage init hang?)
2. **"seed_loader: entering (..., exists=False)"** → container copy dropped the seeds dir. Re-check `Dockerfile` `COPY backend/ .` and `.dockerignore`.
3. **"seed_loader: exiting, results={..., errors: [...]}"** with errors populated → inspect the `error.code` / `error.status`:
   - `code=42501` ("new row violates row-level security policy") → service-role client is not bypassing RLS; check `SUPABASE_SERVICE_ROLE_KEY` env var in docker-compose
   - `status=503` / `type=ConnectError` → Supabase cold-start race; follow-up PR adds retry with backoff
   - `code=23505` ("duplicate key value") → upsert logic has a race or slug collision
4. **"seed_loader: exiting with exception"** (the outer catch triggered) → `_format_error` output tells us the type. Most likely import-time failure.

- [ ] **Step 2: Trigger manual reload if seeds still empty**

Using an admin user's JWT (grab from browser devtools on the deployed app):

```bash
curl -X POST https://mediahubserver.heygo.cn:88/api/v1/ai-library/admin/reload-seeds \
  -H "Authorization: Bearer <JWT>"
```

Expected: JSON response with `errors: []` → seeds loaded on demand; confirms the code path works and the startup race is environmental, not a code bug.

- [ ] **Step 3: Based on findings, open follow-up ticket / PR 2.1.1 if needed**

If root cause is now-known:
- Startup race → PR 2.1.1 adds `_await_supabase_ready()` with 3 × 2s/5s/10s backoff before `load_all()`.
- Path mismatch → PR 2.1.1 fixes `Dockerfile` or seeds_root fallback.
- RLS → PR 2.1.1 audits `get_async_supabase_admin()` and env-var plumbing.
- Seeds fine on reload but still broken on next deploy → file issue; don't speculate further.

If this step finds the seeds ARE loading successfully now (the manual MCP injection from Phase 1 made subsequent restarts idempotent-no-op): close the loop, no follow-up PR needed.

---

## Self-Review Checklist

Run through this after completing all tasks — checkbox it as you verify each:

- [ ] Every task has actual code, no `TBD` / `TODO` / `similar to above` placeholders
- [ ] `_format_error` is defined before `load_all()` references it (ordering within the file)
- [ ] `load_all()`'s new return type `dict[str, Any]` propagates to `main.py`'s `f"seed_loader: exiting, results={seed_results}"` (still renders fine — f-string on dict works)
- [ ] Existing tests updated for new `_load_agents(errors)` / `_load_skills(errors)` / `_bind_script_ai_skills(errors)` signatures
- [ ] New admin endpoint's path math (`__file__` → `parent.parent.parent / "seeds"`) matches container layout (router file is at `backend/app/api/…`, which is 3 levels deep from `backend/`)
- [ ] Full test suite `uv run pytest tests/ -v` shows zero regressions — not just the seed-specific tests
- [ ] `grep -r 'logger.exception' backend/app/services/seed_loader.py` returns exactly 3 hits (one per scope: agent, skill, binding)
- [ ] `grep 'reload-seeds' backend/app/api/` shows the endpoint is findable
- [ ] Commit messages are imperative + include the "why", not just "what"

## Ship Checklist (after Task 5)

- [ ] Run `/ship` — cuts the feature branch, merges base, runs tests, creates PR to master
- [ ] PR title: `feat(ai): seed loader diagnostic logging + admin reload endpoint (Phase 2 PR 2.1)`
- [ ] PR body: link this plan file, paste the 4 "expected output cases" from Task 5 so reviewer knows how to interpret post-deploy logs
- [ ] After merge: execute Task 5 (SSH + curl) — record findings in `project_ai_library_plan.md` memory so Phase 2 PR 2.2 starts with this data
