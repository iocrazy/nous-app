# Nous Platform Provider (user-side) — Design

**Status:** Approved design, pending implementation plan
**Date:** 2026-06-19

## Goal

Surface admin-configured platform AI models (the `nous_models` registry) on the **user side** as a single **"Nous" provider**, so users can select and use platform models in each feature area. Selected Nous models run on the **platform's API key** (the user supplies no key). Prompt customization is preserved by keeping the existing **agent** layer: an agent's underlying model can be a Nous model.

**Out of scope (v1):** billing. There is exactly ONE points system today (`team_quotas.points_balance` + transactions + daily free points); the "charge points per nous usage" logic does **not** exist (only `nous_models.pricing_*` columns sit unused). v1 runs Nous models on the platform key with **no charge and no usage metering** — that is the separate, still-unbuilt *Nous Models & AI Billing* project.

## Background (current state)

- **Admin** configures platform models in the `nous_models` table (`name`, `display_name`, `category`, `actual_provider`, `actual_model`, `api_key` [masked], `base_url`, `is_enabled`, `sort_order`, pricing fields). Admin API: `/admin/nous-models`. Admin UI: `admin/src/pages/settings/` nous config.
- A **public endpoint already exists** — `GET /api/v1/ai/nous-models` (returns enabled models, no keys) — **but no frontend UI consumes it.**
- **User-side selection is agent-centric:** `user_settings.settings_json.ai_settings.task_assignment[module]` holds an **agent slug** for LLM modules (summarization/visual_analysis/translation/caption/classification) and a `provider:model` string for **transcription**. Agents (`ai_agents`) carry a `model` field; the resolver derives the provider from the model and reads the user's BYOK key.
- **Resolution:** `backend/app/services/ai/providers/ai_provider_helpers.py::resolve_task_provider_config()` gates on governance, then resolves agent → `agent.model` → provider → user BYOK.
- **Governance** (`ai_module.<module>.user_allowed` in `system_settings`): locked → admin platform config; unlocked → user BYOK + agent. **Unchanged by this feature.**

## Key decisions (confirmed)

1. **Classify Nous models purely by model TYPE: `llm | embedding | tts | asr`.** Type is intrinsic to the model; one LLM serves many features. (The column is renamed `category → type` to make this unambiguous.)
   - **No data migration:** `nous_models` is **empty in prod (0 rows, verified 2026-06-19)**, so the migration just **swaps the CHECK constraint** from the old `('transcription','summarization','analysis')` to `('llm','embedding','tts','asr')` (and renames the column). There is nothing to remap.
2. **Per-slot typing (supersedes the earlier "2a list-all").** Each selection slot shows Nous models of the type it needs:
   - Agent model picker (LLM modules) → **llm** Nous models.
   - Transcription dropdown → **asr** Nous models.
3. **v1 consumer wiring:** `llm → agents`, `asr → transcription`. `embedding` and `tts` become **admin-configurable types** but have **no user-facing consumer in v1** (embedding → memory wiring and any TTS feature are deferred; admin can configure them to reserve the slot).
4. **Agent layer preserved.** Users build/edit their own agents (custom prompt) and set an agent's model to a Nous model. No auto-generated platform-preset agents in v1.
5. **Admin annotation.** Add an optional `description` field to `nous_models` (admin writes a short usage note, e.g. "fast, cheap, short clips"). User pickers show `display_name` + type badge + `description`.
6. **Disabled/missing model → fail-closed.** If an agent (or transcription setting) references a Nous model that is disabled or deleted, the resolver raises a clear error ("platform model X is no longer available") rather than silently falling back to a wrong model.

## Architecture

Three focused changes; **no new tables**, reuse the existing `nous_models` + agent + `task_assignment` machinery.

```
admin nous_models (enabled)            ← type: llm | embedding | tts | asr  (+ description)
        │  GET /api/v1/ai/nous-models?type=llm|asr  (enabled only, no keys)
        ▼
① User AI Settings: "Nous" provider card
   - no api_key input (platform-managed)
   - lists enabled Nous models (display_name + type badge + description)
② Selection slots show Nous models of the matching type:
   - agent model picker        → llm  Nous models  → sets agent.model = nous_models.name
   - transcription dropdown     → asr  Nous models  → sets task_assignment.transcription = nous ref
        │  task_assignment unchanged in shape
        ▼
③ Resolver branch (ai_provider_helpers):
   resolved model name matches an ENABLED nous_models.name
     → use that row's actual_provider / actual_model / api_key / base_url   (platform key)
   matches a DISABLED/missing nous name
     → fail-closed (raise clear error)
   else
     → existing BYOK path (unchanged)
```

### Components

**Backend**
- `nous_models` model + migration: rename `category → type`, swap its CHECK constraint to `('llm','embedding','tts','asr')` (no data remap — table is empty in prod), add nullable `description text`.
- `schemas/nous.py`, `schemas/admin.py`: type enum + `description`.
- `api/admin/nous_router.py`: accept/return type enum + `description` (api_key stays masked/write-only).
- `api/ai_settings_router.py`: `GET /api/v1/ai/nous-models` returns `name, display_name, type, description, sort_order`; supports `?type=` filter (replaces `?category=`). Enabled-only, no keys.
- `services/ai/providers/ai_provider_helpers.py`: add a **shared Nous-resolution step** applied to whichever model name the resolver lands on. `resolve_task_provider_config` derives the model name in two places — the governance-**locked** path (`governance.model`, ~line 99/115) and the **unlocked** agent path (`agent.model`, ~line 144). The Nous lookup must run for **both** (admin may lock a module directly to a Nous model name), so factor it into one helper called before `provider_key_for_model(...)` in each branch. Lookup by `name`; fail-closed on disabled/missing.
- Adapter factory: construct an adapter from a nous platform config (provider/model/key/base_url).
- **`schemas/nous.py`** has the `category` enum as `Literal[...]` in **4 places** (Create / Update / 2× Response) — all must change to the new type enum. `nous_repository.py::list_enabled(category)` + `nous_repository_orm.py` select/filter on `category` and must follow the rename to `type`.

> **Verified during design review (2026-06-19):** the `nous_models_category_check` CHECK constraint (mig116) and the resolver insertion points above are confirmed in code. **Open item for planning:** the **transcription** provider/model resolution does **not** live in `ai_provider_helpers.py` — it is a separate path (whisper/ASR). Decision **1b** (ASR Nous models) therefore touches a second, not-yet-located resolution path and is higher-uncertainty than the LLM/agent path; the plan must locate it first and may split ASR into its own task.

**Frontend (`frontend/`)**
- `components/AISettings.tsx`: add a **Nous provider card** (no key field; renders enabled Nous models with type badge + description; read-only informational). Transcription dropdown: append ASR Nous models as options.
- AI Library **agent editor model picker**: add a **"Nous (platform)"** group listing **llm** Nous models; selecting sets `agent.model = nous_models.name`.

**Admin (`admin/`)**
- Nous model config form: `category` → **type** dropdown (`llm|embedding|tts|asr`) + a `description` text field.

### Data flow / resolution detail

- `agent.model` is a free-text string today; a Nous model's `name` is a valid value. **Resolution order:** the resolver looks the resolved model name up in `nous_models` across **all** rows (enabled + disabled), then:
  - **found + enabled** → use the platform config (`actual_provider/actual_model/api_key/base_url`).
  - **found + disabled** → **fail-closed** (the model was a Nous model but admin disabled it; do not fall back to a guessed BYOK provider).
  - **not found** → existing BYOK path, untouched (the name is an ordinary provider model like `gpt-4o`).
  This full-table lookup is what lets the resolver tell a disabled-Nous reference apart from an ordinary BYOK model name.
- Transcription stores the Nous choice as **`nous:<nous_model_name>`** in `task_assignment.transcription` — mirroring the existing `provider:model` shape with `provider = nous`. The resolver strips the `nous:` prefix and runs the same `nous_models` lookup to get the platform ASR config.
- The platform `api_key` is read server-side only and never returned to any client (matches current masking).

### Error handling

- Resolver fail-closed on disabled/missing Nous model: raise a typed error surfaced to the feature as "this platform model is no longer available — pick another," consistent with the existing fail-closed governance behavior.
- Nous endpoint and pickers degrade gracefully (empty Nous group) when no enabled models exist — never block the rest of the settings UI.

### Testing

- **Backend unit:** resolver returns platform config when `agent.model` matches an enabled Nous model; fail-closed when disabled/missing; unchanged BYOK path when the model is not a Nous name. Migration swaps the CHECK constraint to the `type` enum (no data remap — table is empty). `nous-models` endpoint returns `type` + `description` and honors `?type=` filter.
- **Frontend:** Nous provider card renders enabled models with badges/descriptions and no key field; agent picker shows only `llm` Nous models; transcription dropdown shows `asr` Nous models.

## Accepted risks (v1)

- **Platform cost exposure.** With no billing (scope A) and an **unlocked** module, any user can assign an agent that uses a Nous model and run it on the **platform key at platform expense**. The admin's only lever in v1 is enabling/disabling Nous models globally (and locking modules). This is the cost hole that the deferred *Nous Billing* project closes; accepted for v1.

## Explicitly deferred (not v1)

- Points/credit charge + usage metering for Nous usage (the *Nous Models & AI Billing* project).
- `embedding` Nous models wired into the memory embedder (currently admin `system_settings`-driven).
- A `tts` consumer feature.
- Auto-generated platform-preset agents per Nous model.
- Per-team / per-user governance overrides.
