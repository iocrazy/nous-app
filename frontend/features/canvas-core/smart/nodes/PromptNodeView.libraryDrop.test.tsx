// features/canvas-core/smart/nodes/PromptNodeView.libraryDrop.test.tsx
//
// The prompt node's drop hint: what it SAYS, and that a drag holding still
// does not keep changing it.
//
// `dropHint` is a PRIMITIVE (`'mention' | 'reference' | null`) rather than the
// `LibraryDropTarget` object it briefly was. `dragover` fires at pointer-move
// rate, and a fresh object fails React's `Object.is` bail-out every frame, so
// the object version re-rendered this 1000-line node for the whole hover —
// the exact cost the node registry's `memo` was added to remove.
//
// The `⌥` cases at the bottom are spec §4.4's acceptance ("正文追加 chip") and
// its other half: a mention must NOT write `manual_refs`. They drive the real
// drop handler and assert on the EDITOR HANDLE, because that is the seam the
// insert crosses — the body itself is TipTap's, and the first cut of this
// feature wrote plain text through it that looked fine and delivered nothing.
//
// The repeat-dragover case below is a BEHAVIOURAL pin, not a render-count one:
// it proves the hint is derived from a value that repeats, which is what makes
// the bail-out reachable. It cannot by itself prove the bail-out happens —
// only a render counter could, and counting renders of a component this size
// would pin incidental re-render sources too and go stale on the next
// unrelated edit. Stated rather than papered over.

import React from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// `t(dropConsequenceKey(...))` passes NO English default — the key is the whole
// message — so a `(k, d) => d ?? k` stub would assert a key rather than what a
// user reads. Resolving against the real `en.json` means these cases also fail
// if a key and its locale entry ever drift apart.
const { EN } = vi.hoisted(() => ({
  EN: JSON.parse(
    require('node:fs').readFileSync(
        require('node:path').resolve(__dirname, '../../../../public/locales/en.json'),
      'utf8',
    ),
  ) as Record<string, unknown>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: string) =>
      (k.split('.').reduce<unknown>(
        (n, part) => (n && typeof n === 'object' ? (n as Record<string, unknown>)[part] : undefined),
        EN,
      ) as string | undefined) ?? d ?? k,
  }),
}));
vi.mock('./useGenerationModels', () => ({ useGenerationModels: () => [] }));

// The editor is stubbed down to its HANDLE. `⌥` inserts chips through
// `insertImage` / `insertAsset`, and those calls are the observable the real
// TipTap document cannot give a jsdom test — `insertText` is exposed too so a
// regression back to plain text is visible as a call, not as an absence.
const editorHandle = {
  insertImage: vi.fn(),
  insertAsset: vi.fn(),
  insertText: vi.fn(),
  focus: vi.fn(),
};
vi.mock('./PromptBodyEditor', () => ({
  PromptBodyEditor: React.forwardRef(function PromptBodyEditorStub(_props, ref) {
    React.useImperativeHandle(ref, () => editorHandle);
    return <div data-testid="prompt-body-editor" />;
  }),
}));

const fetchAssetDetail = vi.fn();
const importResourceAsCanvasMedia = vi.fn();
vi.mock('../../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));
vi.mock('../mediaImport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));
vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { LIBRARY_DND_MIME } from '../../library/dropLibraryItems';
import { getMentionHandle } from '../../library/mentionHandles';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0,
  deletable: true, draggable: true, selectable: true,
} as const;

const EXISTING_REF = { url: '/api/v1/generated-media/700000000000000009/file', kind: 'image' };

const DATA = {
  body: 'pos', provider_slug: '', agent_id: null,
  run_status: 'idle', resource_refs: [],
  // Non-empty on purpose: "a mention does not touch `manual_refs`" is only a
  // real assertion against a list that had something in it to lose.
  manual_refs: [EXISTING_REF],
};

/** The REAL `DataTransfer` interface — a `types` list plus `getData` by key. */
function libraryDataTransfer(
  items: Array<{ store: string; id: string; kind: string; title: string }> = [],
): DataTransfer {
  const store = new Map<string, string>([[LIBRARY_DND_MIME, JSON.stringify({ items })]]);
  return {
    get types() {
      return [...store.keys()];
    },
    setData: (k: string, v: string) => void store.set(k, v),
    getData: (k: string) => store.get(k) ?? '',
  } as unknown as DataTransfer;
}

function dragOver(el: Element, dt: DataTransfer, altKey: boolean): void {
  const evt = new Event('dragover', { bubbles: true, cancelable: true });
  Object.assign(evt, { dataTransfer: dt, altKey });
  fireEvent(el, evt);
}

function mount(readOnly = false) {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '9', loadStatus: 'ready', readOnly,
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: DATA }],
    connections: [], selection: [],
  } as never);
  render(
    <ReactFlowProvider>
      <PromptNodeView {...baseProps} id="p1" type="prompt" data={DATA} />
    </ReactFlowProvider>,
  );
  return screen.getByTestId('smart-prompt-node');
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  editorHandle.insertImage.mockReset();
  editorHandle.insertAsset.mockReset();
  editorHandle.insertText.mockReset();
});

/** The dropped payload for the ⌥ cases: one asset and two pictures. */
const THREE = [
  { store: 'assets', id: '727145299382534300', kind: 'character', title: 'Cole Bannon' },
  { store: 'generated', id: '800000000000000001', kind: 'image', title: 'A wide shot' },
  { store: 'generated', id: '800000000000000002', kind: 'image', title: 'Harbour at dusk' },
];

function refsOn(nodeId: string): Array<{ url: string }> {
  return (
    (useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === nodeId) as {
      data?: { manual_refs?: Array<{ url: string }> };
    }).data?.manual_refs ?? []
  );
}

function drop(el: Element, dt: DataTransfer, altKey: boolean): void {
  const evt = new Event('drop', { bubbles: true, cancelable: true });
  Object.assign(evt, { dataTransfer: dt, altKey });
  fireEvent(el, evt);
}

describe('PromptNodeView library drop hint', () => {
  it('says which of the two things the drop will do, and switches with ⌥', () => {
    const node = mount();
    const dt = libraryDataTransfer();
    expect(screen.queryByTestId('prompt-drop-hint')).toBeNull();

    dragOver(node, dt, false);
    expect(screen.getByTestId('prompt-drop-hint').textContent).toBe('Add as Reference');

    dragOver(node, dt, true);
    expect(screen.getByTestId('prompt-drop-hint').textContent).toBe('Insert as Mention');
  });

  it('holding the drag still keeps the hint on ONE answer', () => {
    const node = mount();
    const dt = libraryDataTransfer();

    dragOver(node, dt, true);
    const first = screen.getByTestId('prompt-drop-hint').textContent;
    // Three more frames of the same hover — a real drag fires these at
    // pointer-move rate without the user doing anything new.
    dragOver(node, dt, true);
    dragOver(node, dt, true);
    dragOver(node, dt, true);
    expect(screen.getByTestId('prompt-drop-hint').textContent).toBe(first);
    expect(first).toBe('Insert as Mention');
  });

  it('leaving clears it, so a hint never outlives the drag', () => {
    const node = mount();
    dragOver(node, libraryDataTransfer(), false);
    expect(screen.getByTestId('prompt-drop-hint')).toBeTruthy();

    fireEvent.dragLeave(node);
    expect(screen.queryByTestId('prompt-drop-hint')).toBeNull();
  });

  it('⌥ inserts a CHIP per item — spec §4.4, and the reason the label is honest', async () => {
    fetchAssetDetail.mockResolvedValue({
      id: THREE[0].id, asset_type: 'character', name: 'Cole Bannon',
      cover_file_id: '600000000000000001', readiness: { state: 'ready', missing: [] },
      files: [], links: [], linked_by: [], loadouts: [],
    });
    const node = mount();

    drop(node, libraryDataTransfer(THREE), true);

    // Three items → three chips. The version this replaces looped
    // `insertText`, whose caret handling deleted the previous item's mention,
    // so five items left ONE — and none of the three was a chip a run reads.
    await waitFor(() => expect(editorHandle.insertAsset).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(editorHandle.insertImage).toHaveBeenCalledTimes(2));
    expect(editorHandle.insertImage.mock.calls.map((c) => c[0].alias)).toEqual([
      'A wide shot',
      'Harbour at dusk',
    ]);
    expect(editorHandle.insertText).not.toHaveBeenCalled();
  });

  it('⌥ leaves manual_refs alone — a mention is not a reference', async () => {
    fetchAssetDetail.mockResolvedValue({
      id: THREE[0].id, asset_type: 'character', name: 'Cole Bannon',
      cover_file_id: null, readiness: { state: 'ready', missing: [] },
      files: [], links: [], linked_by: [], loadouts: [],
    });
    const node = mount();
    expect(refsOn('p1')).toEqual([EXISTING_REF]);

    drop(node, libraryDataTransfer(THREE), true);
    await waitFor(() => expect(editorHandle.insertImage).toHaveBeenCalledTimes(2));

    // Untouched: the two paths write to two different places, and the whole
    // point of the ⌥ modifier is choosing between them.
    expect(refsOn('p1')).toEqual([EXISTING_REF]);
  });

  it('WITHOUT ⌥ the same drop adds references and inserts no chip', async () => {
    const node = mount();

    drop(node, libraryDataTransfer([THREE[1]]), false);

    await waitFor(() => expect(refsOn('p1')).toHaveLength(2));
    expect(refsOn('p1')[1].url).toBe('/api/v1/generated-media/800000000000000001/file');
    expect(editorHandle.insertImage).not.toHaveBeenCalled();
    expect(editorHandle.insertAsset).not.toHaveBeenCalled();
  });

  // Ruling R16. The panel is a BUTTON, not the `@` picker, so it has no
  // pending query — and the editor's `insertText` defaults to deleting back to
  // the last literal `@` within 80 characters. A wrapper that forwarded the
  // default would eat text the user wrote: the plain-text twin of the
  // `contact me at foo@bar.com` bug the chip inserters already opt out of.
  //
  // Spied here rather than driven through the real editor because THIS is the
  // seam under test — that the registered wrapper passes the option. The
  // editor's own honouring of it is pinned in `PromptBodyEditor.test.tsx`
  // against a real TipTap document.
  it('the registered insertText opts OUT of consuming a pending @query', () => {
    mount();

    getMentionHandle('p1')?.insertText('a wide shot');

    expect(editorHandle.insertText).toHaveBeenCalledWith('a wide shot', {
      consumeMention: false,
    });
  });

  it('a viewer gets no hint — the node is not a drop target at all', () => {
    const node = mount(true);
    const evt = new Event('dragover', { bubbles: true, cancelable: true });
    Object.assign(evt, { dataTransfer: libraryDataTransfer(), altKey: false });
    fireEvent(node, evt);

    expect(screen.queryByTestId('prompt-drop-hint')).toBeNull();
    expect(evt.defaultPrevented).toBe(false);
  });
});
