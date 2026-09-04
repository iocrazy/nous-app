import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { LIBRARY_STORAGE_KEY, useLibraryStore, type LibraryTarget } from './libraryStore';

const TARGET = { nodeId: 'p1', kind: 'prompt' as const, title: 'Harbour' };

// A panel target is only ever a PROMPT node. Nothing in the tree builds any
// other kind, and both `LibraryPanel` and `LibraryMediaPage` refuse one — so a
// wider type here would let a caller arm a target the panel silently ignores.
// This is a COMPILER assertion: `@ts-expect-error` fails the typecheck if the
// line below ever stops being an error, which is what pins the narrowing.
// (`LibraryDropTarget` in dropLibraryItems.ts is a different type and still
// has its own `media` case — a drop lands on media nodes, an aim does not.)
// @ts-expect-error 'media' is not a LibraryTarget kind
const NOT_A_TARGET: LibraryTarget = { nodeId: 'm1', kind: 'media', title: 'Clip' };
void NOT_A_TARGET;

/** A board with the target node on it, so the canvas selection has something
 *  real to name. `setSelection` drops ids that are not on the board. */
function seedBoard(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: '' } },
      { id: 'p2', type: 'prompt', position: { x: 300, y: 0 }, data: { body: '' } },
    ] as never,
    connections: [],
    selection: [],
  });
}

const canvasSelection = () => useCanvasCoreStore.getState().selection;

beforeEach(() => {
  localStorage.clear();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
  seedBoard();
});
afterEach(() => {
  localStorage.clear();
  useCanvasCoreStore.getState().reset();
});

describe('libraryStore', () => {
  it('opens closed, on the Media page, with no target', () => {
    const s = useLibraryStore.getState();
    expect(s.open).toBe(false);
    expect(s.page).toBe('media');
    expect(s.target).toBeNull();
  });

  it('openPanel carries a page, a store and a target in one call', () => {
    useLibraryStore.getState().openPanel({
      page: 'media',
      mediaStore: 'generated',
      target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' },
    });
    const s = useLibraryStore.getState();
    expect(s.open).toBe(true);
    expect(s.mediaStore).toBe('generated');
    expect(s.target?.nodeId).toBe('p1');
  });

  it('toggle closes an open panel and clears the target with it', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' } });
    s().toggle();
    expect(s().open).toBe(false);
    // A target that outlives its panel would silently re-arm the next open.
    expect(s().target).toBeNull();
  });

  it('focusSearch bumps a nonce rather than holding a ref', () => {
    const before = useLibraryStore.getState().focusNonce;
    useLibraryStore.getState().openPanel({ focusSearch: true });
    expect(useLibraryStore.getState().focusNonce).toBe(before + 1);
  });

  it('persists page / store / width, and nothing else', () => {
    const s = useLibraryStore.getState();
    s.openPanel({ mediaStore: 'assets' });
    s.setWidth(420);
    s.setQuery('harbour');
    s.setSelection(['uploads:1']);
    const saved = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) ?? '{}');
    expect(saved).toEqual({ page: 'media', mediaStore: 'assets', width: 420 });
  });

  // ── Spec §3.1: the aimed-at node is highlighted while the panel points at
  // it, and un-highlighted when it stops. The panel is on the right and names
  // the node in its target bar, but a name is not a location once a board has
  // a dozen cards — nothing on the canvas said which one was receiving.
  it('opening WITH a target selects that node, so the board shows the aim', () => {
    useLibraryStore.getState().openPanel({ target: TARGET });
    expect(canvasSelection()).toEqual(['p1']);
  });

  it('opening with NO target leaves the board selection alone', () => {
    useCanvasCoreStore.getState().setSelection(['p2']);
    useLibraryStore.getState().openPanel();
    expect(canvasSelection()).toEqual(['p2']);
  });

  it('closing releases the highlight it put there', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: TARGET });
    s().close();
    expect(canvasSelection()).toEqual([]);
  });

  it('Stop Aiming releases it too — the target bar is the other way out', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: TARGET });
    s().clearTarget();
    expect(canvasSelection()).toEqual([]);
  });

  it('a selection the user moved on to is NOT cleared by closing', () => {
    // Between opening and closing the user can click another card. Clearing
    // unconditionally would deselect something this panel never selected.
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: TARGET });
    useCanvasCoreStore.getState().setSelection(['p2']);
    s().close();
    expect(canvasSelection()).toEqual(['p2']);
  });

  it('a corrupt stored value is ignored, not thrown on', () => {
    localStorage.setItem(LIBRARY_STORAGE_KEY, '{not json');
    expect(() => useLibraryStore.getState().setWidth(360)).not.toThrow();
  });
});
