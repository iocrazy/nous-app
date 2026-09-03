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
// The repeat-dragover case below is a BEHAVIOURAL pin, not a render-count one:
// it proves the hint is derived from a value that repeats, which is what makes
// the bail-out reachable. It cannot by itself prove the bail-out happens —
// only a render counter could, and counting renders of a component this size
// would pin incidental re-render sources too and go stale on the next
// unrelated edit. Stated rather than papered over.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
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
vi.mock('../../../../services/resourceService', () => ({
  fetchPromptAssets: vi.fn().mockResolvedValue([]),
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { LIBRARY_DND_MIME } from '../../library/dropLibraryItems';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0,
  deletable: true, draggable: true, selectable: true,
} as const;

const DATA = {
  body: 'pos', provider_slug: '', agent_id: null,
  run_status: 'idle', resource_refs: [],
};

/** The REAL `DataTransfer` interface — a `types` list plus `getData` by key. */
function libraryDataTransfer(): DataTransfer {
  const store = new Map<string, string>([[LIBRARY_DND_MIME, JSON.stringify({ items: [] })]]);
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
});

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

  it('a viewer gets no hint — the node is not a drop target at all', () => {
    const node = mount(true);
    const evt = new Event('dragover', { bubbles: true, cancelable: true });
    Object.assign(evt, { dataTransfer: libraryDataTransfer(), altKey: false });
    fireEvent(node, evt);

    expect(screen.queryByTestId('prompt-drop-hint')).toBeNull();
    expect(evt.defaultPrevented).toBe(false);
  });
});
