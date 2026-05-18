# Resources routers — full horizontal-authz audit

Date: 2026-05-18
Triggered by: PR #298 (closed `list_resources` leak from task #20). Once that confirmed the pattern, did a full audit across all 4 resources routers.
Status of this PR: ships the next batch of obvious wins (folders read endpoints — same shape as #298). Larger remaining work is **documented as security TODOs below**, not silently deferred.

## Status matrix (4 resources sub-routers)

### `resources_upload_router` — ✅ ALREADY GUARDED (PR #274)

`POST /upload` uses `_scope_guard: None = Depends(verify_scope_access)`. Done.

### `resources_crud_router` — ✅ PARTIALLY GUARDED (PR #298 + this PR)

| Endpoint | Method | Status |
|----------|--------|--------|
| `/resources` | GET (list) | ✅ PR #298 |
| `/resources/trash` | GET | ✅ PR #298 |
| `/resources/trash/folders` | GET | ✅ PR #298 |
| `/resources/{id}` | DELETE | ✅ PR #298 |
| `/resources/by-platform-id/{id}` (trash) | * | ⚠️ **OPEN** — `Optional[str] scope_id`, needs `verify_scope_access_optional` helper |
| `/resources/by-media-id/{id}` (trash) | * | ⚠️ **OPEN** — same |
| `/resources/unlink/{id}` | * | ⚠️ **OPEN** — same |

### `resources_folders_router` — ⚠️ PARTIALLY GUARDED (this PR)

#### Fixed by this PR (3 GET endpoints with required `scope_id`)

| Endpoint | Method | Fix |
|----------|--------|-----|
| `/resources/smart-folders` | GET | added `_scope_guard: None = Depends(verify_scope_access)` |
| `/resources/smart-folders/{id}/results` | GET | same |
| `/resources/folders/list` | GET | same |

#### Still open (10 endpoints, listed by risk)

| # | Endpoint | Method | Risk | Reason |
|---|----------|--------|------|--------|
| 1 | `/resources/folders/{id}` | DELETE | **CRITICAL — destructive cascade** | No ownership check on `folder_id`; deletes folder + all contained resources for any folder owned by anyone |
| 2 | `/resources/folders/{id}/trash` | POST | HIGH — destructive | Same — can trash any folder |
| 3 | `/resources/folders/{id}/restore` | POST | HIGH — state mutation | Can restore any user's trashed folder |
| 4 | `/resources/smart-folders/{id}` | DELETE | HIGH | Can delete any smart folder |
| 5 | `/resources/smart-folders/{id}` | PATCH | HIGH | Can rewrite any smart folder's rules |
| 6 | `/resources/folders/{id}` | PATCH | HIGH | Can rename / move / trash any folder |
| 7 | `/resources/folders/{id}/content-count` | GET | MEDIUM — info leak | Returns resource counts inside any folder |
| 8 | `/resources/smart-folders` | POST | MEDIUM | `scope_id` in JSON body — can create folder in anyone's scope (clutters their library) |
| 9 | `/resources/folders` | POST | MEDIUM | Same |
| 10 | (handled) | — | — | `GET /smart-folders/{id}/results` query also needs guard on `folder_id` itself, not just scope_id |

These all need a **new** guard helper `verify_folder_access(folder_id, auth)` that:
1. Looks up `folders` row by id (admin client)
2. Returns 404 if not found
3. For `personal` scope: 403 if `folder.scope_id != auth.user_id`
4. For `team` scope: 403 if `auth.user_id not in team_members(folder.scope_id)`

Mirrors the existing `verify_resource_write_access`.

### `resources_versions_router` — ⚠️ PARTIALLY GUARDED (audit complete, fixes pending)

| Endpoint | Method | Status |
|----------|--------|--------|
| `/resources/{id}/versions` (upload) | POST | ✅ PR #274 (`verify_resource_write_access`) |
| `/resources/{id}/versions` (list) | GET | ⚠️ **OPEN** — no read guard exists yet |
| `/resources/{id}/versions/{n}/set-current` | POST | ⚠️ **OPEN** — can rewrite version pointer on any resource |
| `/resources/{id}/versions/{vid}` | DELETE | ⚠️ **OPEN — CRITICAL destructive** |
| `/resources/{id}/versions/{vid}/hls/{path}` | GET | ⚠️ **OPEN** — file content leak |
| `/resources/{id}/versions/{vid}/transcode` | POST | ⚠️ **OPEN** — can trigger transcode on any resource (cost/abuse) |
| `/resources/{id}/versions/{vid}/file` | GET | ⚠️ **OPEN** — file content leak |

Needs a **new** `verify_resource_read_access(resource_id, auth)` helper. The existing `verify_resource_write_access` checks `creator_id == auth.user_id`. Read access could be slightly broader (team members can read team resources too) — needs design discussion.

## Why not fix everything in one PR

- This audit grew during PR #298's diagnosis. Continuing to expand would balloon the diff and slow review of the (urgent, already-confirmed) leaks.
- The 17 remaining endpoints split across 3 different guard patterns:
  - `verify_scope_access_optional` — for the 3 trash-by-id endpoints with `Optional[str] scope_id`
  - `verify_folder_access` — for 7 folders endpoints acting on a folder_id
  - `verify_resource_read_access` — for 6 versions endpoints acting on a resource_id
- Each helper needs design + tests. Bundling them risks a half-baked guard family.

## Recommended sequencing

1. **This PR**: ship the 3 folders GET fixes (same pattern as #298, zero design risk).
2. **Next PR (1-2 days)**: write `verify_folder_access` helper + apply to the 7 folder-id endpoints. Single helper, single PR.
3. **Next PR (1-2 days)**: write `verify_resource_read_access` helper + apply to the 6 versions endpoints. Same shape, same day.
4. **Next PR (~30 min)**: write `verify_scope_access_optional` + apply to the 3 trash-by-id endpoints.

Total remaining work: ~3 small focused PRs, ~3-5 days elapsed if reviewed promptly.

## Tracking

Add to TODOS.md as TODO-SECURITY-001 / 002 / 003 corresponding to the 3 follow-up PRs above. Reference this audit doc as the master list.
