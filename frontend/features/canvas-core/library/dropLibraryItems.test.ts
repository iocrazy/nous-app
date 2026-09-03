// features/canvas-core/library/dropLibraryItems.test.ts
//
// The drag payload and where a drop lands. `DataTransfer` does not exist in
// jsdom, so the stub below is the REAL interface — `types` is a list of MIME
// strings and `getData` answers by key. Anything looser (an object with an
// `items` field, say) would test a shape the browser never produces.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const addReferences = vi.fn();
const placeLibraryItems = vi.fn();

vi.mock('./addReferences', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  addReferences: (...a: unknown[]) => addReferences(...a),
}));
vi.mock('./placeLibraryItems', () => ({
  placeLibraryItems: (...a: unknown[]) => placeLibraryItems(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  LIBRARY_DND_MIME,
  dropConsequenceKey,
  dropLibraryItems,
  hasLibraryDrag,
  readLibraryDrag,
  writeLibraryDrag,
} from './dropLibraryItems';
import type { LibraryItem } from './librarySearch';

function fakeDataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    get types() {
      return [...store.keys()];
    },
    setData: (k: string, v: string) => void store.set(k, v),
    getData: (k: string) => store.get(k) ?? '',
    effectAllowed: 'all',
    dropEffect: 'none',
  } as unknown as DataTransfer;
}

const ITEMS: LibraryItem[] = [
  { store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: 'x', kind: 'image' },
  { store: 'assets', id: '727145299382534300', title: 'Cole Bannon', thumbUrl: 'y', kind: 'character', ready: true },
];

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      { id: 'm1', type: 'media', position: { x: 0, y: 0 }, data: { title: 'Media', items: [] } },
    ] as never,
    connections: [],
    selection: [],
  });
}

beforeEach(() => {
  seed();
  addReferences.mockReset().mockResolvedValue({ added: 2, skipped: 0, failed: [] });
  placeLibraryItems.mockReset().mockResolvedValue({
    nodeIds: ['n1'], inserted: 2, skipped: 0, failed: [],
  });
});
afterEach(() => useCanvasCoreStore.getState().reset());

describe('the drag payload', () => {
  it('round-trips through one MIME slot, keeping string ids as strings', () => {
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);
    expect(dt.types).toContain(LIBRARY_DND_MIME);
    const back = readLibraryDrag(dt)!;
    expect(back.map((i) => i.id)).toEqual([
      '800000000000000001',
      '727145299382534300',
    ]);
    expect(typeof back[0].id).toBe('string');
  });

  it('also writes text/plain, so a drag into a text field is not garbage', () => {
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);
    expect(dt.getData('text/plain')).toBe('A wide shot, Cole Bannon');
  });

  it('a foreign drag reads as null rather than as an empty selection', () => {
    const dt = fakeDataTransfer();
    dt.setData('text/plain', 'hello');
    expect(hasLibraryDrag(dt)).toBe(false);
    expect(readLibraryDrag(dt)).toBeNull();
  });

  it('a malformed payload reads as null, not as a throw during a drop', () => {
    const dt = fakeDataTransfer();
    dt.setData(LIBRARY_DND_MIME, '{not json');
    expect(readLibraryDrag(dt)).toBeNull();
  });
});

describe('dropLibraryItems', () => {
  it('an empty-pane drop places nodes at the drop point', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'canvas', position: { x: 12, y: 34 } }, 's');
    expect(placeLibraryItems).toHaveBeenCalledWith(ITEMS, 's', { x: 12, y: 34 });
    expect(r).toMatchObject({ handled: true, placed: 2 });
  });

  it('a prompt drop adds references, and creates no node', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'prompt', nodeId: 'p1', mention: false }, 's');
    expect(addReferences).toHaveBeenCalledWith('p1', ITEMS, 's');
    expect(placeLibraryItems).not.toHaveBeenCalled();
    expect(r).toMatchObject({ handled: true, referenced: 2, placed: 0 });
  });

  it('⌥ over a prompt does NOT write manual_refs — the node inserts the mention', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'prompt', nodeId: 'p1', mention: true }, 's');
    expect(addReferences).not.toHaveBeenCalled();
    expect(r).toMatchObject({ handled: true, referenced: 0, mentioned: 0 });
  });

  it('a media drop appends to that card and creates no second one', async () => {
    const urls = () =>
      (
        useCanvasCoreStore
          .getState()
          .nodes.find((n) => (n as { id: string }).id === 'm1') as {
          data: { items: Array<{ url: string }> };
        }
      ).data.items.map((i) => i.url);

    await dropLibraryItems(
      [ITEMS[0]],
      { kind: 'media', nodeId: 'm1' },
      's',
    );
    expect(urls()).toEqual(['/api/v1/generated-media/800000000000000001/file']);

    // The SAME pick again — the gesture a user makes when they cannot tell
    // whether the first drop landed. Without the url dedupe the card shows
    // the one file twice, and nothing downstream ever notices.
    const second = await dropLibraryItems(
      [ITEMS[0]],
      { kind: 'media', nodeId: 'm1' },
      's',
    );
    expect(urls()).toEqual(['/api/v1/generated-media/800000000000000001/file']);
    expect(second).toMatchObject({ handled: true, placed: 0 });
    expect(placeLibraryItems).not.toHaveBeenCalled();
  });

  it('an empty item list is not handled, so the caller does not claim it did something', async () => {
    expect(await dropLibraryItems([], { kind: 'canvas', position: { x: 0, y: 0 } }, 's')).toMatchObject({
      handled: false,
    });
  });

  // The node-level handler is the only caller that knows the target model's
  // ceiling — `dropLibraryItems` cannot ask for it without reaching into a
  // hook. So it forwards what it is given, and forwarding it is pinned here:
  // dropping five picks onto a node with a two-ref model must not be the one
  // path into `manual_refs` that ignores the quota.
  it('a prompt drop hands the caller\'s ceiling down to addReferences', async () => {
    await dropLibraryItems(
      ITEMS,
      { kind: 'prompt', nodeId: 'p1', mention: false },
      's',
      { maxRefs: 3 },
    );
    expect(addReferences).toHaveBeenCalledWith('p1', ITEMS, 's', { maxRefs: 3 });
  });
});

describe('dropConsequenceKey', () => {
  it('says a different thing for each of the four landings', () => {
    expect(dropConsequenceKey({ kind: 'canvas', position: { x: 0, y: 0 } })).toBe(
      'canvas.library.dropPlace',
    );
    expect(dropConsequenceKey({ kind: 'prompt', nodeId: 'p1', mention: false })).toBe(
      'canvas.library.dropAddReference',
    );
    expect(dropConsequenceKey({ kind: 'prompt', nodeId: 'p1', mention: true })).toBe(
      'canvas.library.dropInsertMention',
    );
    expect(dropConsequenceKey({ kind: 'media', nodeId: 'm1' })).toBe(
      'canvas.library.dropAppend',
    );
  });
});
