# Module Control Center — Design Spec

**Date:** 2026-07-08
**Status:** Approved (design), pending implementation plan
**Author:** brainstormed with user

## Problem

Per-module feature/visibility toggles today are **ad-hoc and duplicated**. Two
modules follow a `{enabled, visible}` blob stored in `system_settings`, but each
has its own copy-pasted service and no shared abstraction:

| Module | `system_settings` key | Service file | Defaults | Admin toggle lives… |
|---|---|---|---|---|
| Topic Inspiration | `topics.module` | `backend/app/services/topics/module_config.py` | ON / fail-open | inside `TopicScoring.tsx` (route `/settings/topic-scoring`) |
| Distribution | `distribution.module` | `backend/app/services/distribution/module_config.py` | OFF / fail-closed | `DistributionModule.tsx`, rendered inside the generic Settings page (`settings/index.tsx:176`) |

Both modules already have dedicated admin GET/PUT endpoints
(`/api/v1/admin/settings/topics-module` and `/distribution-module`) and their own
`{enabled, visible}` schemas (`TopicModuleConfigResponse`,
`DistributionModuleConfigResponse`). The problem is not that they're missing UI —
it's that the two toggles are **scattered across two different admin pages** with
copy-pasted backend + frontend code, and there is no single place to see or flip
every module.

Problems this causes:
- The two `module_config.py` files are near-identical siblings, not a shared unit.
- The two admin endpoints, schemas, API hooks, and toggle components are
  copy-paste duplicates.
- A Distribution toggle sitting on the generic Settings page is easy to miss; a
  Topic toggle sitting on the *scoring* page is a poor home for it.
- The topic `module_config` does **not** defensively handle the asyncpg
  "jsonb-returned-as-string" quirk (distribution's does, via `_coerce_dict`), so
  a string-typed value would silently fall back to defaults — a latent bug.
- There is no single place for an admin to see/flip every module.

## Goal

A single **Modules** admin page (new nav entry) that centrally controls every
product module's two switches — **Processing** (feature / backend behavior) and
**Visibility** (shown in user nav/pages) — backed by a registry so adding a
future module is a one-line change.

**In scope:** the two existing modules (Topic Inspiration, Distribution), built
on an extensible registry.
**Out of scope (YAGNI):** extending toggles to other product modules
(Media/Tags/Projects/…); unifying the user-facing frontend hooks; the
`distribution.douyin` credentials key.

## Approach (chosen: Method A — registry + backend consolidation)

Build the registry and consolidate the backend, but keep the two public
`/module-status` endpoints untouched so the user-facing app (Sidebar, router)
sees **zero change** and carries zero risk.

### 1. Backend — module registry

New file `backend/app/services/modules/registry.py`:

```python
@dataclass(frozen=True)
class ModuleDef:
    id: str            # stable slug used in the admin URL, e.g. "topic-inspiration"
    key: str           # system_settings key, e.g. "topics.module"
    label: str         # English display label, e.g. "Topic Inspiration"
    enabled_default: bool
    visible_default: bool

MODULES: list[ModuleDef] = [
    ModuleDef("topic-inspiration", "topics.module", "Topic Inspiration",
              enabled_default=True,  visible_default=True),   # fail-open
    ModuleDef("distribution",      "distribution.module", "Distribution",
              enabled_default=False, visible_default=False),  # fail-closed
]

MODULES_BY_ID  = {m.id: m for m in MODULES}
MODULES_BY_KEY = {m.key: m for m in MODULES}
```

Shared helpers in the same module (single source of the jsonb-string fix):

- `read_module_config(key, enabled_default, visible_default) -> ModuleState`
  — one `db_engine.fetch_val` read; coerces a str-typed jsonb via `json.loads`
  (lifts distribution's `_coerce_dict`); parses `enabled`/`visible` bools with
  per-field defaults; returns `ModuleState(enabled: bool, visible: bool)`.
  `fail_mode` is expressed purely by the defaults passed in (no separate field).
- `write_module_config(key, enabled, visible, updated_by)` — delegates to the
  existing `SystemSettingsRepository.upsert_setting`.

The two existing `module_config.py` files become thin shims. Their public
functions **keep the same signatures** so all current call sites are untouched:

- `topics/module_config.py`: `is_module_enabled()` / `is_module_visible()`
  delegate to `read_module_config("topics.module", True, True)`.
- `distribution/module_config.py`: same, delegating with `(False, False)`.
- The `MODULE_CONFIG_KEY` constants stay (re-exported) for any importer.

This fixes the topic jsonb-string latent bug for free (both now route through the
one coercing reader).

### 2. Backend — single admin endpoint

In `backend/app/api/admin/settings_router.py` (prefix `/api/v1/admin/settings`),
add two routes reusing the existing admin-auth dependency and audit-log pattern
already used by `PUT /topics-module`:

- `GET /modules` → iterate `MODULES`, read each current state, return
  `list[ModuleSummary]` where
  `ModuleSummary = {id, key, label, enabled, visible, enabled_default, visible_default}`.
  (Frontend derives an "opt-in / default off" badge from
  `enabled_default == false`.)
- `PUT /modules/{module_id}` → body `{enabled: bool, visible: bool}`. Validate
  `module_id in MODULES_BY_ID` (404 otherwise); `write_module_config(...)`;
  write an audit-log entry (same helper the topics-module PUT uses); return the
  updated `ModuleSummary`.

The dedicated `GET/PUT /topics-module` **and** `GET/PUT /distribution-module`
endpoints are **removed** — their only consumers are the two scattered toggle UIs
being deleted, and the unified `/modules` endpoint supersedes both. Their request
schemas (`TopicModuleConfigResponse`, `DistributionModuleConfigResponse`) are
removed from the settings-router imports (the classes may remain in
`schemas/admin.py` if referenced elsewhere — verify with grep at implementation
time). The generic `PATCH /settings/{key}` path remains for everything else.

**Unchanged and intentionally so:** the public
`GET /topics/module-status` and `GET /distribution/module-status` endpoints, and
`require_distribution()`. They already call `is_module_enabled/visible`, which
now route through the registry — behavior is identical.

### 3. Admin frontend — new page, hooks, nav

- New page `admin/src/pages/settings/Modules.tsx`: one Card per module, reusing
  the two-switch card layout from `DistributionModule.tsx` — a **Processing**
  switch (bound to `enabled`) and a **Visibility** switch (bound to `visible`),
  each with the open/close consequence hint text below it. Modules whose
  `enabled_default === false` show a small "Opt-in · default off" badge. The PUT
  sends both fields (matching the whole-blob replace semantics).
- API hooks in `admin/src/api/endpoints/settings.ts`: `ModuleSummary` interface,
  `useModules()` (GET `/api/v1/admin/settings/modules`), `useUpdateModule()`
  (PUT `/api/v1/admin/settings/modules/{id}`). Remove the now-dead
  `useTopicModuleConfig` / `useUpdateTopicModuleConfig` / `TOPIC_MODULE_URL` and
  `useDistributionModuleConfig` / `useUpdateDistributionModuleConfig` /
  `DISTRIBUTION_MODULE_URL` / `DistributionModuleConfig` / `TopicModuleConfig`.
- Route: `admin/src/App.tsx` add
  `<Route path="/settings/modules" element={<Modules />} />`.
- Nav: `admin/src/layouts/AdminLayout.tsx` add a `<MenuItem key="/settings/modules">`
  labelled **"Modules"** in the **System** group (next to Settings / AI
  Governance) and add `/settings/modules` to `allMenuKeys`.
- Cleanup:
  - delete the two toggle cards + `patchModule` + the `useTopicModuleConfig` /
    `useUpdateTopicModuleConfig` usage from `TopicScoring.tsx` (that page keeps
    only the scoring / prefilter / content-fetch configuration).
  - delete `admin/src/pages/settings/DistributionModule.tsx` and remove its
    `import` + `<DistributionModule />` render from `settings/index.tsx`.

### 4. Explicitly not done (YAGNI)

- No unified public `/modules-status` endpoint; no merging of the user-facing
  hooks (`useTopicModuleStatus` / `useDistributionModuleStatus`); **no changes to
  `frontend/components/Sidebar.tsx` or `frontend/router.tsx`.** (Method C —
  backlog, revisit when a third module is added.)
- No new module toggles for other product areas — the registry has the extension
  point ready.
- `distribution.douyin` (credentials) is untouched — it is a credential, not a
  switch.

## Data flow

```
Admin UI (Modules.tsx)
  GET  /api/v1/admin/settings/modules   ── registry.MODULES → read_module_config each → [ModuleSummary]
  PUT  /api/v1/admin/settings/modules/{id} ── validate id → write_module_config → upsert_setting → audit log

User app (unchanged)
  GET /topics/module-status        ── topics.module_config (shim) → read_module_config
  GET /distribution/module-status  ── distribution.module_config (shim) → read_module_config
  require_distribution()           ── distribution.is_module_enabled (shim)
```

## Error handling

- Reader: any DB/parse failure returns the **module's own defaults** (fail-open
  for topics, fail-closed for distribution) — same behavior as today, now
  centralized.
- `PUT /modules/{id}` with unknown `id` → 404. Invalid body (non-bool) → 422 via
  the Pydantic request model.
- Admin endpoints keep the existing admin-role auth dependency.

## Testing

- **Unit:** `read_module_config` — dict value, string-typed jsonb value (the
  quirk), missing key → defaults, partial blob (only `enabled` present) uses
  per-field default for the missing one; both fail-open and fail-closed default
  pairs.
- **Backend integration:** `GET /modules` returns both modules with current
  state; `PUT /modules/topic-inspiration` flips and persists; `PUT` unknown id →
  404; audit-log row written.
- **Regression:** the two shims preserve behavior — `is_module_enabled/visible`
  for topics/distribution return the same values as before across the reader test
  matrix. Public `/topics/module-status` and `/distribution/module-status`
  responses unchanged.
- **Admin UI:** Modules page renders one card per registry entry; toggling calls
  PUT with both fields; TopicScoring no longer renders the module toggles.

## Files touched

New:
- `backend/app/services/modules/registry.py`
- `admin/src/pages/settings/Modules.tsx`
- tests for the registry reader + admin endpoints

Modified:
- `backend/app/services/topics/module_config.py` (→ shim)
- `backend/app/services/distribution/module_config.py` (→ shim)
- `backend/app/api/admin/settings_router.py` (add `/modules` GET+PUT, remove `/topics-module` + `/distribution-module`)
- `admin/src/api/endpoints/settings.ts` (add module hooks, remove topic + distribution module hooks)
- `admin/src/App.tsx` (route)
- `admin/src/layouts/AdminLayout.tsx` (nav item + allMenuKeys)
- `admin/src/pages/settings/TopicScoring.tsx` (remove toggle cards)
- `admin/src/pages/settings/index.tsx` (remove `<DistributionModule />` render + import)

Deleted:
- `admin/src/pages/settings/DistributionModule.tsx`

Unchanged (verified): `frontend/` Sidebar, router, module status services/hooks;
public `/module-status` endpoints; `require_distribution`; `distribution.douyin`.
