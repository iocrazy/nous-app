// features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx
// Phase 2 Task 3: PromptNodeView gets a Library pill button (lucide Library
// icon, same CANVAS_PILL_TRIGGER styling as the other toolbar pills) that
// opens AssetPromptPicker. This only covers the open-on-click wiring — the
// pick→apply path is covered by loadPromptAsset.test.ts (pure function) and
// AssetPromptPicker.test.tsx (picker UI).

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn().mockReturnValue({
    data: { results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null },
    loading: false,
    error: null,
  }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [],
}));
// vi.mock() calls are hoisted above all imports/consts — vi.hoisted() is the
// documented escape hatch for a fixture the factory below needs to close over.
const { mockPromptAsset } = vi.hoisted(() => ({
  mockPromptAsset: {
    id: 'asset-1',
    filename: 'hero.png',
    gen_prompt: 'a cinematic hero shot',
    gen_prompt_zh: null,
    gen_prompt_negative: 'lowres',
    gen_prompt_negative_zh: null,
    updated_at: '2026-07-26T00:00:00Z',
  },
}));

vi.mock('../../../../services/resourceService', () => ({
  fetchPromptAssets: vi.fn().mockResolvedValue([mockPromptAsset]),
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));
vi.mock('../../../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
}));

const mockImportResource = vi.fn();
vi.mock('../mediaImport', () => ({
  importResourceAsCanvasMedia: (...args: unknown[]) => mockImportResource(...args),
}));

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

beforeEach(() => {
  mockImportResource.mockReset();
  mockImportResource.mockResolvedValue({ url: '/api/v1/generated-media/gm-1', kind: 'image' });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView Library button', () => {
  it('opens AssetPromptPicker on click', () => {
    setNode();
    render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={BASE_DATA} />
      </ReactFlowProvider>,
    );

    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
    fireEvent.click(screen.getByTestId('prompt-library-button'));
    expect(screen.getByTestId('asset-prompt-picker')).toBeTruthy();
  });

  // Regression for the atomic-write fix: picking a row used to call
  // patch(promptPatch) against a stale `nodes` snapshot, then immediately
  // overwrite the store with that same stale snapshot + the new media node
  // — silently discarding the prompt text/negative just written. This must
  // fail against the pre-fix two-write version (verified by stashing the
  // fix once: body stayed 'hand typed' instead of picking up the asset's
  // gen_prompt) and pass once the pick is folded into one setNodes call.
  it('applies the picked asset body/negative and wires the media node in one write', async () => {
    const handTyped = { ...BASE_DATA, body: 'hand typed' };
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      canvasId: '9',
      loadStatus: 'ready',
      nodes: [{ id: 'p1', type: 'prompt', position: { x: 400, y: 200 }, data: handTyped }],
      connections: [],
      selection: [],
    });

    render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={handTyped} />
      </ReactFlowProvider>,
    );

    fireEvent.click(screen.getByTestId('prompt-library-button'));
    await screen.findByTestId('asset-prompt-picker');
    const row = await screen.findByTestId('asset-prompt-picker-row');
    fireEvent.click(row);

    await waitFor(() => {
      const { nodes } = useCanvasCoreStore.getState();
      expect(nodes).toHaveLength(2);
    });

    const { nodes, connections } = useCanvasCoreStore.getState();
    const promptNode = nodes.find((n) => (n as unknown as { id: string }).id === 'p1') as unknown as {
      data: { body: string; negative_body?: string };
    };
    expect(promptNode.data.body).toBe('a cinematic hero shot');
    expect(promptNode.data.negative_body).toBe('lowres');

    const mediaNode = nodes.find((n) => (n as unknown as { id: string }).id !== 'p1') as unknown as {
      id: string;
    };
    expect(mediaNode).toBeTruthy();
    expect(connections).toHaveLength(1);
    expect((connections[0] as unknown as { id: string }).id).toBe(`conn-${mediaNode.id}-p1`);

    expect(mockImportResource).toHaveBeenCalledWith(mockPromptAsset.id);
    const mediaNodeData = nodes.find(
      (n) => (n as unknown as { id: string }).id !== 'p1',
    ) as unknown as { data: { items: Array<{ url: string }> } };
    expect(mediaNodeData.data.items[0].url).toBe('/api/v1/generated-media/gm-1');
  });

  // I1 (P3 final review): a video asset must produce a media node item
  // with kind 'video' — the kind returned by importResourceAsCanvasMedia
  // used to be discarded, hardcoding 'image' for every pick.
  it('carries a video mediaKind through to the inserted media node item', async () => {
    mockImportResource.mockResolvedValueOnce({ url: '/api/v1/generated-media/gm-2', kind: 'video' });
    setNode();
    render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={BASE_DATA} />
      </ReactFlowProvider>,
    );

    fireEvent.click(screen.getByTestId('prompt-library-button'));
    await screen.findByTestId('asset-prompt-picker');
    const row = await screen.findByTestId('asset-prompt-picker-row');
    fireEvent.click(row);

    await waitFor(() => {
      const { nodes } = useCanvasCoreStore.getState();
      expect(nodes).toHaveLength(2);
    });

    const { nodes } = useCanvasCoreStore.getState();
    const mediaNode = nodes.find(
      (n) => (n as unknown as { id: string }).id !== 'p1',
    ) as unknown as { data: { items: Array<{ kind: string }> } };
    expect(mediaNode.data.items[0].kind).toBe('video');
  });

  // Phase 3 Task 3: when minting the durable URL fails, the pick still
  // succeeds — it just falls back to the (visual-only) resource cover URL
  // instead of blocking the Library flow on a failed network call.
  it('falls back to the cover URL when the durable import rejects, and still inserts', async () => {
    mockImportResource.mockRejectedValueOnce(new Error('network down'));
    setNode();
    render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={BASE_DATA} />
      </ReactFlowProvider>,
    );

    fireEvent.click(screen.getByTestId('prompt-library-button'));
    await screen.findByTestId('asset-prompt-picker');
    const row = await screen.findByTestId('asset-prompt-picker-row');
    fireEvent.click(row);

    await waitFor(() => {
      const { nodes } = useCanvasCoreStore.getState();
      expect(nodes).toHaveLength(2);
    });

    const { nodes } = useCanvasCoreStore.getState();
    const mediaNode = nodes.find(
      (n) => (n as unknown as { id: string }).id !== 'p1',
    ) as unknown as { data: { items: Array<{ url: string }> } };
    expect(mediaNode.data.items[0].url).toBe(`https://api.test/cover/${mockPromptAsset.id}`);
  });
});
