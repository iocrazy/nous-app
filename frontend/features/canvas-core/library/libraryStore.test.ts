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

  it('the Files source chip resets when the segment changes', () => {
    // Same reason `kind` resets: the chips are per-segment. A source left
    // pressed from Files would survive a trip to Generated invisibly (that
    // segment draws no source row) and then narrow the shelf on the way back
    // with nothing on screen saying why.
    const s = () => useLibraryStore.getState();
    s().setUploadSource('web');
    expect(s().uploadSource).toBe('web');
    s().setMediaStore('generated');
    expect(s().uploadSource).toBe('all');
  });

  it('the source chip is NOT persisted — only page / store / width are', () => {
    // A restored "Downloaded" would open the shelf tomorrow already filtered,
    // with the chip the only clue, on a panel the user last used for uploads.
    const s = () => useLibraryStore.getState();
    s().openPanel({ mediaStore: 'uploads' });
    s().setUploadSource('web');
    const saved = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) ?? '{}');
    expect(saved.uploadSource).toBeUndefined();
    expect(saved).toEqual({ page: 'media', mediaStore: 'uploads', width: 340 });
  });

  it('the Assets shelf starts narrowed to the library', () => {
    // The user's ruling: library membership is something somebody DID. The
    // shelf's job is to show what they added, and the pill is the way out.
    expect(useLibraryStore.getState().assetsInLibraryOnly).toBe(true);
  });

  it('the in-library toggle is NOT persisted — only page / store / width are', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ mediaStore: 'assets' });
    s().setAssetsInLibraryOnly(false);
    const saved = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) ?? '{}');
    expect(saved.assetsInLibraryOnly).toBeUndefined();
    expect(saved).toEqual({ page: 'media', mediaStore: 'assets', width: 340 });
  });

  it('widening the Assets shelf drops the selection with it', () => {
    // Same reason the scope setters clear it: the keys resolve against rows
    // the next query may not return, so a kept selection would be counted by
    // the footer and dropped at send time.
    const s = () => useLibraryStore.getState();
    s().setSelection(['assets:727145299382534300']);
    s().setAssetsInLibraryOnly(false);
    expect(s().selection).toEqual([]);
  });

  it('choosing a source drops the selection with it', () => {
    // Keys resolve against rows the next query may not return, so a kept
    // selection would be counted by the footer and dropped at send time.
    const s = () => useLibraryStore.getState();
    s().setSelection(['uploads:655000000000000001']);
    s().setUploadSource('upload');
    expect(s().selection).toEqual([]);
  });

  it('openPanel naming a DIFFERENT segment drops the kind chip with it', () => {
    // A programmatic switch is a switch. The prompt card's Open Library button
    // forces `uploads`, so a `character` chip chosen on Assets would arrive on
    // the Files shelf, narrow it to nothing, and draw no chip saying why —
    // `KIND_LABEL.uploads` has no `character` entry to press.
    const s = () => useLibraryStore.getState();
    s().openPanel({ mediaStore: 'assets' });
    s().setKind('character');
    s().openPanel({ mediaStore: 'uploads' });
    expect(s().kind).toBeNull();
  });

  it('openPanel on the SAME segment keeps the kind chip — nothing changed', () => {
    // The `L` shortcut and the target-arming path both re-open on whatever
    // segment is showing. Resetting there would clear a chip the user can see
    // and just chose, with the shelf underneath unchanged.
    const s = () => useLibraryStore.getState();
    s().openPanel({ mediaStore: 'uploads' });
    s().setKind('image');
    s().openPanel({ mediaStore: 'uploads' });
    expect(s().kind).toBe('image');
    s().openPanel();
    expect(s().kind).toBe('image');
  });

  it('the in-library pill survives a programmatic switch — it is not in-segment', () => {
    // Kept on purpose, unlike `kind`: the pill sits in the Assets scope row
    // and is drawn `aria-pressed` whenever that row is on screen, so nobody
    // is narrowed by a control they cannot see.
    const s = () => useLibraryStore.getState();
    s().openPanel({ mediaStore: 'assets' });
    s().setAssetsInLibraryOnly(false);
    s().openPanel({ mediaStore: 'uploads' });
    expect(s().assetsInLibraryOnly).toBe(false);
  });

  it('a corrupt stored value is ignored, not thrown on', () => {
    localStorage.setItem(LIBRARY_STORAGE_KEY, '{not json');
    expect(() => useLibraryStore.getState().setWidth(360)).not.toThrow();
  });
});
