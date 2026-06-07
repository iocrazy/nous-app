# 100k-Record Scale-Readiness Audit (2026-06-07)

Trigger: Eagle library import (~100,000 records) pending. PostgREST hard-caps every
response at **1000 rows** (clamps even explicit `.limit(10000)`); the SQLAlchemy ORM
path has **no cap** (returns everything). So at 100k scale, every non-paginated list
query is wrong in one of two ways:

- **REST path** (frontend supabase-js direct, or backend supabase-py repo): silently
  truncates to 1000 → missing data, wrong counts, broken filters — invisibly.
- **ORM path** (`*_orm.py`, SQLAlchemy direct): returns all 100k → payload explosion /
  OOM / timeout, and diverges from the REST behavior it's meant to match.

Correct = real pagination (offset/keyset + bounded page size) OR server-side
aggregation (COUNT / GROUP BY in SQL, not fetch-all-then-count-in-Python).

This is INDEPENDENT of (but intersects) the ORM 2.0 rollout. The main library list
(`fetchLibraryPaginated`) is already correctly paginated — the gaps are secondary paths.

---

## Frontend (supabase-js direct, path A) — silent truncation at 1000

### RISK-BULK (list silently caps at 1000)
| file:line | function | table | @100k impact |
|---|---|---|---|
| services/resourceService.ts:434 | `fetchResources` | resource_items | Library/folder content caps at 1000 items per scope/folder |
| services/resourceService.ts:979 | `fetchDownloadedResources` | resource_items | Team download view stuck at 1000 |
| services/resourceService.ts:1111 | `fetchSmartFolderResources` | resource_items | Smart folders match ≤1000; results/count wrong |
| services/resourceService.ts:777 | `fetchTrashedResources` | resources | Recycle bin shows only 1000; bulk restore/purge misses rest |
| services/resourceService.ts:104 | `fetchFolderContents` | resource_items | Folder >1000 items truncates on view |
| components/DownloadsView/useDownloadsData.ts:28 | `useResourceDataMap` | resources | Decoration rows (rating/notes/AI status) truncate; + URL-length 502 risk |

### RISK-COUNT-VIA-IDS (build id-array from unbounded select → count/filter wrong)
| file:line | function | table | @100k impact |
|---|---|---|---|
| services/resourceService.ts:372 | `resolveTagIntersection` | resource_tags | AND-tag filter on tag with >1000 resources drops matches |
| services/dataService.ts:334 | `resolveLibraryTagIntersection` | resource_tags | Downloads tag filter incomplete |
| services/resourceService.ts:592 | `fetchResourceCount` (team) | resource_items | Wrong sidebar upload count |
| services/resourceService.ts:1009 | `fetchDownloadedResourceCount` (team) | resource_items | Wrong download count |

SAFE: `fetchLibraryPaginated` (keyset+limit), all `count:exact head:true` counts,
single-row resolvers, chunked `.in(ids)` (chunk 50). Note `fetchLibrary` caps local
search cache at `LOCAL_CACHE_SIZE=500` (client-side search only covers first 500).
Secondary risk: large `.in(...)` id lists hit Kong/nginx URL-length (502) BEFORE the 1000 cap.

Log/transcript/summary/analysis tables are NOT queried via supabase-js from the
frontend (they go through the backend apiClient) → no frontend truncation there.

---

## Backend repos (REST `*.py` + ORM `*_orm.py`)

### RISK-BOTH (REST truncates ≤1000 AND ORM unbounded — 20 methods)
| file:line (REST / ORM) | method | table | @100k impact |
|---|---|---|---|
| resources_repository.py:406 / _orm:781 | `get_resource_items` | resource_items⋈resources | REST: ≤1000 files shown; ORM: whole library into RAM |
| resources_repository.py:782 / _orm:955 | `get_trashed_resources` | resource_items⋈resources | trash view truncated / materialized whole |
| resources_repository.py:761 / _orm:934 | `get_expired_trashed_resources` | resources | GC sees only 1000 expired/run (slow drain) |
| resources_repository.py:898 / _orm:1105 | `get_untranscoded_video_versions` | resource_versions | transcode backlog stuck at 1000 |
| resources_repository.py:588 / _orm:563 | `_resource_ids_for_platforms` | parsed_media, resources | platform filter wrong / huge IN list |
| resources_repository.py:621 / _orm:593 | `_resource_ids_with_all_tags` | resource_tags | tag-AND filter silently wrong |
| admin/stats_repository.py:57 / _orm:132 | `user_registrations_since` | user_profiles | registration chart caps 1000 |
| admin/stats_repository.py:67 / _orm:142 | `video_status_history` | parsed_media | status-history chart undercounts |
| admin/stats_repository.py:77 / _orm:159 | `completed_videos_by_user` | resources | per-user storage stats wrong >1000 |
| admin/audit_logs_repository.py:60 / _orm:140 | `list_since` | audit_logs | audit stats truncate |
| admin/request_logs_repository.py:70 / _orm:169 | `stats_since` | api_request_logs | KNOWN seed — request stats cap 1000 |
| admin/alert_rules_repository.py:115 / _orm:278 | `request_status_codes` | api_request_logs | error-rate alert from ≤1000 rows (wrong alerts) |
| admin/alert_rules_repository.py:125 / _orm:288 | `request_response_times` | api_request_logs | p95 latency alert from ≤1000 samples |
| admin/credits_repository.py:254 / _orm:576 | `all_quotas_balances` | team_quotas | points-total undercounts >1000 teams |
| admin/credits_repository.py:259 / _orm:582 | `transactions_by_type` | point_transactions | aggregate over ≤1000 txns |
| admin/credits_repository.py:271 / _orm:594 | `orders_by_status` | orders | revenue/order aggregates cap 1000 |
| admin/credits_repository.py:347 / _orm:660 | `revenue_chart_rows` | orders | revenue chart undercounts |
| admin/transcode_repository.py:149 / _orm:274 | `list_versions_for_batch` | resource_versions | batch transcodes only 1000/run (silent partial) |
| analysis_repository.py:110 / _orm:149 | `get_videos_without_analysis` (inner) | resource_analysis | re-queues already-analyzed videos >1000 |

### RISK-REST-TRUNCATE (REST clamps; ORM honors higher limit → divergence — 10 methods)
| file:line | method | table | @100k impact |
|---|---|---|---|
| admin/search_repository.py:21/39/54/69 | `request_logs`/`app_logs`/`frontend_logs`/`audit_logs` | log tables | `.limit(2000)`→1000; ORM honors 2000 → half the window |
| admin/monitoring_repository.py:27/45 | `request_logs_between`/`app_logs_between` | api_request/app_logs | `.limit(10000/5000)`→1000; dashboard drops ~90% |
| logs_repository.py:115 | `get_logs_for_export` | user_logs | "export all" exports only 1000 |
| media_repository.py:405 | `get_pending_downloads` (inner) | resources | pending filter from truncated id set |
| tags_repository.py:359 | `_get_tag_counts_fallback` | resources, resource_tags | tag counts undercount (RPC-fallback only) |
| admin/tags_repository.py:118 | `all_tag_group_ids` | tags | group-id set truncates if tags >1000 |

Total at-scale-unsafe backend list methods: **30** (20 both-impl + 10 REST-only).

---

## Remediation tiers

**Tier 1 — user-facing, breaks visibly at Eagle import (do first):**
frontend bulk lists + counts + tag filters (10 sites) and their backend twins
`get_resource_items` / `get_trashed_resources` / `_resource_ids_with_all_tags`.
Fix = real pagination + server-side COUNT + server-side tag intersection. Also fix the
`.in(...)` URL-length 502 risk (chunk or move server-side).

**Tier 2 — admin dashboards compute wrong numbers (even today):**
admin/stats, credits, alert_rules, search, monitoring, request_logs aggregates.
Fix = SQL aggregation (COUNT/GROUP BY) instead of fetch-all-then-aggregate-in-Python.
This is a behavioral FIX (not an inert ORM copy) — the 1000-truncation is a latent bug.

**Tier 3 — GC/batch sweepers process ≤1000/run:**
get_expired_trashed_resources, get_untranscoded_video_versions, transcode batch,
analysis already-analyzed set. Fix = loop/paginate or raise the working limit.

## Reframe for the ORM rollout
For these methods, do NOT make ORM "inertly replicate" the 1000 truncation (that
preserves the bug). Migrate them scale-correct: list methods → pagination; admin
aggregates → SQL aggregation. Each is a tested behavioral change, not blind parity.
The remaining (small-table / already-paginated) ORM methods stay inert.
