# Script Block Editor — Notion-style blocks for the screenplay editor

**Date:** 2026-07-12
**Status:** Phase 0 shipped (v0.25.248, #1261); Phase 1+ behind `VITE_FEATURE_SCRIPT_BLOCKS`
**Owner:** editor

## Problem

Users perceive the screenplay editor as "one scene = one block" and cannot input
paragraph content. They want Notion / laper.ai-style block editing.

Investigation (see conversation 2026-07-12) found the editor is **already**
block-structured (scene → typed element rows, each an editable block), but:

1. **An empty scene was a dead-end** — zero elements meant zero editable rows;
   the "按 Tab 开始动作行" line was a static non-focusable `<div>`, and Tab is a
   machine key that only fires from a focused element line. Users literally could
   not start typing, and the fixed Hollywood/Asian format had no rows to render
   on. **(Fixed in Phase 0.)**
2. Block creation, type-switching, and reordering are keyboard-only and
   undiscoverable (Tab cycles type; no slash menu; no drag handle on elements).
3. Legacy chapter prose (`ChapterFallback`) is read-only until converted, so
   older content shows as cards you can't type into.

## Decision (scope)

**Screenplay-element blocks, not general-document blocks.** Each block is one of
the 7 existing screenplay element types (`action | dialogue | character | paren |
transition | comment | subtitle`) plus the scene heading. We layer Notion-style
block *interaction* on top; we do NOT add arbitrary block types (headings/lists/
images/tables) and we KEEP the fixed Hollywood/Asian column formatting. This is
the laper.ai model. (User decision, 2026-07-12.)

**Data model is unchanged.** Scenes hold `ScriptElement[]`; persistence stays the
anchored op layer (`insert`/`update`/`move`/`delete` with `before_id`/`after_id`),
which is already a good block foundation. No migration.

## Architecture

All work is additive to `frontend/editor/`. Key existing pieces reused:

- `editorMachine.ts` — pure keyboard state machine (Enter=new block, Tab=cycle
  type, Backspace=delete empty). Already has empty-scene seed logic.
- `layoutShared.ts::ElementLine` — the contentEditable block row.
- `SceneBlock.tsx` — renders a scene's blocks; owns keydown dispatch + optimistic
  ops via `applyResult`.
- `sceneService.ts` — op persistence (`applyOps`, `move`).

## Phases

### Phase 0 — Empty scene is typeable (SHIPPED, unflagged, v0.25.248)
`EmptySceneHint` is now a real seed affordance (`role=button`, `tabIndex=0`):
click / Enter / Tab / Space runs the machine's empty-scene seed (`onEnter` →
insert first `action`) and drops the caret in. Bug fix, no flag.

### Phase 1 — Slash menu (`/`)
Typing `/` at the **start of an empty block** opens an inline menu to pick the
block type: Scene, Action, Character, Dialogue, Parenthetical, Transition,
Comment, Subtitle. Keyboard nav (↑/↓/Enter/Esc) + type-to-filter. Selecting sets
the block's type via an `update` op (or, for "Scene", splits into a new scene —
deferred to Phase 3 if complex). Tab-cycle stays as a power shortcut.

- New component `SlashMenu.tsx` (mirror `MentionCombobox` structure).
- Hook into `SceneBlock.handleKeyDown` / `handleInput`: detect leading `/`,
  open menu, filter on the query, apply on select. Reuse the mention-picker
  positioning + ARIA combobox pattern.
- Reuse `ElementToolbar`'s `SCRIPT_ITEMS` labels/icons as the menu source.

### Phase 2 — Block reordering + block handles
Per-block drag handle (⋮⋮) on hover to reorder blocks within a scene (and across
scenes if cheap), using the existing `move` op. Block quick-actions (delete,
duplicate, change type). Mirror the existing scene-level drag wiring in
`SceneBlock.tsx`.

### Phase 3 — Legacy chapter prose
Make `ChapterFallback` either inline-editable or auto-convert on open, so a
user's existing chapter prose shows as editable blocks instead of read-only
"该章节暂无文本 / 转换为场景" cards. Ensure `convertToScenes` produces scenes
that already contain seeded blocks (never an empty dead-end).

### Phase 4 — Polish
Per-block placeholder hints ("Action", "Character name", …) on the empty focused
block; smooth focus/caret transitions; empty-block affordances; mobile.

## Feature flag

`VITE_FEATURE_SCRIPT_BLOCKS` (frontend), default false. Phases 1–4 render behind
it; Phase 0 is unflagged (bug fix). Flag removed within ~2 weeks of go-live per
project convention. Go-live gated on real-machine + Vercel-preview UX pass.

## Testing

- Unit: machine transitions (already covered), slash-menu filter/apply, seed
  affordance (Phase 0, done).
- Component: SceneBlock renders slash menu on `/`, applies type on select,
  drag-reorder dispatches `move`.
- Manual/preview: full block flow (seed → type → Enter → slash → reorder) in
  both Hollywood and Asian, EN + ZH.

## Out of scope

- Arbitrary rich-text / general-document blocks (headings/lists/images/tables).
- Data-model or persistence changes.
- Rich inline formatting beyond the existing `@mention` chips.
