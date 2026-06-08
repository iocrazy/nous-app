# Resources Scope Activation — Implementation Plan (epic A)

> First production opt-in of the app-layer tenant-scope choke point (`app/db/scope.py`).
> Branch off the just-merged master (#506). Executes subagent-driven (spec + quality review per task).
> Design target: 100k users. Decisions settled 2026-06-05 with the user.

## Goal
Make `resources` the first **enforced** scoped table: `UserScoped(creator_id)` actually injects `creator_id == scope.user_id` on reads and forbids cross-user writes, **replacing today's inconsistent manual scoping** (some repo methods take `creator_id`, many — `get_resource_by_id`/`update_resource`/`delete_resource` — have NO scope and return/mutate any row by id). Fail-closed by construction.

## Settled decisions
- **Scope-threading = entry ambient (D1).** A FastAPI generator dependency builds `Scope(user_id)` from `AuthContext.user_id` and sets the `_scope` ContextVar for the request duration; repo methods keep using `read_scope()/write_scope()` (which do NOT touch `_scope`), so the choke point reads the request-ambient scope. DBOS workflows that touch resources set `_scope` at task entry from the payload `user_id`. No explicit `Scope` param threading.
- **Resources = UserScoped(creator_id) single-axis.** Per decisions §7.2 resources is NOT team/project scoped → Scope only needs `user_id`. **No membership resolution needed** (deferred to B-class tables in later phases).
- **Rollout = cover-all-callers-then-mixin + temporary flag (D4).** Env flag `SCOPE_ENFORCE_RESOURCES` (default false). The `UserScoped` mixin is ALWAYS on the `Resources` model (stable class hierarchy), but the choke point ENFORCES resources only when the flag is on. Off = fully legacy/inert. Audit + cover every caller first → dev-verify green → flip in prod → delete flag within 2 weeks.

## Key mechanism: flag-gated enforcement (refactor the choke point's "what is scoped")
Today `scope.py` decides "is this table scoped?" via `issubclass(cls, UserScoped/TeamScoped/ProjectScoped)` (in `_scoped_mappers` / `_scoped_table_names`). Once `Resources` mixes in `UserScoped`, that check fires regardless of any flag. So introduce a single **enforced-set** gate: `_enforced_scoped_tables()` returns the scoped tables whose per-table enforcement flag is ON. ALL choke-point logic (the `_walk_scoped_refs` table set, `_scoped_mappers`, the write-forbid, the SELECT traversal/inject) keys off the ENFORCED set, not raw `issubclass`. When `SCOPE_ENFORCE_RESOURCES=false`, `resources` is absent from the enforced set → zero enforcement, zero raise (legacy). When true → full deny-by-default enforcement. Inert guarantee preserved: with no flags on, the enforced set is empty → existing short-circuit.

## Tasks (subagent-driven, each spec+quality reviewed)

### A1 — Foundational: flag-gated enforcement + ambient request-scope entry + mix in (gated)
- Add `SCOPE_ENFORCE_RESOURCES: bool = False` to `config.py`.
- Refactor `scope.py` so enforcement keys off a flag-aware **enforced-set** (`_enforced_scoped_tables()` filtered by per-table flags), not raw `issubclass`. Keep all 5-leak-class deny-by-default behavior intact for ENFORCED tables; non-enforced scoped-mixin tables behave exactly as unscoped (legacy). Re-run the full `test_scope_chokepoint.py` (the test models there must still enforce — give them an always-on test flag, or have the enforced-set treat the test Base's mixin classes as always-enforced).
- Add the **request-scope entry**: `app/db/scope.py::request_scope(scope)` (an async contextmanager that sets/resets `_scope`, like `user_session` but session-less) + a FastAPI generator dependency `scoped_request_dep(auth: AuthDep)` (in `app/core/deps.py` or a new `app/core/scope_dep.py`) that builds `Scope(user_id=auth.user_id)` and sets the ambient scope for the request. + a DBOS-entry helper to set scope from a workflow payload.
- Mix `UserScoped` (`__tenant_user_col__="creator_id"`) into the `Resources` model (`app/models/library.py` or wherever Resources lives). It stays INERT because `SCOPE_ENFORCE_RESOURCES` defaults false.
- Tests (integration, dev PG): flag OFF → resources queries unscoped (legacy, no raise, cross-user visible); flag ON + ambient scope set → reads return only own rows, cross-user write forbidden; flag ON + no scope → `UnscopedQueryError` (fail-closed). Prove the 5-leak-class protections apply to resources when enforced (port the key adversarial shapes onto the real Resources model).

### A2 — Audit + route ALL resources-repo callers
- Enumerate every caller of `ResourcesRepository*` methods (HTTP routers, services, workflows, sweepers, the media downloader that writes resources, `retry_failed_downloads`, admin).
- User-facing HTTP routers → add `scoped_request_dep` (ambient user scope).
- System / cross-user paths (sweepers, downloader-on-behalf-of-user, admin, DBOS workflows) → wrap in `system_session(reason=...)` OR set scope to the acting user (`request_scope(Scope(user_id))`) where it acts for one user. Decide per-caller; document each.
- Goal: with `SCOPE_ENFORCE_RESOURCES=true`, NO resources access path hits the fail-closed raise. Add a dev integration pass exercising the main flows under the flag.

### A3 — scoped_sql for the 18 `text()` paths (Decision #2 / C4)
- The choke point's ORM event does NOT see `text()`. Audit the ~18 `text()` statements in `resources_repository_orm.py`: which already carry `creator_id` in the WHERE (manually scoped → wrap/annotate), which don't (must be scoped or routed to `system_session`).
- Build `app/db/scope.py::scoped_sql(scope, sql, params)` — forces a scope arg, binds the tenant filter param, for raw SQL on scoped tables. Route the resources text() reads through it (or convert simple ones to ORM). Crown-jewel RLS is NOT on resources, so this is the only backstop for the text() paths.
- Strengthen the CI guard: flag bare `text()` referencing the `resources` table outside `scoped_sql` (once enforced).

### A4 — Remove redundant manual `WHERE creator_id` + regression + flip readiness
- Once the choke point covers a path, the manual `WHERE creator_id` / explicit `creator_id` args become redundant. Remove carefully (keep signatures interface-stable where callers still pass it; the arg can become ignored/asserted-equal). Audit indirect consumers (the dropping-column lesson).
- Full regression: flag OFF == today; flag ON == fail-closed tenant isolation end-to-end (own-only reads, cross-user write/PK-get blocked, the 5 leak classes closed on real resources).
- parsed_media indirect-scope: verify media reads that JOIN resources are injectable (or routed) under the flag.
- Readiness checklist for the prod flip (separate ops step, not in the PR).

## A2 EXECUTION — audit done 2026-06-05, all decisions settled

**Caller inventory (corrected):** 28 listed + `app/workflows/scheduled_cleanup.py` (SYSTEM) + `app/api/ai_library_router.py::upload_chat_attachment` (USER); `projects_router.py:434` is a FALSE positive (project_files, exclude). Only the `resources` table is scoped — sibling-table methods (resource_versions/folders/resource_items/resource_tags/parsed_media) are NOT enforced, but wire by entry boundary anyway (paths mix scoped+non-scoped + table set may grow).

**Settled decisions (2026-06-05):**
- **#2 (run_async breaks contextvar) → FIX SHARED HELPERS.** `app/tasks/utils.py::run_async` (ThreadPoolExecutor.submit(asyncio.run) — new thread, no ctx) + `app/services/media/parsers/parse_helpers.py::_run_async` (asyncio.run fresh ctx) must `contextvars.copy_context()` so the outer `_scope` propagates into the thread/loop. One change covers download_helpers (4 chain helpers) + downloader (`_ensure_carousel_resource`) + media_service (`save_metadata_only`).
- **#1 (public/share-token serve endpoints) → `system_request_scope("public-share-serve")`** for the no-auth branches of `serve_resource_cover`/`serve_preview_sprite`/`serve_resource_file`. `serve_resource_file` is hybrid → conditional (authed=ScopedRequestDep, share-token-only=system).
- **#3 (transcode user_id Optional) → None→SYSTEM.** `transcode_workflow`: user_id present → `request_scope(Scope(user_id))`; None (batch/admin) → `system_request_scope("system-transcode")`.

**Wiring passes (all decided; flag stays OFF/inert during all of these):**
1. **Routers** → add `ScopedRequestDep` to the ~14 router endpoints (uniform `auth.user_id`). The 3 public endpoints in `resources_crud_router` use `system_request_scope` per #1 (conditional for serve_resource_file). `media_fetch_helpers.handle_media_fetch_dispatch` inherits its endpoint's scope (no own wiring). `dedup_and_dispatch` touches no resources (only enqueues) — skip.
2. **Direct DBOS workflows** (clean required user_id, no run_async in the resources call) → `request_scope(Scope(user_id))`: `download.finalize_post_download_step`, `soda_download_workflow`, `soda_ugc_download_workflow`.
3. **Sweepers / cross-user** → `system_request_scope(reason)`: `temp_resource_sweeper.sweep_temp_resources`, `scheduled_cleanup.cleanup_trashed_resources_step` + `cleanup_orphan_storage_step`.
4. **run_async-boundary** (after #2 helper fix): `download_helpers` (4 helpers), `downloader._ensure_carousel_resource`, `media_service.save_metadata_only` — set `request_scope(Scope(user_id))` at the boundary; the copy_context fix makes it propagate through run_async.
5. **transcode/thumbnail** (per #3): `transcode_workflow` user_id→USER / None→SYSTEM; `transcode_service`/`thumbnail_service` inherit ambient (no own wiring, operate by resource_id/version_id).
- **Services** (`resources_service`, `chat_upload`, `downloader`, `thumbnail_service`, `transcode_service`) get NO own wiring — inherit the ambient scope set by passes 1-5.

**A2.5 — REQUIRED repo-refactor BEFORE the flag can flip (ESCALATE #4, NOT caller-wiring):** the `resources_repository_orm.py` write/count methods are INCOMPATIBLE with the hardened choke point under a real user Scope:
- `create_resource` (Core `insert(Resources)`), `update_resource` (Core `update`), `delete_resource` (Core `delete`) → `_forbid_scoped_bulk_dml` RAISES → refactor to sanctioned **load-then-modify** (`session.add(Resources(...))` / load-instance-mutate / `session.delete(instance)`).
- `count_resources_by_media_id` (`func.count()` no scoped column) → deny-by-default SELECT RAISES → project a scoped column or restructure.
- `get_owned_platform_ids` / raw `text()` reads → BYPASS the choke point → route through A3 `scoped_sql` (or convert to ORM).
This is the deny-by-default forbid biting our own 5.2 repo (written before the hardening). Do it (likely folded with A3) before flipping.

## Constraints / invariants
- Reuse the hardened `scope.py` (do not regress the 5-leak-class deny-by-default). `get_engine()` singleton. No Alembic. Default-off flag → instant rollback.
- Interface-stable repo signatures (106 importers). Inert guarantee: with all enforcement flags off, zero behavior change.
- Run CI-parity lint before every push: `black + isort + flake8` (NOT ruff) on changed files (xargs; `--diff-filter=ACMR`).
- DB tests vs dev stack (`/tmp/orm2_integration.env`), never prod writes.

## PR
Branch `feature/resources-scope-activation` off master. One PR (or A1 infra + A2-A4 as a second) — decide at task time. Flag default-off → safe to merge before the prod flip.
