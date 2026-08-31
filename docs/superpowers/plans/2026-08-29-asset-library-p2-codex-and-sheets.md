# Asset Library P2 — Codex & Entity Sheets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the user-facing asset library on top of P0: the **Assets** sidebar location with the type-tab codex (`AssetsView` + `AssetCard`), the six entity sheets (`AssetSheetPage` with the board-style visual area, relations, loadouts, prompts), the `Equip…` file picker, `Generate missing…`, prompt translate/regenerate wired to the existing agents, and the remaining P0-deferred API pieces (`duplicate`, `generate-slot`, readiness/tag/sort filters, nullable-field clearing, `Envelope[T]`).

**Architecture:** Backend adds four endpoints on the existing `AssetsService`/router (`duplicate`, `generate-slot`, `prompt/translate`, `prompt/regenerate`) plus list filters; `generate-slot` reuses `ImageGenerationService.generate_image` and `register_generated_media(... source_asset_id, params.target_slot)` so output lands in the P1 inbox pre-filled. Frontend is a new `features/assets/` area: one page shell, one card, six sheet sub-layouts sharing a `Board` component (read-only pins, arrange persisted to `attrs.board_layout`), and two dialogs (`EquipDialog` = resource picker, `GenerateMissingDialog`). Project-workspace pages, canvas nodes and chat wiring stay in P3–P5.

**Tech Stack:** FastAPI + SQLAlchemy async (ORM only), existing `TranslateService` / vision caption agent / `ImageGenerationService`; React 19 + vitest/RTL + Playwright; i18next; Tailwind semantic tokens; lucide icons only (no emoji — user rule).

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` — §2 decisions 2, 3, 10–15; §3.6 slots; §5.1 (P2 subset: duplicate, generate-slot, prompt/translate, prompt/regenerate, list `readiness/tag/sort`); §6.2 entity pages; §7; §9 P2 row. Mockup screens 3, 4, 4b, 4c: https://claude.ai/code/artifact/3db4a9bf-907d-40c5-bd21-40694107efbb. P0 final-review deferred items assigned to P2: `update_asset` cannot clear nullable fields (M5); `Envelope[T]` response model (I4); `cover_file_id` scope check (M6); relation writes bump `updated_at` (M7); `list_project_assets` legacy-owner 500 (M8 read half); private-name imports (T8 minor).

## Global Constraints

- Same as P1: Python 3.13 / `uv run`; Node 22; ORM only; scope via `?scope_id=` + team membership; string Snowflake ids on the wire; `SnowflakeId` validation; `{success, data}` / `{success:false, error:{code,detail}}` envelope; no PostgREST reads of asset tables; real wire shapes in edge mocks; English UI via i18n (`assets.*` namespace); lucide icons, semantic color tokens (`ok/warn/danger/info/agent`), **no emoji**.
- **System presets are read-only everywhere** (P0 `_require_writable`); `duplicate` is the only way to edit one.
- **Readiness is derived, never stored**; the board never writes files — `Equip` writes `asset_files` only; `Generate missing` writes a `generated_media` row only (the user attaches from the inbox, or the dialog attaches on completion via the P1 `save-as-asset` endpoint).
- Do not touch P1's files if P1 has not merged yet: this branch forks from master at P0; if P1 lands first, rebase (`bash scripts/sync-worktree.sh`) before Task 8 (which calls P1's `save-as-asset`). If P1 is still open when Task 8 starts, implement the attach-on-completion path against the P0 `POST /assets/{id}/files` endpoint instead and note it.
- Commit only files the task names; never `git add -A`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/app/schemas/assets.py` | + `DuplicateRequest`, `GenerateSlotRequest/Response`, `PromptTranslateRequest`, `Envelope[T]` generics, `AssetListQuery` extras; `AssetUpdate` gains explicit-null clearing via `model_fields_set` |
| `backend/app/services/assets/assets_service.py` | + `duplicate`, `translate_prompt`, `regenerate_prompt`, `generate_slot`, list `readiness/tag/sort`, `touch_updated_at` on relation writes, `cover_file_id` scope check, `update_asset` clears nullable fields |
| `backend/app/services/assets/slot_generation.py` | Pure: `slot_prompt(asset, slot) -> (positive, negative)` templates per type/slot (e.g. character `expressions` → "…six basic expressions 2×3 grid…") + reference selection order (primary → worn → stills) |
| `backend/app/repositories/assets_repository.py` | + `list` filters `readiness` (derived → filtered in service), `tag`, `sort`; `duplicate_rows`; `touch` |
| `backend/app/api/assets_router.py` | + 4 routes; `response_model=Envelope[...]` on every route |
| `frontend/services/assetsService.ts` | Full client (list/get/create/update/delete/duplicate/files/links/loadouts/project-refs/translate/regenerate/generate-slot) |
| `frontend/features/assets/AssetsPage.tsx` | Route shell `/team/:teamId/resources/assets(/:type)?` — type tabs ↔ sidebar sync, filters, grid |
| `frontend/features/assets/AssetCard.tsx` | Card: portrait, name, role, slot squares, readiness ring, chips |
| `frontend/features/assets/sheet/AssetSheetPage.tsx` + `sheets/{Character,Location,Prop,Costume,Prompt,Audio}Sheet.tsx` | Six sub-layouts on one skeleton |
| `frontend/features/assets/sheet/Board.tsx` | Main pin + pins grid; Arrange (drag, persisted); Equip/Generate hooks |
| `frontend/features/assets/sheet/{RelationsSection,LoadoutChips,PromptEditor,UsedInPanel,DetailsPanel}.tsx` | Sections |
| `frontend/features/assets/dialogs/{EquipDialog,GenerateMissingDialog,NewAssetDialog,LinkAssetDialog}.tsx` | Dialogs |
| `frontend/components/ResourcesSidebar.tsx`, `router.tsx`, `contexts/ResourcesContext.tsx` | Assets location + six sub-items with counts; routes |
| `frontend/public/locales/{en,zh}.json` | `assets.*` |
| `frontend/e2e/assets-codex.spec.ts` | Route-mocked flow with real wire fixtures |

---

### Task 1: Backend — list filters, nullable clearing, cover scope check, touch, Envelope

**Files:** `backend/app/schemas/assets.py`, `backend/app/repositories/assets_repository.py`, `backend/app/services/assets/assets_service.py`, `backend/app/api/assets_router.py`; tests `backend/tests/services/assets/test_assets_service_p2.py`, `backend/tests/api/test_assets_router_p2.py`, `backend/tests/services/assets/test_schemas.py` (extend).

**Interfaces:**
- `GET /assets` adds `readiness=ready|draft` (filtered in the service after derivation), `tag=<string>` (matches `tags` JSONB: any group contains the value — repo: `Assets.tags.op('@>')` is not usable for nested groups; use `func.jsonb_path_exists(Assets.tags, '$.* ? (@ == $v)', {"v": tag})` via `func`), `sort=recent|name|readiness` (readiness sorts in service).
- `AssetUpdate`: fields declared `Optional[...] = None` stay, but the service uses `payload.model_fields_set` so an explicit `null` clears `subtype / cover_file_id / prompt_*` (docstring: "omit = unchanged, null = clear").
- `update_asset`: when `cover_file_id` is set, `relations.resource_in_scope(cover_file_id, scope_id)` else `404 resource_not_found`.
- `AssetRelationsRepository.touch_asset(asset_id)` sets `assets.updated_at = now()`; the service calls it after every relation write (attach/detach/link/unlink/loadout create/update/delete/project link/unlink).
- `Envelope[T] = BaseModel(success: bool, data: T)` + `ErrorEnvelope`; every router route gets `response_model=Envelope[X]` with `responses={4xx: ErrorEnvelope}`; the existing contract test is extended to assert `AssetResponse` still validates.
- `list_project_assets`: when `_resolve_personal_team_id` raises for the owner → `422 personal_team_missing` envelope (P0 M8 read half).

- [ ] Tests → fail → implement → `uv run pytest tests/services/assets tests/api/test_assets_router*.py -v` → pass → lint → commit `feat(assets): list filters, nullable clearing, cover scope check, touch on relation writes, Envelope response models`.

---

### Task 2: Backend — `duplicate`

**Files:** service/repo/router/schemas as above; tests in the same P2 files.

- `POST /assets/{id}/duplicate?scope_id` body `{name?: str}` → 201 new asset in the caller's scope with `source='duplicated'`, `duplicated_from=id`, all `asset_files` rows copied (same resource ids, same slots/loadout mapping remapped to the new loadouts), `asset_links` copied (outgoing only), loadouts copied with `is_default` preserved, `prompt_*`/`attrs`/`tags`/`platform_params` copied, `is_system_preset=false`. Name defaults to `"{name} (copy)"`; 409 on collision as usual. Presets are duplicable by any member (that is how they get edited). One `unit_of_work()`.
- [ ] Tests (fake repos: files/links/loadouts remapped; preset source allowed; 409 path) → implement → pass → commit `feat(assets): duplicate (files/links/loadouts remapped)`.

---

### Task 3: Backend — prompt translate / regenerate

**Files:** `assets_service.py`, `assets_router.py`, `schemas/assets.py`; tests.

- `POST /assets/{id}/prompt/translate?scope_id` body `{target_lang: "zh"|"en"}` → translates `prompt_positive`+`prompt_negative` into the `_zh` columns (or reverse) using `TranslateService` exactly as `resources_ai_router.translate_gen_prompt` does (read it and reuse `build_translate_plan`'s shape for two fields; never clobber a non-empty target unless `force=true`). Errors map: `AllModelsFailed`/`LLMCallError` → `503 translate_unavailable` with the provider detail.
- `POST /assets/{id}/prompt/regenerate?scope_id` → vision caption over the **primary-slot** file (first by `sort_order`) via the same path `_single_asset_ai(resource_id, user_id, "caption")` uses (extract the callable into `app/services/library/resource_ai_ops.py` if it is router-private; do not import from a router), writes `prompt_positive` (+`_zh` when the agent returns both); `422 no_primary_file` when the slot is empty; presets 403.
- [ ] Tests with the agent calls monkeypatched → implement → pass → commit `feat(assets): prompt translate/regenerate via existing agents`.

---

### Task 4: Backend — `generate-slot`

**Files:** `backend/app/services/assets/slot_generation.py` (+ test), `assets_service.py`, router, schemas.

- Pure `slot_prompt(asset_row, slot, loadout_row | None) -> {"positive", "negative"}` — templates per (type, slot): character `sheet` ("character sheet: close-up + front/side/back…"), `expressions` (2×3 grid), `stills`, location `establishing`/`keyframes`, prop `turnaround`, costume `flat`/`worn`, audio → `422 slot_not_generatable`; prepends the asset's `prompt_positive` and the loadout's `prompt_extra` + linked costume/prop prompts; negatives merged/deduped. Pure + tested for each (type, slot).
- Pure `reference_order(files_by_slot, type) -> list[resource_id]` (primary → worn → stills, capped at `max_refs` given by the caller).
- `POST /assets/{id}/generate-slot?scope_id` body `{slot, loadout_id?, model?, count: 1..4}` → for each: `ImageGenerationService().generate_image(project_id="asset", node_id=f"asset:{id}:{slot}", prompt, model, provider_name=None, reference_image_url=<cover URL of the first reference>, user_id)` → `register_generated_media(user_id, scope_id, source_url=…, mime, origin=GenerationOrigin(kind="agent_run", node_id=…, prompt, model, provider, params={"target_slot": slot, "loadout_id": …}))` **then** `set source_asset_id` (repo method from P1 if merged, else a small `GeneratedMediaRepository.set_source_asset(gen_id, asset_id)` added here) → returns `{generations: [ids], inbox_state: "unreviewed"}`. Provider errors → `503 generation_failed` with detail; no partial-success swallowing (per-item results).
- [ ] Tests (pure templates; service with fakes for the two external calls) → implement → pass → commit `feat(assets): generate-slot — templated prompt + refs → Generated inbox with source_asset_id`.

---

### Task 5: Frontend — client, routes, sidebar

**Files:** `frontend/services/assetsService.ts` (create or extend if P1 merged), `router.tsx` (`resources/assets`, `resources/assets/:type`, `resources/assets/item/:assetId`), `ResourcesSidebar.tsx` (Assets item + six sub-items with counts from `GET /assets/counts?scope_id` — add that endpoint in this task: `{character: n, …}`), `ResourcesContext.tsx` (`SidebarView` `'assets'`, `assetsCounts`), i18n `resources.assets`, `assets.types.*`.
- Tests: `assetsService.test.ts` (query building, envelope error), sidebar test (six sub-items with counts; active state follows `:type`).
- [ ] → commit `feat(assets): client, routes, sidebar location with type counts`.

---

### Task 6: Frontend — `AssetsPage` + `AssetCard` (screen 3)

- Type tab bar `All / Characters / Locations / Props / Costumes / Prompts / Audio` with counts, synced with `:type` and the sidebar; filters Project (from `projectService`), Readiness, Tags, Sort; `+ New ▾` (type preselected; in All → six-way menu) → `NewAssetDialog` (name/role/description) → `createAsset` → navigate to the sheet; `Import from script` button links to the project workspace (P3 owns the flow) — render disabled with tooltip when no project filter is active.
- `AssetCard`: portrait (`cover_file_id` → `/resources/{id}/cover`, else type icon), name, role line, slot squares from `SLOTS[type]` (filled when `file_counts_by_slot[slot] > 0`, dashed otherwise), readiness ring SVG (ok when ready, warn when draft; `missing` text in the chip), project chips, type tag in All view. Click → sheet.
- Tests: tab ↔ URL sync; card renders ring/missing/chips from a real `AssetResponse` fixture; New dialog validation.
- [ ] → commit `feat(assets): codex page with type tabs and asset cards`.

---

### Task 7: Frontend — `AssetSheetPage` skeleton + `Board` + sections (screens 4/4b/4c)

- Skeleton: breadcrumb, header (portrait, name, chips, role line, description inline-editable, loadout chips for character), `Board`, relation sections, `PromptEditor`, right column (`DetailsPanel`, `UsedInPanel` reading `project_ids` + canvas refs placeholder "Canvas usage arrives with P4", generation history from P1's inbox filtered by `source_asset_id` when P1 is merged, else hidden).
- `Board`: main pin = first file of the primary slot (16:9 frame, `Sheet`/`Establishing`/`Flat`/`Turnaround` label), pins grid = other slots (count badge, empty → dashed with `Equip · Generate`), `Arrange` toggles drag mode (dnd via pointer events; order persisted to `attrs.board_layout = {slot_order: [...]}` through `updateAsset`), click pin → lightbox (reuse `OutputLightbox` from canvas-core if importable without side effects, else a minimal modal).
- Six sheets differ only in: header extras (location `exterior/interior` from `role_tag`, audio `loopable/duration` from `attrs`), primary slot label, pins list, presence of loadouts (character only), relation sections (`Wears/Holds` on character; `Worn by/Held by` on costume/prop; `Attached to` on audio; none on location/prompt), and prompt sheet replacing the board with `PromptEditor` (positive/negative textareas, placeholders panel from `attrs.placeholders`, platform params editor, Examples pins) and audio replacing it with `AudioWaveformPlayer` + Variants list.
- `LoadoutChips`: list/select/create/rename/set default/delete (default undeletable, matching the API); switching a loadout re-filters `worn` pins by `loadout_id`.
- `RelationsSection`: `LinkAssetDialog` (search assets of the allowed target type, `addLink`), remove → `removeLink`.
- Actions column: `Send to Canvas` (disabled, tooltip "Arrives with P4"), `Send to Agent` (disabled, "P5"), `Copy loadout prompt` (client-side concat: asset positive + loadout extra + costume/prop positives → clipboard), `Duplicate`, `Open in canvas ›` small link (P0 decision 13: creates a kind-matched canvas via `createCanvas(projectId?, {kind, asset_id})` — requires a project; when the asset is linked to none, prompt to pick one).
- Tests per section with real `AssetDetailResponse` fixtures; readiness chip from derived field; loadout switch filters pins; relation add/remove calls.
- [ ] → commit `feat(assets): entity sheet skeleton, board, six sub-layouts, relations, loadouts, prompt editor`.

---

### Task 8: Frontend — `EquipDialog` + `GenerateMissingDialog`

- `EquipDialog(asset, slot)`: picks from My Uploads via the existing resource search endpoint (`/resources/search` with thumbnails — see `useResourceSearch`), multi-select → `attachFiles` batch (`POST /assets/{id}/files` with `items`), per-item errors surfaced (envelope codes), success toast `Attached {n} to {slot}`.
- `GenerateMissingDialog(asset, slot, loadout?)`: shows the templated prompt preview (call a new `GET /assets/{id}/generate-slot/preview?slot=&loadout_id=` added in this task, returning the pure `slot_prompt` output + the chosen reference ids), model select (from the model catalog endpoint used by the canvas), count 1–4 → `generateSlot` → on success, toast with a link to the Generated inbox (`/resources/generated?state=unreviewed`) — **P1 owns attach-from-inbox**; if P1 is merged, offer `Attach now` which calls `save-as-asset` for each id with the same slot.
- Tests: preview render; per-item error surfacing; P1-present/absent branch (feature-detect by probing `assetsService.hasInboxAttach` set from a build-time constant `VITE_ASSET_INBOX=1` default on — document).
- [ ] → commit `feat(assets): Equip and Generate-missing dialogs`.

---

### Task 9: i18n, e2e, docs, suite, PR

- `assets.*` en/zh complete; `frontend/e2e/assets-codex.spec.ts` (mocked with captured bodies: codex tabs → open sheet → equip → readiness flips to Ready → loadout create → duplicate); `e2e-prod/walkthrough.spec.ts` gains "sidebar Assets visible → codex heading visible".
- `CLAUDE.md`: one line pointing to the assets routes and the "Send to Canvas/Agent arrive with P4/P5" state.
- Full suites + lint; PR body must list: P2 depends on P0 only; P1 merge order note; disabled buttons that later phases enable; the P0 deferred items closed here (M5/M6/M7/M8-read/I4) and those still open (M2/M3/M4/M11 → P3).

---

## Self-review

- **Spec coverage (P2 row of §9):** AssetsView/AssetCard (T6), AssetSheetPage six types + Board (T7), relations + loadouts (T7), prompt agent wiring (T3 + T7), Generate missing (T4 + T8), duplicate (T2), list filters readiness/tag/sort (T1). Decisions 10–13 honored (board is read-only display; primary slot = one composite image; readiness derived; Open in canvas is a small link that creates on demand).
- **Cross-plan seams:** P1's `save-as-asset` / inbox history are feature-detected, not assumed (Global Constraints + T8); `source_asset_id` write in T4 has a fallback repo method if P1 is absent.
- **Placeholders:** none; test lists are behavior-named; every endpoint/field/key is defined above.
