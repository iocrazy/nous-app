# Script Editor → TipTap/ProseMirror Migration (epic)

**Date:** 2026-07-13
**Status:** approved to start (user: "立", 2026-07-13); spec authored from a full
inventory of the current editor (58 source files, 45 test files — see §Parity)
**Flag:** `VITE_FEATURE_SCRIPT_TIPTAP` (default false; per-script dev override)
**Prior art in-repo:** TipTap 3.27.3 already powers Inspiration notes, ChatInput,
IssueReplyBox — the dependency and team familiarity exist.

## Why

The screenplay editor is a hand-rolled contentEditable stack (per-element rows +
keyboard machine + caret-safety hacks). It now works, but this session fixed a
long tail of bug CLASSES that ProseMirror solves structurally: focus rings on
contenteditable, gutter/text alignment, drag ergonomics, toolbar-follow lag,
"only writer of the editable" caret rule. Each was fixable; the architecture
keeps generating the next one. TipTap NodeViews + extensions are the
industry-standard basis for exactly this block UX (BlockNote / Novel / laper-
class editors).

## Non-negotiable constraint: the data plane does not change

The backend contract stays byte-identical. TipTap replaces ONLY the editing
surface; everything below the dashed line survives unchanged:

- Op protocol: `ElementOp` insert/update/delete/move with `before_id`/`after_id`
  anchors, `If-Match: content_version`, 409 conflict / 422 reject semantics.
- `useSceneSync` (FIFO serial queue, optimistic dispatch, 409-identical replay,
  conflict freeze, backoff, offline, flush, reconcile).
- Realtime collab (`useScriptOpsRealtime` / presence / remote-apply router /
  `VITE_FEATURE_COLLAB` flag-dark) and `opBuilder.applyLocal`/`buildInverse`.
- Version history (commits / diff / rollback), copilot service (ops-based),
  import, convert-to-scenes, episodes, scene CRUD/move.

Yjs/CRDT was considered and REJECTED: it rewrites the server and the versions
model for zero user-visible gain at current collaboration scale.

## Architecture decisions

**D1 — One TipTap instance PER SCENE.** SceneBlock keeps its shell (heading row,
scene gutter, copilot card, presence badge, chapter fallback) and swaps only the
elements area (LayoutEngine/ElementLine/editorMachine) for a `TipTapSceneEditor`.
This preserves: per-scene `useSceneSync` + `content_version`, windowing (mount/
unmount per scene, placeholders), scene-level reorder, and the shell-coordinated
cross-scene drag. A whole-script single doc would break the per-scene version
protocol — rejected.

**D2 — PM schema.** `doc = scriptElement+`; node `scriptElement` attrs
`{ id, elType, characterId? }`, content `text*` (plain text; the mention chip
becomes a TipTap Mention inline node that serializes back to literal `@Name`
text so `ScriptElement.text` round-trips byte-identical).

**D3 — Transaction → ops mapping (the core).** On each editor transaction,
diff the previous vs next ordered element list (ids from node attrs; text from
node text; splits/joins mint ids via `newElementId()`), emit the MINIMAL op
batch (insert/update/delete/move with sibling anchors), and `dispatchOps(ops,
applyLocal(prev, ops))`. Structural ops dispatch immediately; text-only diffs
keep the 500 ms debounce. Remote ops apply as a PM transaction tagged with a
`remote` meta so the mapper never re-emits them (loop guard). GOLDEN TESTS pin
the mapper: for every editing scenario, `applyLocal(prevElements, mappedOps)`
must equal the elements read back from the PM doc.

**D4 — Keyboard semantics are ported, not reinvented.** The type-derivation
tables (Tab CYCLE ring, `TAB_TYPE`/`SHIFT_TAB_TYPE`/`ENTER_NEW_TYPE`, paren
step-into-dialogue, Backspace-at-start delete, heading field ring) move to a
shared module consumed by a TipTap keymap extension. `editorMachine.test.ts`'s
transition table becomes the parity oracle.

**D5 — NodeView renders the EXISTING row DOM.** The `scriptElement` NodeView
emits the same structure/classes as today's `ElementLine` (`.mh-el-row` >
in-flow `.mh-el-gutter` [num + 4-dot handle] + `.mh-el-tick` + content div with
`hw-*`/`as-*` class + `data-el-type`), so `editorShellStyles.ts` — the Hollywood
60ch grid, Asian ornaments (△ prefix / ： suffix via NodeView, not CSS hacks),
warm paper, whispers, chips — carries over ~unchanged. Paged seams become PM
**widget decorations** between nodes (computed by the same shell measurement +
`computePageLayout`), which is strictly cleaner than today's JSX splicing.

**D6 — Feature ports as extensions.** Slash menu → TipTap Suggestion plugin
(`/` trigger). Mentions + character-cue picker → Mention extension with the
existing chip CSS + candidates merge. Same-scene drag → handle-initiated node
move command; cross-scene drag → unchanged shell broadcast/registry, target
inserts + source deletes THROUGH the mapper. Toolbar follow → selection's node
attrs (elType / heading sentinel) — no more lift-lag plumbing. Copilot →
unchanged (ops in, ops out); tick selection lives in the NodeView gutter.

**D7 — Rollout.** Flag-dark parallel implementation. `SceneBlock` branches the
elements-area render on `VITE_FEATURE_SCRIPT_TIPTAP`. Legacy stays the default
until the parity matrix (below) is signed off on prod dogfood; legacy removal
lands within ~2 weeks of flag-on per project convention.

## Parity checklist (gate for cutover)

The full inventory lives in the epic memory + this spec's source conversation;
cutover requires every line green in BOTH formats (Hollywood + Asian):

1. Data/sync: ops mapping golden tests; 409-identical replay; conflict
   keep-mine/take-theirs; offline/backoff/flush; remote apply + self-echo drop;
   divergence beacon; version compare/rollback; copilot polish + proposal +
   undo (buildInverse).
2. Keyboard: full `editorMachine.test.ts` table via keymap; IME composition
   (Chinese input!); 500 ms text debounce; caret never stolen by re-renders
   (PM owns this natively — the whole "only writer" hack dies).
3. Block UX: continuous numbering; hover gutter + 4-dot handle; same-scene +
   cross-scene drag (incl. heading-row drop on empty scenes); slash menu;
   empty-block whisper; EmptySceneHint seed; toolbar type-follow incl. 'scene'
   sentinel; TypeCommand retype; mentions + cue picker.
4. Layout: Hollywood 60ch grid; Asian △/：; heading chips; paged seams with
   keep-rules; windowing (>30 scenes); embedded/standalone modes; theme sync.
5. Shell integrations: scenes lift to workspace sidebar; Outline/Beats/Nodes/
   Storyboard rail views (untouched — they don't render ElementLine); Writing
   panel stats from live elements; SaveIndicator aggregation; import; chapter
   convert / start-writing; episodes.

## Phases

- **M0 — Spike (de-risk the mapper):** `TipTapSceneEditor` skeleton — schema,
  NodeView with today's row DOM, doc↔elements round-trip, and the transaction→
  ops mapper with golden tests (`applyLocal(prev, ops) === docElements(next)`
  across insert/split/join/retype/move/delete/paste scenarios). READ-ONLY
  render parity in both formats. Success criterion: mapper survives an
  adversarial edit-fuzz (random keystroke script) with zero divergence.
- **M1 — Editing core:** keymaps from shared tables; text debounce → sync;
  IME; remote-ops apply with meta guard; conflict rebuild; SaveState pass-
  through. Legacy tests' scenarios mirrored against the TipTap instance.
- **M2 — Block UX:** gutter NodeView, drags (same/cross-scene), slash,
  whisper/seed, toolbar follow, TypeCommand, mentions/cue, copilot.
- **M3 — Layout:** paged seam decorations, windowing interplay, format matrix
  (every type × both engines), Asian ornaments.
- **M4 — Cutover:** flag-on dogfood on prod (user), parity sign-off, perf pass
  (100-scene script), legacy removal PR (~-6k lines).

## Out of scope

- Backend/protocol changes of any kind (incl. Yjs).
- MORE/CONT'D intra-dialogue page splitting (unchanged from Paged v2 scope).
- Migrating Outline/Beats/Nodes/Storyboard views (they don't use ElementLine).
- General-document block types (still screenplay-element blocks only).
