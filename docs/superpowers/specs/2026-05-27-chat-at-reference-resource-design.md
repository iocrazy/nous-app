# Chat `@`-Reference Resource — Design Spec

**Sub-plan 4 of the media-context epic.** Lets users type `@` in the chat composer to attach existing library resources as agent context, mirroring the way `Skill(slug, file)` exposes skills — lazy, on-demand, low token cost.

**Status:** Approved 2026-05-27. Implementation plan to follow via writing-plans.

**Related:**
- Sub-plan 1 (shipped #351): temp resource foundation
- Sub-plan 2 (shipped #352): temp TTL + DBOS sweeper
- Sub-plan 3 (shipped #353): paste / drag chat uploads
- Sub-plan 4 (this doc): `@`-reference existing resources
- Sub-plan 5 (deferred): AI-generated media → resource

---

## Goal

A chat user can type `@` in the AIChatPanel composer, see a floating picker of resources they have access to, select one, and the agent sees a metadata-only reference in its system message plus a `ResourceFetch(id, mode?)` tool it can call on demand to load full content.

## Non-goals (v1)

- IssueReplyBox does NOT get `@`-ref (only ChatInput). Issue context is task execution; agent value lower; ship risk smaller.
- No `@@` user / agent mentions. Only `resource_ref`.
- No Smart Folder / Library targets (ambiguous semantics for "reference a folder").
- No batch multi-select. `@` + Enter multiple times works.
- No chip drag-reorder (insertion order doesn't influence LLM anyway).
- No `mode='frames'` time-stamp customization. v1 picks 5 evenly-spaced keyframes.
- No chunked / streaming RAG for huge files. v1 truncates with a `[truncated]` marker.
- No cross-turn @-ref ("agent recall earlier @"). Each turn's `<available_resources>` is fresh.

## Architecture

### Data flow

```
User types '@' in ChatInput
  │
  ▼
tiptap Mention extension fires → ResourcePickerSuggestion popup
  │
  │   GET /api/v1/resources/search?q=&kinds=&scope=accessible&limit=20
  │   (200ms debounce; results cached for the duration of one '@' session)
  ▼
User picks an entry → tiptap inserts resourceRef node →
  ChatInput.attachments[] appends {
    kind: 'resource_ref',
    resource_id, name, mime, scope: {type, id}
  }
  │
  │   POST /api/v1/ai-library/sessions/{sid}/messages
  │   body.attachments = [...paste/drag..., {resource_ref}...]
  ▼
ai_library_chat_service.send_user_message(attachments=...)
  │
  ├─ chat_attachment_resolver  →  image / pdf_page  (unchanged path)
  └─ resource_ref_resolver     →  permission check → metadata fetch
                                  (no bytes loaded yet)
  │
  ▼
prompt_composer assembles system message:
   <persona> ... </persona>
   <available_skills> ... </available_skills>      ← existing
   <available_resources>                            ← NEW
     <resource id=... kind=... mime=... scope=... name=... size=... .../>
     ...
   </available_resources>
   <available_tools>
     ... existing tools ...
     ResourceFetch(resource_id, mode?, args?)     ← NEW
   </available_tools>
  │
  ▼
AgentRunner loop (unchanged)
  Agent may call ResourceFetch(id, mode=...) zero, one, or many times.
  Each call:
    - re-validates user can read resource_id (defense against scope drift)
    - resolves per kind+mode to content + injects multimodal parts
    - returns text or {error:...}
    - in-memory cached per (run_id, resource_id, mode)
```

### Frontend file map

```
frontend/
├── components/chat/
│   ├── ChatInput.tsx                       MODIFY: textarea → tiptap EditorContent
│   │                                       Keep prop surface stable for AIChatPanel.
│   ├── ChatInputResourceMention.ts         NEW:    tiptap Extension wrapping
│   │                                                @tiptap/extension-mention
│   ├── ResourcePickerSuggestion.tsx        NEW:    Suggestion renderer (Tippy.js
│   │                                                popover; type tabs; row list)
│   └── ResourceChipNode.tsx                NEW:    tiptap NodeView for chip render
├── services/
│   └── resourceSearchService.ts            NEW:    fetch + AbortController per query
├── hooks/
│   └── useResourceSearch.ts                NEW:    150ms debounce + per-prefix cache
├── types.ts                                MODIFY: add ResourceRefAttachment type
└── public/locales/{en,zh}.json             MODIFY: i18n keys (placeholder, empty,
                                                    type tabs, kbd hints)
```

### Backend file map

```
backend/app/
├── api/
│   └── resources_search_router.py          NEW: GET /resources/search (paginated,
│                                                kind/scope filter, used by picker)
├── services/ai/
│   ├── resolvers/
│   │   ├── chat_attachment_resolver.py     UNCHANGED
│   │   └── resource_ref_resolver.py        NEW: kind='resource_ref' handler;
│   │                                            permission + metadata + dedup
│   ├── tools/
│   │   └── resource_fetch_tool.py          NEW: ResourceFetch impl;
│   │                                            kind-routed dispatch
│   ├── chat/
│   │   └── ai_library_chat_service.py      MODIFY: send_user_message —
│   │                                                call resource_ref_resolver,
│   │                                                expose ResourceFetch tool,
│   │                                                pass refs into prompt_composer
│   └── prompt_composer.py                  MODIFY: render <available_resources>
│                                                    block between SKILL and
│                                                    cache-boundary
└── repositories/
    └── resources_repository.py             MODIFY: add list_accessible_for_user(
                                                user_id, q, kinds, scope_filter,
                                                limit) (or via new search method)
```

## Component-by-component

### Frontend: ChatInput (rewrite from textarea to tiptap)

Public prop surface stays identical (`onSend`, `onAttach`, `disabled`, `placeholder`, `onPaste`) so `AIChatPanel.tsx` does not need a parallel rewrite. Internally:

- `EditorContent` from `@tiptap/react`
- Extensions: `StarterKit` (with paragraph + history only), `Placeholder`, `Mention` configured for `@` trigger
- Existing paste / drop hooks (`useComposerPaste`, `useComposerDropzone`, `useChatAttachmentUpload`) re-wired to tiptap's `onPaste` / `onDrop` events; tiptap fires DOM events to its `editorProps.handlePaste/handleDrop` so the existing handlers attach cleanly.
- On `editor.getJSON()`: serialize text + mentions for sending. Mentions become text `@<name>` in the displayed message, with structured `attachments[]` carrying the real `resource_id`.

### Frontend: ResourcePickerSuggestion

A Tippy.js popover (tiptap mention extension's standard render path) anchored to the caret position.

Header strip:
- Type tab pills with live counts: `All N · 🎬 Video N · 🖼️ Image N · 📄 Doc N`
- Tab keyboard switch: `⌥1` / `⌥2` / `⌥3` / `⌥4` (option+number)
- Footer-right: "↑↓ navigate · ↵ insert · Esc cancel"

Result rows (single column, 4–6 visible):
- Type icon (32×32 colored block by kind)
- Line 1: `<name>` · `<scope label>` (`personal` / `team alpha`)
- Line 2: `Updated <relative> · <size>`
- Hover: row bg highlight
- Active (keyboard-focused) row: indigo bg

Empty state:
- "No resources match `xyz`" + sub-line "Type to refine, or upload via paste/drop"

Network:
- `useResourceSearch(query, kindFilter)` → debounced 150ms
- Backed by `GET /api/v1/resources/search?q=&kinds=video,image&limit=20`
- Per-query AbortController; older requests cancelled

### Frontend: ResourceChipNode

Tiptap atom Node configured:
- `name: 'resourceRef'`
- `group: 'inline'`
- `inline: true`
- `atom: true`           // Backspace deletes the whole chip
- `selectable: true`
- `draggable: true`      // tiptap default; we won't bind hooks for ordering
- Attributes: `resourceId`, `name`, `kind`, `mime`, `scope`

`renderHTML`: `<span data-resource-id={id} data-kind={kind} class="resource-chip">{icon} {name}</span>`

NodeView (custom React renderer) so we can:
- Render real icon + truncated name + small `×` hover
- Pad / round consistently with paste-attachment chips (visual cohesion)

Serialization for cross-window paste / message history:
- Markdown link form: `[<name>](resource://<id>?kind=<kind>)`
- On paste of such a link, the extension re-resolves to a chip if the user has access

### Frontend: Attachment send shape

```ts
type AttachmentBase = { kind: 'image' | 'video_thumbnail' | 'pdf_page' | 'audio' | 'resource_ref' };

type ResourceRefAttachment = AttachmentBase & {
  kind: 'resource_ref';
  resource_id: string;        // BIGINT serialized as string (Snowflake)
  name: string;               // snapshot, used by history rendering even if resource later deleted
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
};
```

### Backend: GET /api/v1/resources/search

| Param | Type | Notes |
|---|---|---|
| `q` | string, optional | substring match on `resources.filename` (ILIKE, indexed via trigram or btree prefix; pick whichever exists; fall back to ILIKE without index for v1) |
| `kinds` | csv | `video,image,doc,audio,pdf`. Maps to `mime LIKE` clauses |
| `scope` | string | `accessible` (default) = personal-owned ∪ team-shared. Other values rejected |
| `limit` | int | default 20, max 50 |
| `cursor` | string, optional | opaque pagination cursor (id of last row + updated_at) |

Returns JSON:
```json
{
  "results": [
    {
      "id": "310...",
      "name": "storyboard-v2.md",
      "kind": "doc",
      "mime": "text/markdown",
      "size": 2438,
      "scope": { "type": "personal", "id": "8e15..." },
      "updated_at": "2026-05-24T10:00:00Z",
      "thumbnail_url": null
    }
  ],
  "counts": { "all": 12, "video": 3, "image": 5, "doc": 4, "audio": 0, "pdf": 0 },
  "next_cursor": null
}
```

`counts` populated only when `q` is non-empty (so the tab counts reflect "matches per type"). When `q` is empty, `counts` returns running totals.

Permission: existing FastAPI dependency `get_current_user`. Visibility joined via `resource_items` for personal scope and via `library_members` for team scope (look at existing `ResourcesRepository.list_resources_in_folder` for the pattern; reuse the same scope-join helper).

### Backend: resource_ref_resolver

Inputs: `attachments: list[dict]` filtered to `kind == 'resource_ref'`, `user_id: str`.

For each entry:
1. Fetch `resources` row by `id`.
2. Verify user access via scope (personal owner or team membership).
3. If accessible: produce a `ResourceRef` dict `{id, name, kind, mime, size, scope, updated_at, brief}` for prompt injection. `brief` is the existing `resources.description` if non-empty, else `null`.
4. If inaccessible / deleted: produce a `ResourceRef` with `error="not_accessible"` and `name` from the request snapshot. The composer renders a warning row "Skipped: `<name>` (deleted)" in the conversation; the LLM does NOT see deleted entries in `<available_resources>`.
5. Dedupe by `id` — same resource referenced twice = one entry.

Returns `(refs_for_prompt: list[ResourceRef], warnings_for_user: list[str])`.

### Backend: prompt_composer changes

After the existing `<available_skills>` block, before the cache-fingerprint comment, render:

```xml
<available_resources>
  <resource id="310..." kind="doc" mime="text/markdown"
            scope="personal" size="2.4KB"
            updated="2026-05-24" name="storyboard-v2.md" />
  <resource id="310..." kind="video" mime="video/mp4"
            scope="team:alpha" size="18MB"
            updated="2026-05-20" duration="2:34"
            name="storyboard-pitch.mp4" />
</available_resources>

Use the ResourceFetch tool to load any of these on demand:
  ResourceFetch(resource_id, mode?, args?)
  - mode for video: summary (default) | transcript | frames
  - mode for doc: excerpt (default) | full
  - mode for pdf: excerpt (default) | page (args.page)
  - mode for image: omit (returns image part)
  - mode for audio: transcript (default)
```

If no `@`-refs in this turn: omit the block entirely (no `<available_resources/>` self-closing — keeps system message stable for cache key when no refs are used).

Resources cache-key contribution: hash of `(resource_id, updated_at)` for each ref. Two turns with identical ref sets hit the same prefix cache; turning a ref into a different version invalidates only the resources sub-block.

### Backend: ResourceFetch tool

Signature (registered in `app/services/ai/tools/resource_fetch_tool.py`):

```python
async def resource_fetch(
    *,
    resource_id: str,
    mode: Optional[str] = None,
    args: Optional[dict] = None,
    # Injected by AgentRunner:
    user_id: str,
    available_refs: set[str],  # the ids from <available_resources> this turn
    request_cache: dict,        # in-memory cache, request-scoped
) -> dict:
    """Returns {content: ..., meta: {...}} or {error: '<short reason>'}."""
```

Routing per kind (all branches reuse existing services where possible):

| Kind | mode | Implementation |
|---|---|---|
| `image` | (any) | Fetch signed URL via `MediaTokenService.mint_temp_token(resource_id)` (existing PR #347 mechanism). Return `{content: [{type:'image_url', url, mime}]}`. Vision-aware adapter receives image part. Non-vision model: return `{content: alt_text}` fallback with the resource's `description` or filename. |
| `video` | `summary` (default) | Read `videos.summary` joined via `parsed_media.id = videos.parsed_media_id`. If null: `{error: 'summary not available; resource not yet processed'}` |
| `video` | `transcript` | Read `videos.transcript`; same null handling |
| `video` | `frames` | Use existing `app/services/media/keyframe_extract.py` (if absent, this is the one new helper we write — extract 5 evenly-spaced JPEG keyframes). Return as multipart `image_url` list. |
| `audio` | `transcript` (default) | Read `videos.transcript` (audio also stored in `videos`) |
| `doc` (markdown/txt/json) | `excerpt` (default) | Read file via existing storage adapter; return first 4000 chars |
| `doc` | `full` | Same but full content, soft-cap 16k tokens. If exceeded: truncate + append `[... truncated, X bytes remaining; use mode='excerpt' or specify args.start, args.end]` |
| `pdf` | `excerpt` (default) | Reuse `app/services/media/pdf_extract.py` if exists; else PDFMiner first N pages |
| `pdf` | `page` (args.page) | Render specific page as image OR text per arg flag |

Guard rails:
- `resource_id not in available_refs` → `{error: 'resource not referenced in this turn'}`
- User-id reload-check on every call (defense against scope drift)
- Image kind + non-vision model: degrade to alt_text + log warning (don't error — agent can keep working)
- All errors return `{error: '<short reason>'}`; never raise — agent must see the error and decide

In-memory cache (request scope only; cleared at run end):
- Key: `(resource_id, mode, args_hash)`
- Value: the resolved `{content, meta}` dict
- Cleared when AgentRunner cleans up the run

## Permissions & security

- All scope checks happen server-side. Frontend `scope` field on the attachment is a hint for picker UX only — never trusted on resolve.
- `resources_search_router` filters by `accessible_for_user(user_id)` at the SQL level.
- `resource_ref_resolver` re-checks accessibility per entry; deleted / scope-drifted entries become user-visible warnings, not LLM-visible refs.
- `ResourceFetch` tool re-checks on every call.
- Signed URLs for image fetch use existing `MediaTokenService` mint with short TTL (5 min default).

## Cache & token budget

| Layer | Cost | Mitigation |
|---|---|---|
| `<available_resources>` block | ~40 token / ref | Small refs only; full content stays out unless agent calls fetch |
| `ResourceFetch(id, summary)` | varies (200–800 token typical) | Capped at 4000 char default; `mode='full'` warns on truncation |
| Per-turn cache | trivial mem | dict cleared at run end |
| Prefix cache | helps when ref set repeats across turns | hash includes `(resource_id, updated_at)` |

## Testing strategy

Frontend (vitest):
- `useResourceSearch.test.ts` — debounce / abort / cache hit / empty `q`
- `ResourcePickerSuggestion.test.tsx` — keyboard nav / type tab switch / empty state
- `ChatInput.test.tsx` — mention insert / chip delete via Backspace removes attachment / send payload shape (with mocks for paste/drag, ensuring existing flows untouched)
- `ChatInputResourceMention.test.ts` — chip serialize / deserialize round-trip via markdown-link form

Backend (pytest):
- `test_resources_search_router.py` — q filter / kind filter / scope filter / cursor / permission deny
- `test_resource_ref_resolver.py` — permission check / deduplication / deleted-resource warning emission
- `test_resource_fetch_tool.py` — per-kind dispatch / available_refs gate / user re-check / cache hit / vision degrade
- `test_prompt_composer.py` — `<available_resources>` block renders when refs present / omits when absent / cache-key contribution

Integration:
- Send a chat message with one image ref + one video ref; assert `ResourceFetch` is in tools; assert agent can call it; assert returned content reaches the next turn

E2E (playwright, optional for v1):
- Type `@`, pick a resource, send, assert agent message references it sensibly

## Rollout plan

Single PR. Behind no feature flag (low blast radius; non-mention paths unchanged). Watch for:
- Token-cost regressions in `agent_runs.cost_cents` for chat sessions (should be flat for sessions without `@`)
- Frontend bundle size delta from tiptap mention extension (~20kb expected)
- Error rate on `/resources/search` (new endpoint; should be zero)

Rollback: single git revert; tiptap migration on ChatInput is contained in one commit.

## Open questions resolved during brainstorm

- (1) **Picker scope:** personal + team-shared, no recycle, no temp.
- (2) **Payload form:** reference-based, not bytes.
- (3) **Chip visual:** inline (not separate tray).
- (4) **Editor:** tiptap + mention extension (not contenteditable hand-roll).
- (5) **Picker UI:** floating dropdown + type tabs + caret-anchored.
- (6) **Backend resolution:** metadata + on-demand fetch tool, mirroring Skill pattern.
- (7) **Tool name:** `ResourceFetch`.
- (8) **Permission model:** server-side enforcement at every step; frontend scope is hint only.
- (9) **Caching:** per-turn in-memory; no cross-turn.
- (10) **History snapshot:** message `attachments` jsonb stores name+kind so deleted resources still render in history.
- (11) **Scope:** ChatInput only; IssueReplyBox deferred.

## Spec self-review (2026-05-27)

1. **Placeholder scan:** No "TBD" / "TODO" / "fill in later" remaining. Every section names concrete files + functions.
2. **Internal consistency:**
   - `Mode` table (section "Mode 表") consistent with the `ResourceFetch` Python signature in tools section.
   - `<available_resources>` XML format identical across architecture diagram + prompt_composer section + ResourceFetch routing section.
   - Attachment shape identical in Frontend and Backend (kind='resource_ref').
3. **Scope check:** Single PR feasible — sub-features are tightly coupled (picker + tool + resolver all needed for any working `@`-ref). Not decomposable further without leaving half-shipped state.
4. **Ambiguity scan:**
   - "Per turn" cache lifetime explicit: "cleared when AgentRunner cleans up the run".
   - "Brief" field of metadata explicit: `resources.description if non-empty else null`.
   - `kinds=` parameter values pinned to enum: `video,image,doc,audio,pdf`.
   - Mode defaults explicit per kind.
5. **Issues found + fixed inline:**
   - First draft had "Tool name: `ResourceFetch` or `Resource`" → pinned to `ResourceFetch` for parallelism with future possibilities (`ResourceList`, etc.).
   - First draft had `args.start/end` for doc full mode without specifying; clarified that v1 only supports the documented modes, `args` is reserved for future fields.
   - First draft did not specify what `scope` strings look like in `<available_resources>`; pinned to `personal` and `team:{name}`.
   - First draft was unclear on whether `<available_resources>` renders empty when no refs — clarified: omit entirely.
   - Cache-key contribution made explicit (was hand-waved as "yes there's caching").

No further issues. Proceeding to writing-plans.
