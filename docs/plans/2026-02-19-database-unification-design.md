# Database Unification Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the dual media_*/resource_* table system, unify all FK references around `resources`, slim `parsed_media` to platform-snapshot-only, and standardize ID formats.

**Architecture:** Three-layer model: `parsed_media` (immutable platform snapshot) -> `resources` (physical file + user assets) -> `resource_items` (ownership/placement). All satellite tables (tags, summaries, transcripts, analysis, access logs) FK to `resources`.

**Tech Stack:** PostgreSQL (Supabase), SQL migrations

---

## Context

The database currently has two parallel systems causing frontend bugs:

1. **Old system** (media_*): `media_tags`, `media_summaries`, `media_transcripts`, `media_analysis`, `media_access_logs` -- all FK to `parsed_media`
2. **New system** (resource_*): `resource_tags` FK to `resources`

Frontend reads from one system while writes go to another, causing data inconsistency and bugs.

## Data Model Principles

```
parsed_media (platform snapshot - immutable)
    |
    v  1:1 FK
resources (physical file + user assets - mutable)
    |
    v  1:N FK
resource_items (ownership/placement - like hard links)
```

- **parsed_media**: Raw data from platform (Douyin, etc.). Immutable after initial parse. No user-editable fields.
- **resources**: Physical file + all user assets (notes, rating, AI processing status, tags). Mutable.
- **resource_items**: Placement in user libraries/folders. One resource can appear in multiple places.

## Changes

### A. parsed_media Slim-Down (58 -> ~33 columns)

**Delete these columns** (redundant or moved to resources):

| Column | Reason |
|--------|--------|
| `source_url` | Identical to `original_url` in all rows |
| `external_id` | Overlaps with `platform_id` |
| `transcript_bool` | Redundant with `transcript_status` (now on resources) |
| `summary_bool` | Redundant with `summary_status` (now on resources) |
| `notes` | User asset, already on resources |
| `transcript_status` | Moved to resources (migration 067) |
| `summary_status` | Moved to resources (migration 067) |
| `visual_analysis_status` | Moved to resources (migration 067) |

### B. media_* -> resource_* Unification

| Old Table | Action | New Table |
|-----------|--------|-----------|
| `media_tags` | Merge into `resource_tags`, DROP original | `resource_tags` (add `source`, `confidence` columns) |
| `media_summaries` | Rename, FK -> resources | `resource_summaries` |
| `media_transcripts` | Rename, FK -> resources | `resource_transcripts` |
| `media_analysis` | Rename, FK -> resources | `resource_analysis` |
| `media_access_logs` | Rename, FK -> resources | `resource_access_logs` |
| `media_collections` | DROP | (replaced by resource_items + folders) |
| `parsed_media_with_tags` VIEW | DROP | - |
| `media_statistics` VIEW | DROP, rebuild | `resource_statistics` VIEW |

### C. resource_tags Enhancement

Current schema: `(resource_id, tag_id)` composite PK

Add columns from media_tags merger:
- `source VARCHAR(50)` -- 'user' | 'ai' | 'system'
- `confidence FLOAT` -- AI confidence score (0.0-1.0), NULL for user tags
- `created_at TIMESTAMPTZ DEFAULT now()`

### D. ID Format Standardization

**Migrate to Snowflake BIGINT** (8 core tables):

| Table | Current | Reason |
|-------|---------|--------|
| `resource_items` | UUID | Core table, every resource open queries it |
| `resource_versions` | UUID | Version management, frequent queries |
| `tags` | UUID | Frequent JOINs with resource_tags |
| `unified_tasks` | UUID | Core business entity |
| `task_assets` | UUID | Queried with tasks |
| `notifications` | UUID | High-frequency writes |
| `user_logs` | UUID | High-volume logging |
| `search_logs` | UUID | High-volume logging |

**Optional migration** (lower priority, for consistency):

| Table | Current |
|-------|---------|
| `user_settings` | UUID |
| `access_overrides` | UUID |
| `share_views` | UUID |
| `point_packages` | UUID |
| `point_pricing` | UUID |
| `project_workflows` | UUID |
| `workflow_nodes` | UUID |
| `member_quotas` | UUID |

**Keep unchanged**:

| Table | Format | Reason |
|-------|--------|--------|
| `user_profiles` | UUID | Must match Supabase Auth |
| `system_status` | UUID | Internal, very low frequency |
| Composite PK tables | N/A | No single id column |
| `api_keys/logs/authors` | Sequential BIGINT | Already efficient |
| Tables being dropped | N/A | Will be removed |

### E. Migration Files

| Migration | Content |
|-----------|---------|
| `075_slim_parsed_media.sql` | Drop redundant columns from parsed_media |
| `076_unify_media_to_resource.sql` | Rename/merge media_* tables, update FKs |
| `077_enhance_resource_tags.sql` | Add source, confidence, created_at to resource_tags; migrate media_tags data |
| `078_standardize_ids.sql` | Migrate 8 core UUID tables to Snowflake BIGINT |
| `079_drop_legacy_views.sql` | Drop old views, create resource_statistics |

### F. Backend Changes

After migration, update backend code:
- Remove any imports/references to `media_tags`, `media_summaries`, etc.
- Update repository layer to use new table names (`resource_summaries`, etc.)
- Update any SQL queries referencing old table names

### G. Frontend Changes

- Update any direct Supabase queries referencing old table names
- Ensure `bigIntSafeFetch` handles newly-Snowflake-ID tables
- No major component changes needed (already using resources-centric model)

## Verification

1. Run all migrations on local Supabase
2. Verify existing data integrity (resource_items, resource_tags still work)
3. Backend starts without errors
4. Frontend builds and loads correctly
5. Resources page: create, read, update, delete all work
6. Tags: assign/remove tags works
7. Downloads: parsed_media data still accessible
