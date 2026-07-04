# Topic Inspiration module: split the master switch into processing + visibility

Date: 2026-07-04
Status: approved (user confirmed design in session)

## Problem

`system_settings['topics.module'] = {"enabled": bool}` is a single kill switch
that drives TWO unrelated behaviors:

1. **Backend pipeline** — `topic_inspiration.py` scheduled tick skips
   fetch/score/embed/cluster when off.
2. **Frontend surface** — `/api/v1/media... /topics/module-status` feeds
   `useTopicModuleEnabled()`, which hides the nav item AND the page.

On 2026-06-30 an admin turned the switch off intending to pause processing;
the nav entry vanished too (user report: "灵感库整个都不显示了"). Pausing the
pipeline and hiding the feature are different intents and need independent
switches.

## Design

Keep ONE jsonb key, extend it to two fields (atomic read/write, no migration):

```json
system_settings['topics.module'] = { "enabled": bool, "visible": bool }
```

- `enabled` — processing switch. Semantics unchanged: scheduled tick no-ops
  when false. Never consulted by the frontend for rendering.
- `visible` — display switch (new). Controls nav item + page routing only.
- **Fails-open on both fields**: missing/garbage → `true`. The currently
  stored `{"enabled": false}` therefore becomes `enabled=false, visible=true`
  on deploy — the nav entry reappears immediately, pipeline stays paused.
  Zero data migration.

### Four combinations

| enabled | visible | behavior |
|---|---|---|
| ✅ | ✅ | normal (default) |
| ❌ | ✅ | entry + page shown with existing hotspot data; page shows a subtle "Content updates paused" notice |
| ✅ | ❌ | pipeline runs, surface hidden (staging/soft-launch) |
| ❌ | ❌ | fully dark (today's behavior) |

## Changes

Backend (`backend/app/services/topics/module_config.py`):
- `parse_module_enabled(raw)` unchanged; add `parse_module_visible(raw)`
  (same shape: `raw["visible"]` must be a bool, else default `True`).
- `is_module_enabled()` unchanged; add `is_module_visible()`. Both share one
  raw read helper; both never raise (fail-open).

Backend (`backend/app/api/topics_router.py`):
- `ModuleStatusResponse` gains `visible: bool`. `/module-status` returns both
  fields. (Existing consumers reading only `enabled` keep working.)

Backend (`backend/app/api/admin/settings_router.py`):
- `TopicModuleConfigResponse` gains `visible: bool = True`.
- GET returns both; PUT persists `{"enabled": ..., "visible": ...}` through
  the same `upsert_setting` + audit log.

Frontend (`frontend/services/topicService.ts`):
- `getModuleStatus()` returns `{ enabled, visible }` (both fail-open true).

Frontend (`frontend/hooks/useTopicModuleEnabled.ts`):
- Becomes the status hook returning `{ visible, enabled }`. Sidebar gates the
  nav item on `visible`; `TopicInspirationPage` gates rendering on `visible`
  and shows the paused notice when `enabled === false`.

Frontend i18n: `topic.updatesPaused` in en/zh.

Admin app (`admin/src/api/endpoints/settings.ts` + its Settings section):
- Topics module panel: one Switch becomes two — "Processing" (`enabled`) and
  "Visible to users" (`visible`), both persisted via the same PUT.

Out of scope: the per-function sub-switches (`topics.prefilter`,
`topics.scoring`, `topics.content_fetch`) are pipeline-internal and unchanged.

## Testing

- Unit: `parse_module_visible` — missing key / non-bool / explicit false.
- Router: `/module-status` returns both fields for all four stored shapes.
- Admin PUT: persists both fields; GET round-trips.
- Frontend: hook exposes visible/enabled; Sidebar hides only on
  `visible=false`; page shows paused notice only on `enabled=false`.

## Rollout

Prod value stays `{"enabled": false}` — after deploy the entry returns on its
own (visible defaults true) and processing remains paused until an admin
re-enables it in the admin panel.
