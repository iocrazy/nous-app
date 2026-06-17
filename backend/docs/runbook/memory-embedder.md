# Memory Embedder — single source of truth & Honcho sync

The two memory subsystems (Graphiti graph memory, Honcho user-model) embed text
with the **same logical embedder**. Its configuration lives in **one place** —
`system_settings`, edited from **Admin → Settings → Memory**.

| Field | system_settings key | Default |
|-------|--------------------|---------|
| Base URL | `graph_embedder_base_url` | (empty → env/extractor fallback) |
| API key | `graph_embedder_api_key` | (empty) |
| Model | `graph_embedder_model` | (empty) |
| **Dimensions** | `graph_embedder_dimensions` | `1536` (code default); **prod runs `4096`** |

> **Prod state (2026-06-17): Qwen3-Embedding-8B @ 4096.** Graphiti indexes 4096 on
> FalkorDB; Honcho was migrated off pgvector onto its **LanceDB** backend to index
> 4096 (see "Honcho LanceDB backend" below). The `1536` code default is the
> conservative fallback for a fresh install.

## How each subsystem consumes it

- **Graphiti** reads `system_settings` **live** (`GraphMemoryConfig.from_settings`,
  loaded per-process on first use). `dimensions` is passed into
  `OpenAIEmbedderConfig(embedding_dim=…)`, which sizes the FalkorDB vector index and
  the `dimensions` value requested from the embedding API. **No restart needed** —
  a new process picks up the change; an already-built client is rebuilt on the next
  cold start.

- **Honcho** is a third-party service that reads its **own container `.env`**
  (`EMBEDDING_*`, `VECTOR_STORE_*`); it cannot read mediahub's database. Its `.env` is
  therefore a **materialization** of the same values above. When the embedder
  model/dimension changes, the Honcho env must be re-synced **manually** (procedure
  below). This is deliberate — auto-syncing from the app would mean the backend
  mutating a third-party container and restarting it, which is not built.

## The dimension cap (4096)

The admin panel **rejects any `dimensions` > 4096** (HTTP 422) — 4096 is
Qwen3-Embedding-8B's native width, the largest embedder we use. Both backends index
4096: Graphiti on **FalkorDB** (no dim ceiling — verified to build indexes at 4096
and 8192) and Honcho on its **LanceDB** backend (`fixed_size_list(float32, N)`, no
page limit).

History: the cap used to be **2000**, because Honcho ran on **pgvector** and
pgvector's HNSW index is hard-capped at 2000 dimensions (8KB index-page limit).
Migrating Honcho to LanceDB (below) removed that ceiling.

## Honcho LanceDB backend (how it's set up)

Honcho supports `VECTOR_STORE_TYPE` ∈ {`pgvector`, `turbopuffer`, `lancedb`}.
To index >2000 dims it runs on **lancedb** (embedded, file-based; the `lancedb`
package ships in the Honcho image). Its `.env` (dev stack `honcho-dev/.env`):

```
EMBEDDING_MODEL_CONFIG__MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_VECTOR_DIMENSIONS=4096          # authoritative (VECTOR_STORE_DIMENSIONS is deprecated)
VECTOR_STORE_TYPE=lancedb
VECTOR_STORE_MIGRATED=true                # else _uses_pgvector() stays true and queries hit pgvector
VECTOR_STORE_LANCEDB_PATH=/app/lancedb_data
```

Compose: a **shared** host volume `…/honcho-dev/lancedb_data:/app/lancedb_data` is
mounted into **both** the `api` and `deriver` services (they must see the same lance
files). Data flow: the deriver writes document/observation rows to the pgvector
`documents` table (embedding column left NULL), and the **reconciler `sync_vectors`
task** (300s interval) embeds them via the configured embedder and writes the vectors
to LanceDB at the configured dim.

Boot gotcha: `src/startup/embedding_validator.py` **always** asserts the pgvector
`documents` / `message_embeddings` column dim == `EMBEDDING_VECTOR_DIMENSIONS`, even
on the lancedb path — so those columns must be `vector(4096)` or the api crash-loops.
Since the HNSW index can't be 4096, drop the index and ALTER the column with no index:

```sql
DROP INDEX IF EXISTS ix_documents_embedding_hnsw;
DROP INDEX IF EXISTS ix_message_embeddings_embedding_hnsw;
ALTER TABLE documents          ALTER COLUMN embedding TYPE vector(4096) USING NULL;
ALTER TABLE message_embeddings ALTER COLUMN embedding TYPE vector(4096) USING NULL;
```

Synology networking note: the NAS **host shell** cannot curl Honcho's published port
(`192.168.50.9:18000` → 503 "Unable to connect", a host↔docker-bridge hairpin quirk),
but **containers can** — the mediahub backend container reaches it at 200. Test Honcho
connectivity from a container, not the host shell.

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
