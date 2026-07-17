/**
 * M3 paged-mode seams as PM widget decorations (spec D5/M3 item 2).
 * `TipTapSceneEditor`'s `pageSeams` prop feeds `pageSeamPlugin.ts`, which
 * renders the byte-identical `PageSeam.tsx` DOM as a widget immediately
 * before the matching `[data-el-id]` row.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor, act } from '@testing-library/react';
import { TipTapSceneEditor } from '../tiptap/TipTapSceneEditor';
import type { ScriptElement } from '../types';

const zeroRect = {
  bottom: 0,
  height: 0,
  left: 0,
  right: 0,
  toJSON: () => ({}),
  top: 0,
  width: 0,
  x: 0,
  y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

const elements: ScriptElement[] = [
  { id: 'e1', type: 'action', text: 'First.', character_id: null },
  { id: 'e2', type: 'action', text: 'Second.', character_id: null },
  { id: 'e3', type: 'action', text: 'Third.', character_id: null },
];

const noopDispatch = () => {};

afterEach(() => {
  cleanup();
});

/** The seam's ordinal position among ALL direct children of the PM content
 *  root, relative to the row it should sit immediately before — asserts DOM
 *  ADJACENCY (seam is the row's previous sibling), not just presence. */
function seamImmediatelyBefore(root: HTMLElement, elementId: string): boolean {
  const row = root.querySelector(`[data-el-id="${elementId}"]`);
  if (!row) return false;
  // The row DOM the seam must sit before is the top-level node DOM (the
  // NodeViewWrapper) — walk up from the content div to it: its closest
  // ancestor that is itself a direct child of the ProseMirror content root.
  const pmRoot = root.querySelector('.ProseMirror') as HTMLElement;
  let topLevel: Element | null = row;
  while (topLevel && topLevel.parentElement !== pmRoot) {
    topLevel = topLevel.parentElement;
  }
  if (!topLevel) return false;
  const prev = topLevel.previousElementSibling;
  return !!prev && prev.classList.contains('mh-page-seam');
}

describe('M3 — page seam widget decorations', () => {
  it('renders no seams when the map is empty', async () => {
    const { container } = render(
      <TipTapSceneEditor initialElements={elements} format="hollywood" dispatchOps={noopDispatch} />,
    );
    await waitFor(() => expect(container.querySelectorAll('.mh-el-row').length).toBe(3));
    expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(0);
  });

  it('renders a seam immediately before the matching element, with the filler paddingTop + page number', async () => {
    const pageSeams = new Map([['e2', { page: 1, filler: 42 }]]);
    const { container } = render(
      <TipTapSceneEditor
        initialElements={elements}
        format="hollywood"
        dispatchOps={noopDispatch}
        pageSeams={pageSeams}
      />,
    );
    await waitFor(() => expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(1));

    expect(seamImmediatelyBefore(container, 'e2')).toBe(true);

    const seam = container.querySelector('.mh-page-seam') as HTMLElement;
    expect(seam.style.paddingTop).toBe('42px');
    expect(seam.getAttribute('aria-hidden')).toBe('true');
    expect(seam.getAttribute('contenteditable')).toBe('false');
    expect(seam.getAttribute('data-testid')).toBe('page-seam');
    expect(seam.querySelector('.mh-page-seam-rule')).not.toBeNull();
    // Page number = the page that ENDS here (no trailing period), not page + 1.
    expect(seam.querySelector('.mh-page-seam-num')?.textContent).toBe('1');
  });

  it('supports multiple seams, each before its own row', async () => {
    const pageSeams = new Map([
      ['e2', { page: 1, filler: 10 }],
      ['e3', { page: 2, filler: 20 }],
    ]);
    const { container } = render(
      <TipTapSceneEditor
        initialElements={elements}
        format="hollywood"
        dispatchOps={noopDispatch}
        pageSeams={pageSeams}
      />,
    );
    await waitFor(() => expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(2));
    expect(seamImmediatelyBefore(container, 'e2')).toBe(true);
    expect(seamImmediatelyBefore(container, 'e3')).toBe(true);
  });

  it('a pageSeams prop update MOVES the seam without a doc edit', async () => {
    const { container, rerender } = render(
      <TipTapSceneEditor
        initialElements={elements}
        format="hollywood"
        dispatchOps={noopDispatch}
        pageSeams={new Map([['e2', { page: 1, filler: 5 }]])}
      />,
    );
    await waitFor(() => expect(seamImmediatelyBefore(container, 'e2')).toBe(true));

    act(() => {
      rerender(
        <TipTapSceneEditor
          initialElements={elements}
          format="hollywood"
          dispatchOps={noopDispatch}
          pageSeams={new Map([['e3', { page: 1, filler: 5 }]])}
        />,
      );
    });

    await waitFor(() => {
      expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(1);
      expect(seamImmediatelyBefore(container, 'e3')).toBe(true);
    });
    expect(seamImmediatelyBefore(container, 'e2')).toBe(false);
  });

  it('renders in Asian format too, before the whole .as-row (marks included)', async () => {
    const pageSeams = new Map([['e2', { page: 0, filler: 3 }]]);
    const { container } = render(
      <TipTapSceneEditor
        initialElements={[
          { id: 'e1', type: 'action', text: 'First.', character_id: null },
          { id: 'e2', type: 'character', text: 'BOB', character_id: null },
        ]}
        format="asian"
        dispatchOps={noopDispatch}
        pageSeams={pageSeams}
      />,
    );
    await waitFor(() => expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(1));
    // The seam sits before the node's OUTER DOM. NOTE: ReactNodeViewRenderer
    // wraps every node in a `div.react-renderer` — the widget renders as a
    // sibling of THAT wrapper (verified via DOM dump), so assert against the
    // wrapper, not the inner `.as-row`.
    const asRow = container.querySelector('.as-row-character') as HTMLElement;
    const wrapper = asRow.closest('.react-renderer') as HTMLElement;
    const seam = wrapper.previousElementSibling;
    expect(seam?.classList.contains('mh-page-seam')).toBe(true);
    // The ornament marks live INSIDE the row that follows the seam — a seam
    // never separates a row from its marks.
    expect(asRow.querySelector('.as-mark.as-suffix')).not.toBeNull();
  });
});

describe('M3 — shell measurement compatibility (reasoned + verified)', () => {
  // `EditorShell`'s measurement effect (~L772-800) queries
  // `.mh-scene-headrow, .mh-el-row, .mh-scene-placeholder` for ROWS and
  // `.mh-page-seam` separately to subtract already-rendered seam heights —
  // both are plain `querySelectorAll` calls scoped to the whole `sheet`
  // container, so PM's internal DOM nesting (widgets/NodeViews living
  // several levels deep inside `.ProseMirror`) is irrelevant: this test
  // pins that BOTH selectors still resolve correctly against a mounted
  // TipTapSceneEditor's DOM.
  it('`.mh-el-row` resolves the SAME count as element rows, and `.mh-page-seam` resolves seams, from one querySelectorAll pass over the container', async () => {
    const pageSeams = new Map([['e2', { page: 1, filler: 8 }]]);
    const { container } = render(
      <TipTapSceneEditor
        initialElements={elements}
        format="hollywood"
        dispatchOps={noopDispatch}
        pageSeams={pageSeams}
      />,
    );
    await waitFor(() => expect(container.querySelectorAll('.mh-el-row')).toHaveLength(3));
    expect(container.querySelectorAll('.mh-page-seam')).toHaveLength(1);

    // Each `.mh-el-row` still carries a descendant `[data-el-id]` the shell
    // reads for its `key`/`kind` (see EditorShell.tsx's row-walk).
    const rows = Array.from(container.querySelectorAll<HTMLElement>('.mh-el-row'));
    for (const row of rows) {
      expect(row.querySelector('[data-el-id]')).not.toBeNull();
    }
  });
});
