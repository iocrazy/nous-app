# Unify ALL AI Config to Settings (Zero env/file) — Design

**Goal:** Every AI-using capability resolves its provider / model / key / base_url from **`system_settings` (DB)** — never from env vars, `config.py` defaults, `config.yml`, or hardcoded constants. Exactly **two config surfaces**: **Admin** (governance lock + platform config + nous control) and **User** (per-module BYOK / model selection).

**Approved scope (2026-06-23):**
- **Model config only.** In scope: provider/model/key/base_url for every AI capability + the nous-center gateway + image/video/embedding model config. Out of scope (stays deploy env): pure service infra (FalkorDB / Honcho / Langfuse hosts), AI feature flags, concurrency/timeout tuning params. These are not "model config" and changing them requires the service to exist.
- **image_gen / video_gen → new governed modules** (admin lock + user BYOK + nous), same shape as the existing 8.
- **Embedding → one unified platform `embedding` config** (search + analyze + memory share it), nous-eligible; kills `embedding_service` `OPENAI_*` env and merges with the existing `graph_embedder_*`.

---

## Current state (audited 2026-06-23)

**Governed today (8 modules):** `transcription, translation, visual_analysis, caption, classification, summarization, topic_scorer, chat` — each via `ai_governance.get_module_governance` + `resolve_task_provider_config` + user BYOK in `user_settings.settings_json.ai_settings`.

**Config surfaces today:**
- Admin: `GET/PUT /admin/settings/ai-governance` (keys `ai_module.<m>.{user_allowed,nous_allowed,base_url,model,api_key}` + `nous.user_enabled`), `nous_models` CRUD, `GET/PUT /admin/settings/graph-memory` (keys `graph_*`). UI: `admin/src/pages/settings/AIGovernance.tsx`, `admin/src/pages/ai/index.tsx`.
- User: `GET/PUT /ai/settings`, `GET /ai/governance`, `GET /ai/nous-models`. UI: `frontend/components/AISettings.tsx`.

**Gaps (non-governed AI consumers reading env/file/agent-composed):**
| Consumer | Reads from | Target |
|---|---|---|
| Canvas `image_gen` / `video_gen` nodes; storyboard `generate_image/video`; `generate_media_tools` | node `data`; `GENMEDIA_DEFAULT_*` env | **new governed modules** image_gen/video_gen |
| `embedding_service.py` | `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL` env | **platform `embedding` config** |
| `graph_memory` embedder | `graph_embedder_*` settings + env fallback | merge into platform `embedding` |
| nous-center runner | `NOUS_CENTER_BASE_URL/TOKEN/POLL_MS/MAX_WAIT_S` env | **platform `nous_center` config** |
| canvas derive slugs | `NOUS_CENTER_OUTPAINT_SLUG` env | **platform `canvas_workflows` config** |
| storyboard `_call_llm` | `settings.LLM_API_URL/KEY/MODEL` | **platform `internal_llm` config** (script-split / video-analysis) |
| chat compaction / summarizer | hardcoded `qwen-turbo/qwen-max`, `COMPACTION_PROVIDER` env | **platform `internal_llm` config** |

**Demolition list:** ~20 `config.py` AI fields, ~28 env reads, ~13 hardcoded model names (full list in the audit; tracked per phase below).

---

## Architecture: two config kinds

The audited items split into two governance kinds. Both live in `system_settings`; both surface in admin; only the first surfaces per-user.

### Kind A — per-module governed config (user-facing AI capabilities)
Shape per module (unchanged from today): `ai_module.<m>.user_allowed` (admin lock) + `ai_module.<m>.{base_url,model,api_key}` (admin platform config, used when locked) + `ai_module.<m>.nous_allowed` + user BYOK/`task_assignment`. Resolution: `resolve_task_provider_config(user_id, module, default_agent_slug)`.

**Modules after this epic (10):** the 8 today **+ `image_gen` + `video_gen`**.

### Kind B — platform-only config (admin-set, no per-user override)
Admin-only platform settings, `system_settings`, surfaced in a new **admin "Platform AI" panel** (sibling of AI Governance). New helper `get_platform_ai_config(section)` (mirrors `graph_memory.from_settings`: DB-only, no env fallback once seeded, never raises). Sections:
- `embedding` — base_url / model / api_key / dimensions (+ nous-eligible). Single source for search, analyze post-embedding, and graph memory embedder.
- `nous_center` — base_url / token / poll_ms / max_wait_s / max_wait_s_workflow.
- `canvas_workflows` — outpaint_slug (+ future crop/split slugs).
- `internal_llm` — base_url / api_key / model for storyboard `_call_llm` (script-split, video-analysis) + chat compaction/summarizer model.

> **Why DB-only, no env fallback:** the directive is a hard "no env." Each migration **seeds the current env/`config.py` value as the DB default**, so seed-time behavior is byte-identical; then the env read is deleted. `is_configured()`-style guards keep a missing-DB deploy from crashing (degrade, never raise) exactly like governance does today.

---

## Files to touch to register a new governed module (from audit)
`ai_governance.py` TASK_MODULES · `admin/settings_router.py` (`_TASK_MODULE_NAMES`, read/update, secret keys) · `schemas/admin.py` (Response/Update) · `ai_settings_router.py` (`/governance` loops ALL_MODULES — auto) · `admin/.../AIGovernance.tsx` (module list) · `schemas/ai.py` task_assignment default · `frontend/AISettings.tsx` rows · seed migration. Service code calls `resolve_task_provider_config(user_id, module, slug)`.

---

## Phasing (each phase = one tested, behavior-preserving PR; defaults seeded from current values = zero behavior change at merge)

- **Phase 0 — Platform-config foundation.** `get_platform_ai_config(section)` helper + admin `GET/PUT /admin/settings/platform-ai` + seed migration (all sections seeded from current env/config.py values) + admin "Platform AI" panel skeleton. No consumer rewired yet → pure no-op infra. Tests: helper read/mask/degrade; endpoint round-trip.
- **Phase 1 — image_gen / video_gen governed modules.** Register both modules (all 8 files above) + resolver wiring; rewire canvas `image_gen`/`video_gen`, storyboard `generate_image/video`, `generate_media_tools` to `resolve_task_provider_config` (kill `GENMEDIA_DEFAULT_*` env). User+admin UI rows. Tests: governance gate, BYOK, nous, locked-fallback.
- **Phase 2 — Unified embedding.** Wire `embedding_service` + analyze post-embedding + graph memory embedder to platform `embedding` config (kill `OPENAI_API_KEY`/`OPENAI_EMBEDDING_MODEL` env in embedding_service; converge graph_embedder onto the same section). Tests: all three consumers resolve from one section; nous-eligible.
- **Phase 3 — nous-center + canvas workflows.** nous-center runner + verify + canvas derive slugs read platform `nous_center` / `canvas_workflows` (kill `NOUS_CENTER_*` env). Tests: runner builds client from DB; missing-DB degrades.
- **Phase 4 — internal LLM.** storyboard `_call_llm` + chat compaction/summarizer read platform `internal_llm` (kill `settings.LLM_*` reads + hardcoded `qwen-turbo/qwen-max` + `COMPACTION_PROVIDER` env). Tests: each resolves from DB.
- **Phase 5 — Demolition + verify.** Delete the now-dead `config.py` AI fields + env reads + hardcoded defaults; confirm no remaining AI env read (grep gate in CI optional); prod seed verification. Tests: a guard test asserting no AI provider/model/key is read from env.

**Sequencing:** Phase 0 first (foundation). Phases 1–4 are independent and can ship in any order after 0. Phase 5 last (only after every consumer is rewired).

---

## Risks / guards
- **Seed = current value** so every phase is a no-op at merge; behavior changes only when an admin later edits settings. Verified per-phase by parity tests.
- **Secrets:** every `*.api_key` / `*.token` is write-only (masked on GET, blank-keeps-existing on PUT), audit never logs them — same contract as `ai_module.*.api_key` + `graph_*_api_key` today.
- **Degrade-not-crash:** missing DB / unconfigured engine → helper returns empty/defaults, never raises (mirrors `get_module_governance`).
- **`system_settings.value` is JSONB** → bools come back native Python `bool`; gates use `if not allowed`, never string compare (known footgun).
- **Migration apply discipline:** seed migrations `-U postgres` pre-apply on prod before the consuming backend deploys (avoids the new-code-before-migration race), idempotent CI replay.
- **Out-of-scope creep:** infra hosts (FalkorDB/Honcho/Langfuse) + feature flags + concurrency params explicitly NOT migrated this epic.
