# Clean Screenplay Page — Phase A (format + cleanup)

**Date:** 2026-07-12
**Status:** design approved (brainstorming), pre-plan
**Owner:** editor
**Follow-on:** Phase B = A4 pagination engine (separate spec/plan)

## Problem

The script editor doesn't read like a real screenplay page (ref: a standard
Hollywood page + laper.ai). The user's real state — empty scenes, unset
headings, seeded empty blocks, a legacy chapter list — looks messy: empty
input-boxes, two conflicting numbering schemes, chapter titles mixed into the
script, cluttered format. Earlier fixes were piecemeal CSS patches that didn't
converge.

Key finding: **laper.ai has an A4 pagination engine (its "Paged" mode with page
breaks + page numbers); nous does not** (WritingPanel comment: "page-count …
depends on the pagination engine, cut for Phase 1"). So "look like laper's A4
page" is two layers: (1) a clean screenplay format, (2) a real pagination engine.
Per the user, do the format/cleanup first (Phase A here); pagination is Phase B.

## Decisions (from brainstorming, 2026-07-12)

- **Numbering:** per-block continuous numbering, laper-style — every block
  (scene heading included) gets one incrementing number, shown on HOVER with the
  6-dot drag handle. Removes the current "scene-number badge + block-number"
  double scheme the user flagged as inconsistent.
- **Chapter list:** the legacy orphan-chapter list (生死边界的对峙 …) is NOT
  script content — move it OUT of the Script tab, into the **Outline** tab.
- **Pagination:** deferred to Phase B.

## Phase A scope

**A1 — Unified per-block numbering.** Remove the scene-number badge. Give every
block a continuous document-order number (1..N), scene headings included. Reveal
number + 6-dot handle on hover of that block only (not focus-within). One handle
style for scene and element rows.

**A2 — Kill the empty box.** A focused empty block must show ONLY the caret — no
box-shadow ring, no rounded border, no tint, no 60ch-wide empty rectangle.
Verify why the box still showed after v0.25.256 (deploy lag vs a real
higher-specificity rule) and root-cause it, not just add another override.

**A3 — Chapter list → Outline.** Stop rendering `ChapterFallback` orphan cards in
the Script tab. Surface the unstarted chapters (with their "Start Writing" entry)
in the Outline (大纲) view instead. Script tab shows only script scenes.

**A4 — Format cleanup.** The remaining points of the 16-item audit: faint
non-italic Courier placeholder for unset headings, whisper-faint "按 Tab" hint,
slug/action flush to one left margin, monochrome text, calmer vertical rhythm.

**A5 — One handle style.** Scene-row and element-row drag handles render
identically (same 6-dot glyph, size, colour, position, margin column).

## Verification (hard requirement)

Every change verified against a headless reproduction of BOTH the user's real
EMPTY state (unset headings + seeded empty blocks + no chapter list) AND a
FILLED state (real script content), using the editor's real CSS + real DOM —
not just the happy-path filled preview that hid the empty-state ugliness before.
Then merge and let the user review on prod (merge-then-review; the user reviews
on prod, not a PR preview — see feedback_merge_then_review_on_prod).

## Out of scope (Phase B / later)

- A4 pagination engine (page breaks, page numbers, dialogue-no-split, MORE/
  CONT'D). Separate spec.
- Any data-model or backend change.
- The right Writing panel's feature set (laper has Pagination/Beats/Shots/
  Relations; not part of Phase A).

## Files (anticipated)

- `editor/components/editorShellStyles.ts` — numbering, box, format, handle CSS.
- `editor/render/layoutShared.ts` — per-block continuous number (index across
  the whole document, not per-scene) + handle; remove scene-badge coupling.
- `editor/render/HollywoodLayout.tsx` / `AsianLayout.tsx` — pass document-order
  index.
- `editor/components/SceneBlock.tsx` — stop rendering the scene-number badge;
  feed continuous index.
- `editor/components/EditorShell.tsx` — move `ChapterFallback` out of the Script
  render into the Outline path.
- `editor/components/OutlineView.tsx` — surface unstarted chapters + Start
  Writing entry.
