# Horizontal authz hole on resources list/trash/delete endpoints

Date: 2026-05-18
Severity: **HIGH** (confirmed real, exploitable by any authenticated user)
Discovered: 2026-05-17 Batch 1 manual QA (task #20)
Fixed: this PR
Related: PR #274 (write-side scope guards — added `verify_scope_access` but missed read+delete endpoints)

## Symptom

`GET /api/v1/resources?scope_type=personal&scope_id=<other_user_id>` returns 200 with the victim's resource list — title, filename, mime, URL, tags, ratings, file sizes, AI status, parsed_media metadata.

Same problem on `GET /resources/trash`, `GET /resources/trash/folders`, and `DELETE /resources/{resource_id}`.

## How it was reached

Caller pattern (any authenticated user with a valid JWT):

```
GET /api/v1/resources?scope_type=personal&scope_id=8e1584e3-9c29-4a5b-90fe-125b74259f7f
Authorization: Bearer <attacker's own JWT>
```

The handler does not verify that `auth.user_id == scope_id` (personal) or `auth.user_id ∈ team_members(scope_id)` (team). The repo call `ResourcesRepository.get_resource_items(scope_type, scope_id, ...)` runs against the service-role Supabase client (bypasses RLS), so the only filter is the user-controlled `scope_id` query parameter.

## Verification (DB-only PoC, did not send live requests)

Query the same data the endpoint would return for an attacker passing `scope_id=8e1584e3-...`:

```sql
SELECT scope_id AS victim_user_id, COUNT(*) AS leakable_items
FROM resource_items
WHERE scope_type='personal'
GROUP BY scope_id ORDER BY 2 DESC LIMIT 5;
```

Result (run 2026-05-18 via Supabase MCP — read-only, no live HTTP):

| victim_user_id | leakable_items |
|----------------|----------------|
| `8e1584e3-9c29-4a5b-90fe-125b74259f7f` | 544 |
| `fab0ba60-05cf-4cdf-a622-33372f0cca4b` | 3 |
| `81e49ea8-c3d5-4bc7-a904-7bdf109e0cd9` | 2 |

All 549 personal-scope resource_items in production were enumerable cross-tenant. Zero team-scope items existed at audit time (which is why the original QA test against an arbitrary team_id returned `[]` and the bug looked latent rather than active).

## Why it looked "maybe safe" in the original QA

QA on 2026-05-17 tested `scope_type=team&scope_id=<arbitrary team>`. That team had zero resources → endpoint returned 200 + `[]`. With `scope_type=personal&scope_id=<another user>` the leak is immediate — but QA didn't try that combination, so I (correctly) filed it as a verification task rather than calling it a confirmed bug. Today's audit closes that gap.

## Root cause

Repository layer uses the **service-role** Supabase client (`get_async_supabase_admin`) which bypasses RLS. Comment in `backend/app/repositories/resources_repository.py:7` explicitly says "Uses async Supabase admin client." The list query at line 351 (`get_resource_items`) filters only by `scope_id` — no `creator_id == current_user` or team-membership check.

This is the exact pattern PR #274 (2026-05-14) called out and fixed for upload endpoints:
> "Three upload endpoints took a user-controlled write target (scope_id / project_id / resource_id) and wrote to it without verifying the caller owned that target. The backend uses the service-role Supabase client, which bypasses RLS, so the table policies that look correct never actually ran for these paths."

PR #274 introduced `app/core/scope_guards.py::verify_scope_access` as a FastAPI dependency. That dependency was applied to `POST /resources/upload` but never propagated to the read or delete endpoints in `resources_crud_router.py`.

## Fix (this PR)

Add `_scope_guard: None = Depends(verify_scope_access)` to four endpoints in `backend/app/api/resources_crud_router.py`:

| Endpoint | Method | Status before | Status after |
|----------|--------|---------------|--------------|
| `/resources` | GET (list) | 200 + leaked data | 403 if non-member / non-self |
| `/resources/trash` | GET | 200 + leaked trash | 403 |
| `/resources/trash/folders` | GET | 200 + leaked folders | 403 |
| `/resources/{resource_id}` | DELETE | 200 + foreign delete | 403 |

`verify_scope_access` (from PR #274) was already correct — just unused on the read/delete side. It enforces:
- `personal`: `scope_id == auth.user_id`, else 403
- `team`: `auth.user_id` must be in `team_members(team_id=scope_id)`, else 403

No repository-layer change. RLS posture unchanged (still relied on admin client for the rest of the methods). Defense-in-depth would also add `creator_id`/`scope_id` filtering at the SQL layer, but it's not needed for THIS fix and out of scope.

## Out of scope (follow-ups)

- **Other resources_crud endpoints with `Optional[str] scope_id`**: `/resources/by-platform-id/{id}` (trash), `/resources/by-media-id/{id}` (trash), `/resources/unlink/{id}` (lines 615 / 668 / 717). These take optional scope_id with default fallback to personal. Need a separate audit because the optional shape doesn't fit `verify_scope_access`'s required-Query model directly — likely need a `verify_scope_access_optional` variant.
- **Other routers**: `resources_versions_router`, `resources_folders_router` — same admin-client pattern, may have similar holes. Out of scope here; flag for next security pass.
- **Defense-in-depth at repo layer**: every `get_*` should also receive `caller_user_id` and add WHERE clauses. Bigger refactor, not blocking.

## Verification plan after deploy

```bash
# As user A, try to read user B's resources
curl -H "Authorization: Bearer <A's JWT>" \
  "https://mediahubserver.heygo.cn:88/api/v1/resources?scope_type=personal&scope_id=<B's user_id>"
# Expected: 403 {"detail":"Cannot write to another user's personal scope"}
# (message says "write" because the guard is shared with upload — copy is fine)

# As user A, read own resources (smoke test, must still work)
curl -H "Authorization: Bearer <A's JWT>" \
  "https://mediahubserver.heygo.cn:88/api/v1/resources?scope_type=personal&scope_id=<A's user_id>"
# Expected: 200 + own data
```

If 403 message confuses users on the read path, follow-up: tighten `verify_scope_access` wording or split into read/write variants.
