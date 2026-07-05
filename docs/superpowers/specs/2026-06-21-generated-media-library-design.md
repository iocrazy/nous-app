# Generated Media → Library (media-context sub-plan 5) — Design

Date: 2026-06-21
Status: approved (brainstorming) → next = writing-plans
Scope owner: media-context epic, sub-plan 5

> Note: `docs/superpowers/` is gitignored in mediahub — this spec is a local
> working doc, not committed to git (per repo convention).

## 1. Problem & Intent

AI-generated media (images / videos) produced inside mediahub is not captured
anywhere reusable. The 25-day-old sub-plan-5 sketch ("when an agent tool returns
a file, register it as a resource") **does not hold today**: the agent runtime
has NO media-producing tool (only `Skill`/`ResourceFetch`/`Delegate`/
`FinishIssue`/MCP — all text/consume-only). Media is generated off the agent
loop:
- canvas ClassicMode `image_gen` / `video_gen` → `StoryboardAIService.generate_image/generate_video` → provider URL (URL-centric, NOT persisted).
- storyboard generation → self-persists to `storyboard_assets`.

User decisions (brainstorming 2026-06-21):
- **Both** producers: (A) wire existing canvas generation in, (B) add NEW agent
  chat tools that generate media.
- Modalities: image + video; **audio/TTS deferred** (no TTS backend exists).
- A central **"Generations" library** holding all generated media, each item
  **provenance-traceable** (which run/canvas/prompt/model produced it).
- **Two-tier storage to protect the core `resources` table** (user's scaling
  concern, design-for-100k): generations are HIGH-CHURN (batch + retries +
  experiments). Dumping every one into the shared, hot `resources` table bloats
  rows/indexes and drags every resources-dependent query. So generations live in
  a **dedicated `generated_media` table** (cheap, append-heavy, isolated); only
  generations the user **keeps / actually uses** are promoted into `resources`.
- Project Assets navigation-UI redesign is a **separate round**.

### Key grounded facts (verified in code, drove the two-tier model)
- `resource_items.scope_id → teams.id`, `folders.scope_id → teams.id` — resources
  are **team-scoped + foldered; there is NO project_id on resources/folders**.
- **Project Assets is a canvas projection**: `project → canvases →
  canvas_resource_refs → resources` (`canvas_resource_refs` = `(canvas_id,
  resource_id, node_id)`; project derived via canvas). "Chat Uploads" is a
  frontend synthetic root. ⇒ There is **no "plain folder under a project"
  surface**; a resource shows in Project Assets only via a `canvas_resource_ref`.
- ⇒ The earlier "land in the project's Generations folder + ensure default
  personal project" plan was built on a non-existent project-folder surface and
  is **dropped**. Surfacing is scope-level (the Generations library), plus
  canvas surfacing on promotion for canvas-origin items.

## 2. Architecture — two tiers + one registrar

```
canvas image_gen/video_gen (A) ─┐                              ┌─ Generations library view (reads generated_media)
                                ├─ register_generated_media(K) ─┤
new agent image/video tools (B) ┘   → row in generated_media   └─ promote_to_resource(P) → resources (+ canvas_resource_ref)
                                       (cheap, provenance-rich)      only on Keep / downstream-use
```

- **Tier 1 — `generated_media`** (new table): every generation lands here. Cheap,
  isolated from core resources. Provenance is first-class columns.
- **Tier 2 — `resources`** (existing): only **promoted** generations enter here,
  becoming first-class library assets (taggable, searchable, canvas-referencable).
- **K** = `register_generated_media(...)` — generic sink both producers call.
- **P** = `promote_generation_to_resource(...)` — Tier1 → Tier2.

| Piece | What | Producer status |
|---|---|---|
| **K** | sink: media (URL/bytes) → `generated_media` row + stored file | — |
| **table** | `generated_media` (+ migration) | new |
| **A** | wire canvas ClassicMode `image_gen`/`video_gen` → K | exists |
| **B** | new `GenerateImage`/`GenerateVideo` agent tools → K | new |
| **P** | promote on Keep / downstream-use → resources | new |
| (deferred) B-audio | TTS tool | needs TTS backend |
| (deferred) A-storyboard | storyboard → K | self-persists; double-write risk |

## 3. `generated_media` table (Tier 1) — new, needs migration

`NNN_generated_media.sql` + ORM model in `app/models/` (media or a new domain).

Columns:
```
id                  BIGINT PK (snowflake)
scope_id            BIGINT NOT NULL        -- teams.id (the owning scope, like resource_items.scope_id)
creator_id          UUID   NOT NULL        -- the user
media_kind          TEXT   NOT NULL        -- 'image' | 'video'
mime                TEXT
file_path           TEXT   NOT NULL        -- under DOWNLOAD_PATH/teams/{scope_id}/generations/{id}/...
file_size_bytes     BIGINT
-- provenance (first-class, queryable)
origin_kind         TEXT   NOT NULL        -- 'agent_run' | 'canvas_run'
origin_run_id       TEXT                   -- agent_runs.id (bigint-as-text) or canvas run id
agent_id            UUID                   -- when agent_run
canvas_id           BIGINT                 -- when canvas_run
node_id             TEXT                   -- canvas node, when canvas_run
prompt              TEXT
model               TEXT
provider            TEXT
params              JSONB                  -- {size, quality, seed, n, ...}
cost_cents          NUMERIC
parent_resource_id  BIGINT                 -- lineage: input resource it derived from
derivation_kind     TEXT                   -- 'image_gen' | 'video_gen' | ...
-- lifecycle
promoted_resource_id BIGINT                -- set once promoted to resources (NULL = Tier-1 only); guards double-promote
created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
```
Indexes: `(scope_id, created_at DESC)` for the library list; `(origin_run_id)`,
`(agent_id)`, `(canvas_id)` for provenance queries; `(promoted_resource_id)`.

**RLS:** backend writes via service-role/direct engine (bypasses RLS). Frontend
reads via a **backend endpoint** (not direct PostgREST), so a service-role-only
RLS policy (mirror `worker_registry` / log-table hardening #541/#542) keeps it
off the anon/authenticated PostgREST surface.

**Cleanup (optional, configurable):** un-promoted rows older than a TTL can be
swept (mirror the temp/chat sweeper) so experiments don't accumulate forever.
Default = keep (no auto-sweep) v1; sweeper is a follow-up knob.

## 4. K — `register_generated_media` (the sink)

`app/services/library/generated_media_service.py`

```
async def register_generated_media(
    *,
    user_id: str,            # UUID (creator)
    scope_id: int,           # teams.id (resolved from the run/session)
    media: GeneratedMediaInput,   # source_url OR bytes + mime + media_kind
    origin: GenerationOrigin,     # kind/run_id/agent_id?/canvas_id?/node_id?/prompt/model/provider/params/cost?/parent_resource_id?/derivation_kind?
) -> dict                    # the generated_media row
```
Steps:
1. **Materialize bytes** — producers mostly return a provider URL; K downloads
   with the existing capped-stream + `.part` atomic-write pattern (mirror
   soda/UGC: `cap_aiter` byte ceiling, write `.part`, `os.replace` on success,
   unlink on failure). Direct-bytes path supported.
2. **Store file** under `DOWNLOAD_PATH/teams/{scope_id}/generations/{id}/...`.
3. **Insert one `generated_media` row** (provenance columns from `origin`). No
   `resources` write. No project, no folder.
4. Return the row (id + file URL) so the producer/agent can reference it.

Scope resolution (`scope_id`) is the caller's job: canvas run → the canvas's
scope; agent run → the session's scope (the user's personal team). No
default-personal-project logic (dropped).

## 5. Generations library view (Tier-1 surfacing)

- Backend: `GET /api/v1/generated-media?scope=...&kind=...&cursor=...` —
  keyset-paginated list from `generated_media` for the scope (server-side
  filter/paginate per [[feedback_keyset_search_must_be_serverside]]). Plus
  `GET /generated-media/{id}` (detail w/ provenance), `DELETE` (discard),
  `POST /generated-media/{id}/file` serve.
- Frontend: a **"Generations" synthetic root** in the resources sidebar (mirror
  the existing "Chat Uploads" synthetic-root pattern), listing `generated_media`
  for the scope, newest-first, with a provenance detail panel (origin, prompt,
  model, cost, lineage) and a **Keep / Save to Library** action (= promote).

## 6. P — promote to `resources` (Tier-2)

`promote_generation_to_resource(generated_media_id) -> resource_dict`
- Triggers: (a) user clicks **Keep / Save to Library**; (b) **auto** when the
  generation is used downstream (dragged into a canvas / set as a reference).
- Idempotent via `generated_media.promoted_resource_id` (already set → return
  existing resource).
- Creates the `resources` three-row set (resources `source_type='generated'` +
  resource_versions + resource_items in the scope + optional folder), pointing at
  (or copying) the Tier-1 file; sets `promoted_resource_id`.
- If origin was a canvas → also write `canvas_resource_ref(canvas_id,
  resource_id, node_id)` so it appears under that canvas in Project Assets.
- Carry provenance forward into `resources.metadata` for the promoted asset.

⇒ The core `resources` table only ever receives kept/used generations — high-churn
experiments stay in cheap Tier-1.

## 7. A — wire existing canvas producers

- **Canvas ClassicMode `image_gen` / `video_gen`**: after the op resolves the
  provider URL, call `register_generated_media(origin=canvas_run{canvas_id,
  node_id, run_id, prompt, model, params}, scope_id=<canvas scope>)`. Result also
  flows back to the node as today (URL unchanged for the live run); the
  generated_media row is the durable capture.
- **Storyboard generation = follow-up** (self-persists to `storyboard_assets`;
  `storyboard_projects.id ≠` canvas projects; double-write risk). Not this version.

## 8. B — new agent generation tools

- New tools **`GenerateImage`** / **`GenerateVideo`** in `AgentRunner`
  SUPPORTED_TOOLS.
- **Thread run context into tool execution** (today `RunRecorder` is a closure,
  not exposed to tools — `agent_runner.py` dispatch ~985-1050): inject run_id /
  user_id / scope_id (session's team) / agent_id so the tool builds
  `GenerationOrigin`.
- Tool body: `StoryboardAIService.generate_image/generate_video` → URL →
  `register_generated_media(origin=agent_run{...})` → return to the agent:
  - **image** → `{ ok, generated_media_id, image_url }` — `image_url` lets the
    model SEE its own output next turn (vision); the agent can later promote it.
  - **video** → `{ ok, generated_media_id, metadata }` (no vision for video).
- Provider not registered → clean `{ ok:false, error }` to the agent (no crash).

## 9. Error handling

- K: download/store failure → clean error; `.part` atomic write leaves no
  truncated file; never silently swallowed.
- B tool failure → clean tool error to the agent.
- A canvas failure → best-effort capture; never crashes the canvas run (log).
- P failure → does not lose the Tier-1 row (it stays in generated_media).
- Generation is synchronous within the agent turn / canvas run (mediahub's
  settled sync model; async/DBOS = Phase 6). No `task_tracking` rows; agent-side
  telemetry via `RunRecorder`.

## 10. Testing

- **`generated_media` migration**: applies; RLS service-role-only; indexes exist.
- **K unit**: URL download (capped + atomic); file path layout; row insert with
  full provenance; scope passthrough; bytes path.
- **Generations list**: keyset pagination, scope isolation, RLS off the anon
  surface.
- **P (promote)**: idempotent (promoted_resource_id guard); creates resources
  three-row set; canvas origin also writes canvas_resource_ref; auto-promote on
  downstream use; manual Keep.
- **A integration**: canvas op → generated_media row (origin=canvas_run);
  failure doesn't crash the run.
- **B**: tool dispatch → K → return shape (image returns image_url for vision;
  video returns id); provider-unavailable → clean ok:false; run context threaded.

## 11. Deferred / out of scope (explicit)

- Audio / TTS generation tool (needs a TTS backend).
- Storyboard → library wiring (double-registration risk).
- Project Assets navigation-UI redesign (separate round).
- AI auto-tag of generated media (run on promote; interface only this version).
- Dedup of identical generations by content hash (can layer on generated_media).
- Auto-sweep of old un-promoted generations (config knob, follow-up).

## 12. Open implementation questions (resolve in writing-plans)

- **Promote file handling**: copy the Tier-1 file into the resources path, or
  reference it in place? (copy = clean separation + lets Tier-1 sweep; reference
  = no duplication but couples lifecycles). Lean copy.
- **scope_id resolution for agent runs**: confirm the session→team scope value
  available in the runner (RunRecorder has team_id; map to resource scope_id).
- **Run-context plumbing point** in `agent_runner.py` tool dispatch (minimal
  injection that doesn't disturb existing tools).
- **canvas scope_id**: confirm how a canvas maps to a teams.id scope for K.

## 13. Borrowed from Infinite-Canvas (study 2026-06-21)

- **Two-tier (history/library) separation** — IC keeps generations in a
  `history.json` ring buffer and only explicit "Add to Library" promotes to the
  persistent asset store. We adopt the same separation at the DB level
  (`generated_media` tier vs `resources` tier) — and it's what protects the core
  table from generation churn. ✅ central to this design.
- **Rich generation record** (prompt/model/provider/params/cost/refs) → first-class
  `generated_media` columns. ✅
- **Lineage** (parent + derivation_kind) — IC's own review flagged the gap; we
  include it as columns. ✅
- **Classification as optional post-processing** → auto-tag on promote, interface
  only. ✅ (deferred build)
- **Task-based async + polling** — IC is async; mediahub generation is sync by
  design (Phase 6 defers async). ❌ skip.
