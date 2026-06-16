# Unified Memory Embedder Config — Design

**Date:** 2026-06-16
**Status:** Draft (awaiting user review)

## Problem

The two memory subsystems embed text with separately-configured embedders:

- **Graphiti** (graph memory, FalkorDB backend) — embedder config already lives in
  `system_settings` (`graph_embedder_base_url` / `graph_embedder_api_key` /
  `graph_embedder_model`), editable in the Admin → Memory panel. But there is **no
  `dimensions` field**: the index dimension is implicit (Graphiti's 1536 default).
- **Honcho** (user-model service, pgvector backend) — embedder config is **not managed
  by mediahub at all**. Honcho reads its own container `.env`
  (`EMBEDDING_*` / `VECTOR_STORE_DIMENSIONS`). mediahub only knows Honcho's
  `base_url` / `workspace` / feature flag.

This means the embedder is configured in two unrelated places with no single source of
truth, and the dimension — the value that decides whether a future model upgrade is even
possible — is invisible and unmanaged.

## Verified constraints (dev, 2026-06-16)

- **pgvector HNSW caps at 2000 dimensions** (8KB index-page limit; `HNSW_MAX_DIM 2000`).
  Honcho's default backend is pgvector → its embedder dimension must be **≤ 2000**.
  Setting 4096 makes `configure_embeddings.py` fail and crashes Honcho's boot validator.
- **FalkorDB has no practical dimension ceiling** — index creation at 4096 *and* 8192
  succeeded on the dev instance. Graphiti can run any dimension.
- **Honcho's LanceDB backend can run 4096** (`fixed_size_list(float32, DIMENSIONS)`, no
  page limit) — but switching Honcho off pgvector is a backend migration (`MIGRATED`
  flag + reconciler + volume), out of scope here.
- Current running state: **both subsystems are on 4B @ 1536** (Graphiti implicit 1536,
  Honcho `.env` = 1536 via ModelScope Qwen3-Embedding-4B). The chosen default does **not**
  change the running dimension.

## Decision: model & dimension

Default the unified embedder to **Qwen3-Embedding-4B @ 1536 via ModelScope** (stable,
already running). The 8B@4096 upgrade path stays open via the new `dimensions` field, but
is deferred — its blocker is now endpoint reliability (the 8B `api.iocrazy.com` endpoint
returned 503 under test), not the dimension ceiling.

## Scope

### In scope (this iteration — pure mediahub code, no NAS mutation)

1. **`dimensions` field on the Graphiti embedder config.** Added to
   `GraphMemoryConfig`, `_SETTINGS_MAP`, the admin schema/endpoint, and the panel.
   Passed into Graphiti's `OpenAIEmbedderConfig(embedding_dim=…)` so the FalkorDB index
   dimension — and the `dimensions` param sent to the embedding API (MRL truncation for
   Qwen3) — are admin-controlled, not implicit. Default **1536**.

2. **Dimension guardrail.** The admin PUT rejects `dimensions` outside **[1, 2000]** with
   a 422 explaining the pgvector HNSW cap. This is a global guard: while Honcho is on
   pgvector, the shared embedder dimension cannot exceed 2000. (Raising the cap is part of
   the deferred LanceDB-migration work.)

3. **Admin panel reframing.** The "Embedder (OpenAI-compatible)" block in
   `MemorySettings.tsx` gains a Dimensions input and a one-line note clarifying it is the
   **shared memory embedder** (Graphiti live; Honcho via the documented sync procedure).

4. **Runbook.** `backend/docs/runbook/memory-embedder.md` documents: `system_settings` is
   the single source of truth; Graphiti consumes it live (no restart); Honcho's `.env` is
   a *materialization* of the same values; the exact manual procedure to re-sync Honcho
   when the dimension changes (render `.env` → `configure_embeddings.py` → restart), with
   the ≤2000 constraint called out.

### Out of scope (deferred to the actual 8B upgrade)

- In-app "Apply to Honcho" automation (backend SSHing into the NAS to rewrite Honcho's
  `.env` and restart its container). High operational risk, zero current benefit because
  both subsystems already run 4B@1536. Deferred until a dimension change is actually
  wanted.
- Honcho LanceDB backend migration (required only for >2000 dimensions).
- Per-user embedder choice. The embedder is platform-global by nature: each subsystem
  stores all users' vectors in one shared fixed-dimension column, so the dimension cannot
  vary per user. (This is why the embedder is admin-only, unlike the per-module
  user-facing AI governance.)

## Data flow

```
Admin → Memory panel  ──PUT /admin/settings/graph-memory──▶  system_settings
                                                              (graph_embedder_*  +  graph_embedder_dimensions)
                                                                      │
                              ┌───────────────────────────────────────┴───────────────────────────┐
                              ▼ (live, per-process on first use)                                    ▼ (manual, runbook)
                   GraphMemoryConfig.from_settings()                                   render Honcho .env from the
                   → OpenAIEmbedderConfig(embedding_dim=dimensions)                    same values → configure_embeddings.py
                   → FalkorDB index sized to `dimensions`                              → restart Honcho
```

## Components touched

**Backend**
- `app/services/ai/memory/graph_memory.py` — add `embedder_dimensions: int` to
  `GraphMemoryConfig` (default 1536); add `"embedder_dimensions"` →
  (`graph_embedder_dimensions`, `GRAPH_EMBEDDER_DIMENSIONS`) in `_SETTINGS_MAP`; resolve
  it in `from_settings` (int parse, fall back to 1536 on bad value); pass
  `embedding_dim=config.embedder_dimensions` into `OpenAIEmbedderConfig` in
  `_build_llm_and_embedder` (only when an embedder key is set, as today).
- `app/schemas/admin.py` — add `embedder_dimensions` to `GraphMemorySettingsResponse`
  (int) and `GraphMemorySettingsUpdate` (optional int).
- `app/api/admin/settings_router.py` — add `embedder_dimensions` to `_GRAPH_FIELD_TO_KEY`
  (key `graph_embedder_dimensions`); validate `1 ≤ dimensions ≤ 2000` in the PUT (422 on
  violation, before any write); include it in `_read_graph_settings` (default "1536").
- `supabase/migrations/NNN_seed_graph_embedder_dimensions.sql` — seed
  `graph_embedder_dimensions` = `'1536'` (mirrors how 291 seeded the other graph_* keys so
  `repo.update` finds an existing row). Idempotent (`ON CONFLICT DO NOTHING`).

**Admin frontend**
- `admin/src/api/endpoints/settings.ts` — add `embedder_dimensions` to the response &
  update types.
- `admin/src/pages/settings/MemorySettings.tsx` — add the Dimensions input (numeric,
  validated ≤2000 client-side as a hint) + the "shared memory embedder" note.

**Docs**
- `backend/docs/runbook/memory-embedder.md` (new).

## Error handling

- `from_settings` already degrades to env/defaults on a broken settings table; the new
  int parse follows the same pattern (bad value → 1536, never raises).
- The guardrail rejects out-of-range dimensions at the API boundary (422), before
  persisting, so a typo can't silently break the FalkorDB index dimension.
- The embedder is only built when a key is configured (unchanged) — the dimensions field
  is inert until the embedder is actually used.

## Testing

- **Unit (backend):** `from_settings` resolves `embedder_dimensions` from DB > env >
  default; bad/empty value → 1536; `_build_llm_and_embedder` passes `embedding_dim` into
  `OpenAIEmbedderConfig` when a key is set, and builds nothing when no key.
- **Unit (router):** PUT with `dimensions=4096` → 422; `dimensions=1536` → persisted;
  `dimensions` omitted → other fields still written; GET masks keys and returns
  `embedder_dimensions`.
- **Frontend:** type-check + build (admin app). No new runtime test infra.
- **Manual:** admin saves dimensions, GET round-trips it; Graphiti rebuild picks up the
  new dim (verified later against dev when the gate is exercised).

## Migration / rollout

Pure-additive: a new seeded key + new optional fields. Default 1536 = current behavior, so
zero behavior change on deploy. The runbook is the only thing the operator must follow when
an actual dimension change is wanted later.
