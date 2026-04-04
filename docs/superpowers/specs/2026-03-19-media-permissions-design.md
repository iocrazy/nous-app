# Media Permissions — Phase B Design

## Goal

Add resource-level permission checks to `/media/{id}` endpoints. Currently any authenticated user can access any media file by ID. After this change, access is restricted to: resource owner, team members, or valid share link holders.

## Access Rules (priority order)

```
1. share_token query param → validate against shares table → ALLOW
2. review_token query param → TODO (future)
3. No authentication + no share_token → 401 Unauthorized
4. resources.creator_id = user_id → ALLOW (own resource)
5. resource in resource_items with scope_type='team',
   user is member of that team → ALLOW (team resource)
6. None of above → 403 Forbidden
```

share_token is checked first because shared links don't require login.

## share_token Validation

Query: `shares` table where:
- `share_code = share_token`
- `status = 'active'`
- Not expired (time + max_views)
- `resource_id` matches the requested resource

Password is NOT verified — share_code itself is the access credential (cryptographically secure 8-char token). Password protection happens at SharePage entry, not at media serving.

## Implementation

### New File: `backend/app/api/media_permissions.py`

Core function:
```python
async def check_media_access(
    media_id: str,
    user_id: str | None,
    share_token: str | None,
) -> bool:
    """
    Check if the user/token has access to the given media resource.

    Priority:
    1. Valid share_token → True
    2. No user_id → False
    3. Creator owns resource → True
    4. User is member of resource's team → True
    5. False
    """
```

### Modified File: `backend/app/main.py`

In the `/media/{media_id}` endpoint handler:
1. Call `_authenticate_media_request()` — returns `user_id` (may be None if share_token present)
2. Extract `share_token` from query params
3. Call `check_media_access(media_id, user_id, share_token)`
4. If False → raise 403
5. Proceed to `_resolve_file_path()` and `_serve_file()`

### Cache Strategy

Extend existing `_media_path_cache` to include ownership data:
```python
# Current cache value: file_path (str)
# New cache value: (file_path, creator_id, team_ids: list[str])
```

TTL remains 5 minutes. Cache key remains `(media_id, file_type)`.

share_token validation is NOT cached (must check status/expiry/view_count freshly).

### Authentication Flow Change

Current `_authenticate_media_request()` raises 401 if no valid auth. Change to:
- If `share_token` present in query params → skip auth requirement, return `None` as user_id
- Otherwise → existing auth flow (token/cookie validation)

## Error Responses

| Scenario | Status | Message |
|----------|--------|---------|
| No auth + no share_token | 401 | Authentication required |
| Authenticated but no access | 403 | Access denied |
| share_token invalid/expired | 403 | Invalid or expired share link |
| Resource not found | 404 | Resource not found |

## Scope Boundaries

**In scope:**
- Permission check on `GET /media/{media_id}`
- Permission check on `GET /media/{media_id}/cover`
- share_token validation against shares table

**Out of scope:**
- review_token validation (TODO placeholder)
- Legacy `GET /media/{file_path:path}` route (no permission check, backward compat)
- Frontend changes (already sends correct auth tokens)
- Changes to the share system itself

## Database Queries

### Ownership lookup (cacheable)
```sql
-- Get creator and team scopes for a resource
SELECT r.creator_id,
       array_agg(DISTINCT ri.scope_id) FILTER (WHERE ri.scope_type = 'team') as team_ids
FROM resources r
LEFT JOIN resource_items ri ON ri.resource_id = r.id AND ri.scope_type = 'team'
WHERE r.id = $media_id
GROUP BY r.creator_id;

-- If not found in resources, check parsed_media → resources link
SELECT r.creator_id,
       array_agg(DISTINCT ri.scope_id) FILTER (WHERE ri.scope_type = 'team') as team_ids
FROM resources r
LEFT JOIN resource_items ri ON ri.resource_id = r.id AND ri.scope_type = 'team'
WHERE r.media_id = $media_id
GROUP BY r.creator_id;
```

### Team membership check
```sql
SELECT 1 FROM team_members
WHERE team_id = ANY($team_ids) AND user_id = $user_id
LIMIT 1;
```

### Share validation (NOT cached)
```sql
SELECT id, resource_id, status, expires_at, max_views, view_count
FROM shares
WHERE share_code = $share_token AND status = 'active'
LIMIT 1;
```

## Files Changed

| File | Change |
|------|--------|
| `backend/app/api/media_permissions.py` | **New** — `check_media_access()` |
| `backend/app/main.py` | Add permission check to `/media/{media_id}` and `/media/{media_id}/cover` |
| `backend/app/main.py` | Modify `_authenticate_media_request()` to allow None user_id with share_token |
| `backend/app/main.py` | Extend `_media_path_cache` value to include creator_id + team_ids |
