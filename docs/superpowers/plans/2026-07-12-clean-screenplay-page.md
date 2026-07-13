# Clean Screenplay Page (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Script tab read like a clean, real screenplay page — one consistent per-block numbering, no empty input-boxes, no chapter list mixed in, tidy monochrome format.

**Architecture:** All work is in the existing editor (`frontend/editor/`). Mostly CSS in the scoped stylesheet plus small render changes: a document-order block index threaded down, the scene-number badge removed, and `ChapterFallback` moved out of the Script render into the Outline view. No data-model or backend change.

**Tech Stack:** React 19 + TypeScript, Vitest, the scoped `EDITOR_SHELL_STYLES` string.

## Global Constraints

- Version: bump `frontend/package.json` to a UNIQUE version above current `origin/master` (read it first — the canvas team ships fast and versions collide). Verbatim rule: `git show origin/master:frontend/package.json | grep version` then +1.
- Verify every visual change against a headless reproduction of BOTH the EMPTY state (unset headings + seeded empty blocks, NO chapter list) AND a FILLED state, using the editor's real CSS + real DOM. The filled-only preview hid the empty-state bugs before — do not repeat that.
- Merge-then-review: squash-merge to deploy; the user reviews on prod, not a PR preview.
- Pre-existing `components/Inspiration/NoteEditor*.test.tsx` failures (missing `@tiptap/extension-task-list`) are unrelated — every test under `editor/` must pass.

---

### Task 1: Document-order continuous block numbering (A1) + one handle style (A5)

**Files:**
- Modify: `frontend/editor/components/EditorShell.tsx` (compute a per-scene block-index base; pass to each `SceneBlock`)
- Modify: `frontend/editor/components/SceneBlock.tsx` (accept `blockIndexBase`; feed it to the layout engine; stop rendering the scene-number badge as a separate scheme)
- Modify: `frontend/editor/render/HollywoodLayout.tsx` + `AsianLayout.tsx` (index = base + local map index)
- Modify: `frontend/editor/components/editorShellStyles.ts` (scene + element handle identical; badge retired)
- Test: `frontend/editor/__tests__/blockNumbering.test.tsx` (new)

**Interfaces:**
- Produces: `blockIndexBase: number` prop on `SceneBlock` and `HollywoodLayout`/`AsianLayout`; the number rendered in `.mh-el-num` = `blockIndexBase + localIndex + 1` (scene heading consumes index 0 of its scene, elements follow).

- [ ] **Step 1: Write the failing test** — a helper `sceneBlockBases(scenes)` returns the cumulative block offset per scene (each scene contributes `1 + elements.length` — heading + its blocks).

```tsx
import { sceneBlockBases } from '../components/blockNumbering';
test('cumulative block base per scene (heading counts as one block)', () => {
  const scenes = [
    { id: 's1', elements: [{}, {}] },      // heading + 2 = 3
    { id: 's2', elements: [{}] },          // heading + 1 = 2
    { id: 's3', elements: [] },            // heading + 0 = 1
  ] as any;
  expect(sceneBlockBases(scenes)).toEqual([0, 3, 5]);
});
```

- [ ] **Step 2: Run it, expect FAIL** — `npx vitest run editor/__tests__/blockNumbering.test.tsx` → module not found.

- [ ] **Step 3: Implement** `frontend/editor/components/blockNumbering.ts`:

```ts
import type { SceneDoc } from '../types';
/** Cumulative block offset for each scene: heading counts as one block, then
 *  its elements. Scene i starts at sum over j<i of (1 + scenes[j].elements.length). */
export function sceneBlockBases(scenes: SceneDoc[]): number[] {
  const bases: number[] = [];
  let acc = 0;
  for (const s of scenes) {
    bases.push(acc);
    acc += 1 + s.elements.length;
  }
  return bases;
}
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Wire it.** In `EditorShell.tsx` where scenes map to `<SceneBlock>`, compute `const bases = sceneBlockBases(scenes)` and pass `blockIndexBase={bases[i]}`. In `SceneBlock.tsx` add the prop (default 0), pass `blockIndexBase` to the layout engine, and give the scene heading number `blockIndexBase` (its own block). In `HollywoodLayout`/`AsianLayout`, `elements.map((el, i) => index={blockIndexBase + 1 + i})` (heading took `blockIndexBase`). Remove the standalone scene-number badge render OR restyle it to be the heading's block number in the same margin column/handle as elements (A5). In `editorShellStyles.ts` ensure `.mh-scene-num-badge` and `.mh-el-num` share size/colour/position, and the scene-row handle matches `.mh-el-drag`.

- [ ] **Step 6: Verify headless** — regenerate the empty-state + filled-state reproductions (real CSS + DOM). Confirm numbers increase continuously across scenes (scene1 heading=1, its blocks 2,3; scene2 heading=4…) and only appear on hover. Paste both screenshots.

- [ ] **Step 7: Run `npx vitest run editor`; commit.**

---

### Task 2: Kill the empty focus box (A2)

**Files:**
- Modify: `frontend/editor/components/editorShellStyles.ts`
- Test: (visual — headless reproduction)

- [ ] **Step 1: Root-cause.** Grep every rule that can put a border/shadow/background on `.mh-el-editable` or `.mh-el-row` when focused/empty: `grep -n "box-shadow\|border\|outline\|background" editor/components/editorShellStyles.ts | grep -iE "el-editable|el-row|focused|hw-|:empty"`. Identify why the earlier override (`.mh-el-row.focused .mh-el-editable{ box-shadow:none }`) did not win on prod (deploy lag vs a higher-specificity rule such as `.mh-el-row.focused .mh-el-editable` being beaten by a later base rule, or the box coming from `.hw-action` width + a different element). Write the finding as a comment.

- [ ] **Step 2: Fix at the source.** Ensure a focused EMPTY block shows only the caret: no `box-shadow`, no `border-radius` box, no tint, and the `width:60ch` empty block paints nothing. If the ring is genuinely wanted for non-empty focused lines, scope it to `:not(:empty)`; otherwise remove it. Add the rule with sufficient specificity to win.

- [ ] **Step 3: Verify headless** — reproduction with a focused empty block shows NO box (only where the caret would be). Paste screenshot.

- [ ] **Step 4: Commit.**

---

### Task 3: Chapter list out of the Script tab → Outline (A3)

**Files:**
- Modify: `frontend/editor/components/EditorShell.tsx` (remove `ChapterFallback` from the Script-mode render)
- Modify: `frontend/editor/components/OutlineView.tsx` (render the unstarted-chapter list + Start Writing entry there)
- Test: `frontend/editor/__tests__/emptyStates.test.tsx` (update: chapter cards NOT in Script view; present in Outline)

- [ ] **Step 1: Write the failing test** — in the Script tab, an orphan chapter renders NO `chapter-fallback`; switching to Outline shows it.

```tsx
it('orphan chapter is NOT in the Script tab (moved to Outline)', async () => {
  svc.listScenes.mockResolvedValue([]);
  scriptSvc.fetchScriptProject.mockResolvedValueOnce({
    chapters: [{ id: 'ch1', title: 'Chapter One', content: '' }],
  });
  render(<EditorShell scriptId="1" />);
  await waitFor(() => expect(screen.queryByTestId('chapter-fallback')).toBeNull());
});
```

- [ ] **Step 2: Run it, expect FAIL** (chapter-fallback still in Script).

- [ ] **Step 3: Implement.** In `EditorShell.tsx` Script-mode render, delete the `orphanChapters.map(<ChapterFallback>)` block. In `OutlineView.tsx`, add a section listing `orphanChapters` with the same `onStartWriting` entry (plain "Start Writing" affordance). Thread `orphanChapters` + `onStartWriting` into `OutlineView` props.

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Verify headless** — Script reproduction has no chapter list; Outline shows it. Paste screenshots.

- [ ] **Step 6: Run `npx vitest run editor`; commit.**

---

### Task 4: Format cleanup consolidation (A4)

**Files:**
- Modify: `frontend/editor/components/editorShellStyles.ts`
- Test: (visual — headless reproduction)

- [ ] **Step 1:** Confirm the already-shipped cleanups survive Tasks 1-3 and finish the remainder: unset-heading placeholder = faint non-italic Courier; "按 Tab" hint = quiet whisper; slug/action flush to one left margin; monochrome cues/transitions/parens; calmer scene rhythm. Remove any rule made dead by Task 1 (retired badge) or Task 3 (chapter card overrides no longer needed in Script).

- [ ] **Step 2: Verify headless** — empty + filled reproductions read as a clean screenplay page. Paste both.

- [ ] **Step 3: Commit.**

---

### Task 5: Ship

- [ ] **Step 1:** `git show origin/master:frontend/package.json | grep version`; bump `frontend/package.json` to the next unique version.
- [ ] **Step 2:** `npx vitest run editor` — all `editor/` tests pass.
- [ ] **Step 3:** Squash-merge to master (merge-then-review). Poll `mediahub.heygo.cn/version.json` until the new version deploys; tell the user to hard-refresh and review on prod.

---

## Self-Review

- **Spec coverage:** A1→Task 1, A2→Task 2, A3→Task 3, A4→Task 4, A5→Task 1 (handle style). Ship + verify → Task 5 + per-task headless. Pagination correctly deferred (Phase B).
- **Placeholders:** none — file paths, the numbering helper code, and test code are concrete. The CSS fixes name their targets; the box fix requires a root-cause step (not a blind override) by design.
- **Type consistency:** `sceneBlockBases` / `blockIndexBase` used consistently across EditorShell → SceneBlock → layout engines.
