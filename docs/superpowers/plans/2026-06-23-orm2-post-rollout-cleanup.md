# ORM 2.0 Post-Rollout Cleanup Plan (49-domain legacy retirement)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. This is a LARGE, delicate prod-data-layer refactor (49 domains). Execute in a dedicated worktree, batch by risk, one domain (or a small batch) per PR, NOT all at once. Do NOT execute the whole thing in a single marathon-context session.

**Goal:** Retire the legacy (supabase-py REST / asyncpg) data-access path now that ORM 2.0 is the prod default (all 49 `USE_ORM_*` flags forced true via prod `.env`). Make ORM the *only* path, delete the legacy bodies + per-domain feature flag + factory branch, and update the factory baseline tests.

**Current state (grounded 2026-06-23):**
- 49 `USE_ORM_*` flags in `backend/app/core/config.py`, ALL `default=False`. Prod `.env` overrides all to `true` (ORM is live in prod TODAY).
- Per domain X: `X_repository.py` holds the legacy `XRepository` + a `get_X_repository()` factory at the bottom that branches on `settings.USE_ORM_X` (on→`XRepositoryOrm`, off→`XRepository`, with a "flag on but engine missing → legacy" half-configured fallback). `X_repository_orm.py` holds `class XRepositoryOrm(XRepository)` overriding every DB method.
- `test_X_repository_factory.py` asserts: flag-off→legacy instance, flag-on→ORM instance, + public-method-surface parity.
- **Why flipping config defaults to True breaks CI:** the `test_*_repository_factory.py::test_factory_returns_legacy_when_flag_off` cases patch the flag False and assert a legacy instance. Flip the default → that assertion's premise changes only if they rely on the default (they `patch(... , False)` explicitly, so they'd still pass) — BUT the broader baseline is that the whole unit suite runs with defaults (flags False) and expects legacy behavior in countless places. The safe path is NOT "flip defaults" — it's "delete the flag + legacy path so there's only ORM," then fix the now-obsolete factory tests.

**KEY STRUCTURAL FACT:** `XRepositoryOrm` *subclasses* `XRepository` and overrides DB methods. So "delete legacy" is a **class collapse**, not a deletion: the ORM overrides must become the repo's only DB methods. Per domain this is delicate — do it carefully, one domain at a time, verifying the collapsed class exposes the same public surface.

## Per-domain recipe (apply 49×, one domain or small batch per PR)

For domain `X`:

1. **Confirm ORM covers legacy.** Read `X_repository.py` (legacy methods) + `X_repository_orm.py` (overrides). Confirm `XRepositoryOrm` overrides EVERY DB method the legacy class has (the factory parity test already asserts this — run it). Note any legacy method NOT overridden (those inherit legacy bodies via MRO and must be ported or consciously kept).
2. **Collapse.** Make the ORM implementation the sole repo. Two viable shapes — pick per domain:
   - (a) Move the ORM method bodies INTO `XRepository` (replacing the legacy bodies), delete `X_repository_orm.py`, OR
   - (b) Keep `XRepositoryOrm` as the class but have `get_X_repository()` always return it + delete the legacy bodies it no longer needs. (a) is cleaner long-term; (b) is a smaller diff. Prefer (a) unless the legacy class has non-DB helpers worth keeping.
3. **De-flag.** Delete `USE_ORM_X` from `config.py`. Delete the `if settings.USE_ORM_X` branch in `get_X_repository()` (factory now unconditionally returns the ORM-backed repo).
4. **Fix tests.** Delete/rewrite `test_X_repository_factory.py`'s `flag_off→legacy` + `flag_on→ORM` cases (no flag anymore). KEEP the public-method-surface + behavior tests (retarget at the collapsed class). Grep for other tests that `patch("...USE_ORM_X", ...)` and update them.
5. **Verify.** `uv run pytest tests/ -k "X"` green; the per-domain integration test (if any, `tests/integration/test_X_repository_orm.py`) green; lint (black/isort/flake8).
6. **Ship.** One domain (or a small same-risk batch) per PR. Prod behavior is UNCHANGED (prod already runs ORM via `.env`), so each PR is low-risk — but verify the prod `.env` still works (the flag deletion means the `.env` `USE_ORM_X=true` line becomes a no-op env var, harmless; remove those lines from prod `.env` in a final pass).

## Batching by risk (suggested order)
1. **Pilot (1 domain, low-stakes, self-contained):** start with a peripheral domain to validate the recipe end-to-end before touching core. Candidates: `USE_ORM_STYLE_TEMPLATES`, `USE_ORM_TAG_PREFERENCES`, `USE_ORM_COLLECTIONS`. (Avoid `COOKIES`/`PAYMENT`/`API_KEY` for the pilot — secrets/money.)
2. **Peripheral batch:** style/tags/collections/notifications/style — low traffic.
3. **Admin batch** (`USE_ORM_ADMIN_*`, ~16 flags) — admin-only surface, lower blast radius than user-facing.
4. **Core user-facing** (`MEDIA`, `RESOURCES`, `AGENT_RUNS`, `USER_SETTINGS`, `PROJECTS`, `STORYBOARD`, `AI`, `ISSUE`, `TEAM`, `POINTS`, `PAYMENT`) — last, one per PR, most care.
5. **Final pass:** delete `SCOPE_ENFORCE_RESOURCES` flag (separate concern — confirm it's fully rolled out first); remove the now-no-op `USE_ORM_*=true` lines from prod `.env`.

## Hard constraints
- **Do NOT flip `config.py` defaults to True as a shortcut** — delete the flag entirely (the warning in memory is about the half-measure of flipping defaults while legacy + factory tests still exist).
- One domain / small batch per PR. Each PR: prod-behavior-neutral (ORM already live), green CI, lint clean.
- CI `Reflect prod & diff ORM models` must stay green (no model changes — this is repo/flag deletion only).
- black+isort+flake8 (NOT ruff); loguru `{}`.
- Worktree-isolated (parallel sessions active); check migration-number collisions if any migration is added (none expected — this is code-only).

## NOT in scope
- No schema migrations (code + flag + test only).
- `SCOPE_ENFORCE_RESOURCES` is a separate rollout concern — only delete in the final pass after confirming enforcement is fully on in prod.

## Why this is staged, not one-shot
49 domains × class-collapse + de-flag + test-fix = high blast radius on the prod data-access layer. The performance guidance (avoid large refactors in deep context) + the prod-data-layer stakes mean this must be batched across PRs/sessions, each independently reviewable + shippable. Prod is already on ORM, so there is NO urgency that justifies a risky big-bang.
