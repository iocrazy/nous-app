/**
 * PromptNodeView — IC-parity ⑤ input-image row + @-picker「输入图」tab.
 *
 * The wired inputs (resolveSourceUrls) render as a thumbnail row above the
 * textarea (Infinite's「2 输入图」); clicking one selects it as the i2i
 * source (data.source_ref, toggles off on re-click). Typing @ opens a
 * two-tab picker — Input Images / Assets — defaulting to Input when
 * inputs exist (IC rule); picking an input inserts @Image N and sets
 * source_ref.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { PromptNodeView } from './PromptNodeView';
import type { ResourceSearchResponse } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn(),
}));
import { useResourceSearch } from '../../../../hooks/useResourceSearch';

const EMPTY_SEARCH: ResourceSearchResponse = {
  results: [],
  counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

const URL_A = '/api/v1/generated-media/a.png';
const URL_B = '/api/v1/generated-media/b.png';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

function seed(withInputs: boolean): void {
  useCanvasCoreStore.getState().reset();
  const media: CanvasNode = {
    id: 'm1',
    type: 'media',
    position: { x: 0, y: 0 },
    data: {
      title: 'Media',
      items: [
        { url: URL_A, kind: 'image', name: 'a.png' },
        { url: URL_B, kind: 'image', name: 'b.png' },
      ],
    },
  } as unknown as CanvasNode;
  const prompt: CanvasNode = {
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 300 },
    data: {
      body: '',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
    },
  } as unknown as CanvasNode;
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: withInputs ? [media, prompt] : [prompt],
    connections: withInputs
      ? [{ id: 'e1', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null }]
      : [],
    selection: [],
  });
}

function renderPrompt(): void {
  const data = (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as { data: unknown }).data;
  render(
    <ReactFlowProvider>
      <PromptNodeView {...baseProps} id="p1" type="prompt" data={data as never} />
    </ReactFlowProvider>,
  );
}

function promptData(): { source_ref?: string; body: string } {
  return (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as { data: never }).data;
}

beforeEach(() => {
  vi.mocked(useResourceSearch).mockReturnValue({
    data: EMPTY_SEARCH,
    loading: false,
  } as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('input image row', () => {
  it('renders wired inputs as thumbnails with a count', () => {
    seed(true);
    renderPrompt();
    const row = screen.getByTestId('prompt-input-row');
    expect(row.querySelectorAll('img')).toHaveLength(2);
    expect(row.textContent).toContain('2 inputs');
  });

  it('clicking a thumbnail toggles it as the i2i source', () => {
    seed(true);
    renderPrompt();
    const thumbs = screen.getAllByTestId('prompt-input-thumb');
    fireEvent.click(thumbs[1]);
    expect(promptData().source_ref).toBe(URL_B);
    fireEvent.click(thumbs[1]);
    expect(promptData().source_ref).toBeUndefined();
  });

  it('no wired inputs → no row', () => {
    seed(false);
    renderPrompt();
    expect(screen.queryByTestId('prompt-input-row')).toBeNull();
  });
});

describe('@ picker input tab', () => {
  // The body is a tiptap contenteditable; '@' is reported from its keydown
  // handler, not from a ChangeEvent it never produces. It notifies on the
  // next tick (so the character lands first), so callers await what they
  // actually need — the two tabs render different roots, and only the
  // Library one carries the `mention-assets-*` testids.
  function openPicker(): void {
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Prompt body' }), { key: '@' });
  }

  it('defaults to the Input tab when inputs exist; picking sets source_ref and inserts a chip', async () => {
    seed(true);
    renderPrompt();
    openPicker();
    const options = await screen.findAllByTestId('mention-input-option');
    expect(options).toHaveLength(2);
    fireEvent.mouseDown(options[1]);
    expect(promptData().source_ref).toBe(URL_B);
    // The picked image is now a chip in the document, and the body's plain
    // text projection renders it as @alias.
    expect(await screen.findByTestId('prompt-image-chip')).toHaveTextContent('Image 2');
    await waitFor(() => expect(promptData().body).toContain('@Image 2'));
  });

  it('without inputs the picker opens on Assets, and the Input tab says why it is empty', async () => {
    seed(false);
    renderPrompt();
    openPicker();
    // The tab is reachable, not disabled: a disabled tab explains nothing,
    // and "this node has no input images yet" is the answer the user needs.
    const inputTab = await screen.findByTestId('mention-tab-input');
    expect(inputTab).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('mention-tab-assets')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(inputTab);
    expect(screen.getByTestId('mention-input-empty')).toBeInTheDocument();
  });
});


describe('manual references (⑨C)', () => {
  it('upstream prompt text renders as a preview line', () => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        {
          id: 'u1', type: 'prompt', position: { x: 0, y: 0 },
          data: { body: 'moody alley at dusk', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] },
        },
        {
          id: 'p1', type: 'prompt', position: { x: 0, y: 300 },
          data: { body: '', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [], gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } },
        },
      ] as never,
      connections: [
        { id: 'e1', source: 'u1', target: 'p1', sourceHandle: null, targetHandle: null },
      ],
      selection: [],
    });
    renderPrompt();
    expect(
      screen.getByTestId('prompt-upstream-preview').textContent,
    ).toContain('moody alley at dusk');
  });

  it('manual ref chips carry a remove key that deletes them', () => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        {
          id: 'p1', type: 'prompt', position: { x: 0, y: 0 },
          data: {
            body: '', provider_slug: '', agent_id: null, run_status: 'idle',
            resource_refs: [],
            manual_refs: [{ url: '/api/v1/generated-media/9/file', kind: 'image' }],
            gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
          },
        },
      ] as never,
      connections: [],
      selection: [],
    });
    renderPrompt();
    // The manual ref shows as an input thumb with a remove key.
    expect(screen.getAllByTestId('prompt-input-thumb')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('remove-reference'));
    expect(promptData().body).toBe('');
    const refs = (useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { id: string }).id === 'p1') as {
      data: { manual_refs?: unknown[] };
    }).data.manual_refs;
    expect(refs).toEqual([]);
  });

  it('Add reference key opens the picker', () => {
    seed(false);
    renderPrompt();
    fireEvent.click(screen.getByTestId('add-reference'));
    expect(screen.getByTestId('reference-picker')).toBeInTheDocument();
  });
});
