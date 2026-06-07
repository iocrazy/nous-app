# Tag Merge — Design Spec

Date: 2026-06-07
Branch: `feature/tag-merge`
Status: Awaiting user review

## Problem

Users accumulate near-duplicate tags (e.g. `提示` and `提示词`, `Prompt` and a
later-renamed `Prompt`). There is no way to fold one tag into another. Renaming
only changes a label; it cannot consolidate two existing tags and their tagged
resources into one.

We need: select N tags, merge them into one surviving tag, and have every
resource that carried any of the merged tags end up carrying the survivor —
with no duplicate associations and no orphaned tags.

## Decisions (confirmed with user)

| Decision | Choice |
|----------|--------|
| Surface | EagleTagPicker (the resource-library tag filter/browse sidebar) |
| Selection model | Reuse the picker's existing selection set as the merge set (most Eagle-like) |
| Trigger | Right-click any tag → context menu shows **"Merge N Tags"** when ≥2 user tags are selected |
| Survivor | User picks one of the selected tags as the target; default = most-used (`media_count` desc) |
| Confirmation | Confirm dialog (lists selected, target radio, preview); **no undo** |
| Backend atomicity | Single `SECURITY DEFINER` Postgres RPC in one transaction |

## Non-goals (YAGNI)

- No undo / soft-delete / "which resources came from which tag" history.
- No dedicated full-page tag management view (considered, rejected as too big).
- No merge from TagsSettings or the Shortcuts page (EagleTagPicker only for now;
  backend is surface-agnostic so a second entry point is cheap later).
- No rename/group/star batch actions in the same menu (merge only this round).

## Data model (verified against prod)

- `tags.id` — `bigint` (Snowflake). `resource_tags.tag_id` — `bigint`.
- `resource_tags` PRIMARY KEY `(resource_id, tag_id)`; `tag_id` FK → `tags(id)`
  `ON DELETE CASCADE`. Re-pointing a source tag onto the target can collide with
  the PK when a resource already carries both → must `ON CONFLICT DO NOTHING`.
- `media_count` is computed only on the list endpoint (RPC `get_tag_counts_by_ids`),
  not stored on the row.

## Backend

### Migration — `265_merge_tags_rpc.sql` (next free number; bump if taken at impl time)

`SECURITY DEFINER` function, one transaction:

```
merge_tags(p_target bigint, p_sources bigint[], p_user uuid) RETURNS bigint
```

1. **Validate.** Target and every source must be `type='user'` and `user_id = p_user`.
   Raise (→ 4xx) otherwise. `p_target` must NOT appear in `p_sources`. `p_sources`
   non-empty.
2. **Re-point with dedup.**
   `INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
    SELECT resource_id, p_target, source, confidence, created_at
    FROM resource_tags WHERE tag_id = ANY(p_sources)
    ON CONFLICT (resource_id, tag_id) DO NOTHING;`
3. **Delete source tags.** `DELETE FROM tags WHERE id = ANY(p_sources)` — cascades
   away the now-redundant source `resource_tags` rows.
4. **Return** `SELECT COUNT(*) FROM resource_tags WHERE tag_id = p_target` (final
   resource count on the survivor, for the UI toast / optimistic count).

Exposed via PostgREST RPC (wrapped per `reference_postgrest_rpc` — named params,
public schema). Why RPC: steps 2–4 must be atomic (all-or-nothing), and it is one
round trip instead of N.

### Repository — `tags_repository.py`

```python
async def merge_tags(self, target_id, source_ids, user_id) -> int:
    # coerce ids to bigint (snowflake-as-str → int), call rpc("merge_tags", {...})
    # return the resulting target resource count
```

### Router — `tags_router.py`

`POST /api/v1/tags/merge` body `MergeTagsRequest { target_id: str, source_ids: list[str] }`,
`user_id` from auth. Returns `{ target_id, resource_count }`. Maps validation
raises to 400/403.

## Frontend

### Selection model

No change to click behavior. The picker already accumulates clicked tags in
`selectedIds` (filter/assign set). Those selected tags ARE the merge set. (We keep
toggle-on-click rather than Eagle's plain-click-replaces + Ctrl-adds, because that
would break existing multi-tag filtering — clicking several tags already
multi-selects without needing Ctrl. **Flag for review.**)

### Components

- `TagContent.tsx` — the existing right-click context menu (currently only
  Star/Unstar) gains a **"Merge N Tags"** item, shown only when:
  `selectedIds.size >= 2` AND all selected tags are `type='user'`. Clicking it
  calls a new `onRequestMerge()` callback.
- New `MergeTagsDialog.tsx` — receives the selected `Tag[]`. Renders:
  - the N tags with their counts,
  - a radio group to choose the target (default = highest `media_count`),
  - preview line: "Up to {sumOfSourceCounts} resources move to «{target}». {N-1} tags deleted.",
  - Cancel / **Merge** (danger). Calls the service, then `onMerged()`.
- Callback plumbing: `EagleTagBrowser` gains optional `onMergeTags?(targetId, sourceIds)`;
  `TagContent` gets `onRequestMerge`. The dialog mounts at the picker top level
  (`index.tsx` / `FloatingPanel`), where `allTags` is owned and refetchable.
- `unifiedTagService.mergeTags(targetId, sourceIds)` → `POST /tags/merge`,
  invalidates the tags cache.
- i18n keys (`en.json` + `zh.json`): `tags.merge`, `tags.mergeNTags`,
  `tags.mergeTarget`, `tags.mergePreview`, `tags.mergeConfirm`.

### Data flow after merge

On success: parent updates selection to `[targetId]` (deleted tags are gone) via
`onTagsChange`, and refetches `allTags` (counts refresh, sources vanish). Surface a
success toast with the returned resource count.

### Error handling

- Merge button disabled when `<2` selected, when any selected tag is non-user, or
  when no target chosen.
- Backend validation raise → toast the message, leave selection/state intact.
- Transaction rollback on any failure → no partial merge; frontend does not refetch.

## Testing

- **Backend (pytest):** RPC behavior — re-point moves resources; dedup when a
  resource has both source and target; source tags deleted; survivor count correct;
  reject when a source/target is not the caller's user tag; reject target ∈ sources.
- **Frontend (vitest):** extract the pure pieces of the dialog —
  `pickDefaultTarget(tags)` (highest media_count) and `canMerge(selected, targetId)`
  (≥2, target set, all user) — and unit-test them. Service test mocks fetch.

## Open questions for review

1. Keeping toggle-on-click (no Ctrl) vs Eagle's plain-click-replaces — OK?
2. Preview wording: show "up to N" (pre-dedup upper bound) vs a second round trip
   to compute the exact post-dedup count before confirming. Recommend "up to N".
3. Should the survivor inherit anything from sources (e.g. union of nothing —
   color/group/name all stay the target's)? Recommend: target unchanged.
