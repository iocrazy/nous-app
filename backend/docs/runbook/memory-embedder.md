# Memory Embedder — single source of truth & Honcho sync

The two memory subsystems (Graphiti graph memory, Honcho user-model) embed text
with the **same logical embedder**. Its configuration lives in **one place** —
`system_settings`, edited from **Admin → Settings → Memory**.

| Field | system_settings key | Default |
|-------|--------------------|---------|
| Base URL | `graph_embedder_base_url` | (empty → env/extractor fallback) |
| API key | `graph_embedder_api_key` | (empty) |
| Model | `graph_embedder_model` | (empty) |
| **Dimensions** | `graph_embedder_dimensions` | `1536` |

## How each subsystem consumes it

- **Graphiti** reads `system_settings` **live** (`GraphMemoryConfig.from_settings`,
  loaded per-process on first use). `dimensions` is passed into
  `OpenAIEmbedderConfig(embedding_dim=…)`, which sizes the FalkorDB vector index and
  the `dimensions` value requested from the embedding API. **No restart needed** —
  a new process picks up the change; an already-built client is rebuilt on the next
  cold start.

- **Honcho** is a third-party service that reads its **own container `.env`**
  (`EMBEDDING_*`, `VECTOR_STORE_DIMENSIONS`); it cannot read mediahub's database.
  Its `.env` is therefore a **materialization** of the same values above. When the
  embedder model/dimension changes, the Honcho env must be re-synced **manually**
  (procedure below). This is deliberate — auto-syncing from the app would mean the
  backend mutating a third-party container and restarting it, which is not built.

## The 2000-dimension cap (why it exists)

Honcho's default vector backend is **pgvector**, and **pgvector's HNSW index is
hard-capped at 2000 dimensions** (8KB index-page limit). The shared embedder feeds
that index, so the admin panel **rejects any `dimensions` > 2000** (HTTP 422).

- `1536` (Qwen3-Embedding-4B native) — current default, fits comfortably.
- `4096` (Qwen3-Embedding-8B native) — **does not fit pgvector HNSW**. To use it you
  must either truncate to ≤2000 via the embedder's `dimensions` param (MRL), or
  migrate Honcho off pgvector (LanceDB / Turbopuffer), which is a separate project.

FalkorDB (Graphiti's backend) has **no** such limit — it indexes 4096+ fine. The cap
exists only because the embedder is shared and Honcho is the lower ceiling.

## Procedure: change the embedder (incl. dimension)

1. **Admin → Settings → Memory → Embedder.** Set Base URL / Model / API key /
   Dimensions. Save. (Dimensions > 2000 is rejected.) Graphiti now uses the new
   config on its next cold start.

2. **Sync Honcho's env** (only if the model/dimension changed). On the NAS, in the
   Honcho compose dir (`/volume1/docker/mediahub/honcho-dev/` on dev):
   - Update the embedder vars in Honcho's `.env`
     (`EMBEDDING_OPENAI_BASE_URL`, `EMBEDDING_OPENAI_API_KEY`,
     `EMBEDDING_MODEL`, `EMBEDDING_VECTOR_DIMENSIONS`) to match the values above.
   - If the **dimension** changed, run Honcho's column-resize tool inside the api
     container **before** restart, or its boot validator will crash on the
     env↔column mismatch:
     ```
     sudo docker exec <honcho-api-container> python scripts/configure_embeddings.py --yes
     ```
     This ALTERs the pgvector columns to the new dimension. It **fails** (and rolls
     back) for any dimension > 2000 — confirm ≤2000 first.
   - Recreate the containers to load the new env:
     ```
     sudo docker compose up -d
     ```
   - Verify the api container is `healthy` and the column dim matches:
     ```
     sudo docker exec <honcho-db-container> psql -U postgres -d honcho \
       -c "SELECT atttypmod FROM pg_attribute WHERE attrelid='public.documents'::regclass AND attname='embedding';"
     ```

3. **Re-embed if needed.** Changing the model/dimension invalidates existing vectors
   (they were embedded by the old model at the old width). Graphiti and Honcho will
   embed *new* content with the new embedder; old content keeps its old vectors and
   becomes mismatched. For a clean cut, clear the relevant graphs/tables — only worth
   it when the embedder change is significant.

## Recovery

If Honcho's api container is `unhealthy` after an env change, the usual cause is an
`EMBEDDING_VECTOR_DIMENSIONS` that doesn't match the physical column dimension (the
boot validator crashes on mismatch). Restore the previous `.env` (a `.env.bak.*`
backup) and `docker compose up -d`, or run `configure_embeddings.py --yes` to ALTER
the columns to the env value (≤2000 only).
