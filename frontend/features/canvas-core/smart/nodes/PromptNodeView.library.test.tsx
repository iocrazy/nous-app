// features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx
// The two doors a prompt card has onto the Library panel: the header's fixed
// Open Library button (Media page) and the bookshelf (Prompts page). Both aim
// the panel at THIS node, so the target bar names the card the user clicked.
//
// The in-card prompt-template picker the bookshelf used to mount is gone — the
// panel's Prompts page replaced it, and one library per canvas is the point.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [],
}));

import { getMentionHandle } from '../../library/mentionHandles';
import { useLibraryStore } from '../../library/libraryStore';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

const BASE_DATA = {
  body: 'pos',
  provider_slug: '',
  agent_id: null,
  run_status: 'idle',
  resource_refs: [],
};

function setNode() {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 400, y: 200 }, data: BASE_DATA }],
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

// ── The FIXED Open Library button (follow-up Task 3) ─────────────────────────
//
// Before this, the only door to the panel from a prompt card was the dashed
// "Add reference" button, which exists on gen kinds alone — so a Text prompt
// had no way to open the Library at all. The header button is drawn for EVERY
// kind, and for a Text node the panel commits mentions rather than references
// (a text run sends `body` only, so a `manual_refs` entry would be dropped
// without a word).

const IMAGE_DATA = {
  ...BASE_DATA,
  gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
};

function renderNode(data: Record<string, unknown>) {
  return render(
    <ReactFlowProvider>
      <PromptNodeView {...baseProps} id="p1" type="prompt" data={data} />
    </ReactFlowProvider>,
  );
}

describe('PromptNodeView Open Library button', () => {
  it('is on a Text-kind card, which had no door to the panel at all', () => {
    setNode();
    renderNode(BASE_DATA);
    expect(screen.getByTestId('prompt-open-library')).toBeTruthy();
    // The dashed reference button is a gen-kind affordance and stays absent —
    // that absence is the whole reason this button exists.
    expect(screen.queryByTestId('add-reference')).toBeNull();
  });

  it('is on a gen-kind card too, beside the reference button it does not replace', () => {
    setNode();
    renderNode(IMAGE_DATA);
    expect(screen.getByTestId('prompt-open-library')).toBeTruthy();
    expect(screen.getByTestId('add-reference')).toBeTruthy();
  });

  it('aims the panel at THIS node, on the Media page', () => {
    setNode();
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    renderNode(BASE_DATA);
    fireEvent.click(screen.getByTestId('prompt-open-library'));
    expect(openPanel).toHaveBeenCalledTimes(1);
    expect(openPanel.mock.calls[0][0]).toMatchObject({
      page: 'media',
      target: { nodeId: 'p1', kind: 'prompt', title: 'pos' },
    });
    openPanel.mockRestore();
  });

  it('is not the Prompt Templates button — the two doors stay separate', () => {
    setNode();
    renderNode(BASE_DATA);
    fireEvent.click(screen.getByTestId('prompt-open-library'));
    // Opening the panel must not also open the in-card template picker.
    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
    expect(screen.getByTestId('prompt-library-button')).toBeTruthy();
  });

  it('the bookshelf button aims the panel at THIS node on the Prompts page', () => {
    setNode();
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    // The spy can arrive DIRTY. `openPanel` calls `set()`, and zustand's spread
    // copies the spy onto the new state object — so an earlier case's
    // `mockRestore()` restores a detached object and the spy stays live on the
    // store. Clearing here makes the assertion below about THIS click alone.
    openPanel.mockClear();
    renderNode(BASE_DATA);
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Templates' }));
    expect(openPanel).toHaveBeenCalledWith(expect.objectContaining({ page: 'prompts', focusSearch: true, target: { nodeId: 'p1', kind: 'prompt', title: 'pos' } }));
    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
  });

  it('registers a mention handle for this node, and drops it on unmount', () => {
    setNode();
    const view = renderNode(BASE_DATA);
    // The panel cannot see `bodyEditorRef`; this registry is how it reaches
    // the editor when the target is a Text prompt.
    expect(getMentionHandle('p1')).not.toBeNull();
    view.unmount();
    expect(getMentionHandle('p1')).toBeNull();
  });

  it('the registered handle reaches the live editor, not a captured null', () => {
    setNode();
    renderNode(BASE_DATA);
    const handle = getMentionHandle('p1');
    expect(handle).not.toBeNull();
    // A wrapper, so an editor that mounts after the effect ran is still
    // reached. Calling it must land a chip in the body rather than throw.
    handle?.insertImage(
      { url: '/api/v1/generated-media/gm-9', alias: 'Harbour', kind: 'image' },
      { consumeMention: false },
    );
    expect(screen.getByTestId('prompt-body-editor').textContent).toContain('Harbour');
  });

  it('a handle whose editor has gone THROWS — a silent no-op reads as success', () => {
    // The surface culls off-viewport cards, so the panel can hold a handle
    // whose editor unmounted between the aim and the commit. Optional-chaining
    // that away (`bodyEditorRef.current?.insertImage(...)`) returned
    // `undefined`, which `mentionLibraryItems` counted as a chip: the panel
    // said "1 inserted", cleared the pick, and the body was untouched.
    setNode();
    const view = renderNode(BASE_DATA);
    const handle = getMentionHandle('p1');
    expect(handle).not.toBeNull();
    // Unmount AFTER capturing it — the registry drops the entry, but the
    // wrapper still closes over the ref, which is exactly what the panel holds.
    view.unmount();
    expect(() =>
      handle?.insertImage(
        { url: '/api/v1/generated-media/gm-9', alias: 'Harbour', kind: 'image' },
        { consumeMention: false },
      ),
    ).toThrow(/not mounted/);
    expect(() =>
      handle?.insertAsset(
        { asset_id: 'a1', name: 'Cole', asset_type: 'character', cover_file_id: null },
        { consumeMention: false },
      ),
    ).toThrow(/not mounted/);
  });

  // The bookshelf is a BROWSE door, so it is ungated like its two siblings onto
  // the same panel — the ⌘K row and the bare `L` shortcut. The Prompts page
  // withholds every write in a read-only session and says so; a disabled button
  // would instead put templates out of reach of a canvas you can only read.
  it('the bookshelf still opens in a read-only session — the page withholds the writes', () => {
    setNode();
    useCanvasCoreStore.setState({ readOnly: true });
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    openPanel.mockClear();
    renderNode(BASE_DATA);
    const bookshelf = screen.getByTestId('prompt-library-button') as HTMLButtonElement;
    expect(bookshelf.disabled).toBe(false);
    fireEvent.click(bookshelf);
    expect(openPanel).toHaveBeenCalledWith(expect.objectContaining({ page: 'prompts' }));
  });

  it('a read-only canvas gets the button disabled rather than missing', () => {
    setNode();
    useCanvasCoreStore.setState({ readOnly: true });
    renderNode(BASE_DATA);
    expect((screen.getByTestId('prompt-open-library') as HTMLButtonElement).disabled).toBe(true);
  });
});
