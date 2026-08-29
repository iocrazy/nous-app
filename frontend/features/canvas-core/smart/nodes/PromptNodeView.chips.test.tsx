/**
 * PromptNodeView — picking an input image leaves a CHIP, not bare text.
 *
 * The @-picker's Input tab already existed (IC ⑤) but inserted the literal
 * string "@Image 1". A chip carries the thumbnail, deletes as one unit, and —
 * because it lives in the document — is what makes the reference visible and
 * removable rather than a string the user can silently break by editing.
 *
 * The reference itself still travels as `source_ref`, unchanged: there is no
 * multi-reference channel on the backend, and inventing one in the UI would
 * promise something generation cannot deliver.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn(),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { PromptNodeView } from './PromptNodeView';
import { useResourceSearch } from '../../../../hooks/useResourceSearch';
import type { ResourceSearchResponse } from '../../../../types';

const EMPTY: ResourceSearchResponse = {
  results: [],
  counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

const UP_URL = '/api/v1/generated-media/55/cover';

const BASE_PROPS = {
  type: 'prompt',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 280,
  height: 160,
  zIndex: 0,
} as const;

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

function seedWithUpstream() {
  const data = {
    body: '',
    provider_slug: '',
    agent_id: null,
    run_status: 'idle',
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
    resource_refs: [],
  };
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T00:00:00+00:00',
    nodes: [
      { id: 'p1', type: 'prompt', data, position: { x: 320, y: 0 } },
      {
        id: 'm1',
        type: 'media',
        position: { x: 0, y: 0 },
        data: { title: 'Media', items: [{ url: UP_URL, kind: 'image' }] },
      },
    ] as unknown as CanvasNode[],
    connections: [
      { id: 'e1', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null },
    ],
  });
  return data;
}

function renderNode(data: Record<string, unknown>) {
  return render(
    <Wrap>
      <PromptNodeView {...BASE_PROPS} id="p1" type="prompt" data={data} />
    </Wrap>,
  );
}

beforeEach(() => {
  vi.mocked(useResourceSearch).mockReturnValue({ data: EMPTY, loading: false, error: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView — input image chips', () => {
  it('picking an input image inserts a chip carrying its thumbnail', async () => {
    const data = seedWithUpstream();
    renderNode(data);

    fireEvent.keyDown(screen.getByTestId('prompt-body-editor'), { key: '@' });
    fireEvent.mouseDown(await screen.findByTestId('mention-input-option'));

    const chip = await screen.findByTestId('prompt-image-chip');
    expect(chip).toHaveTextContent('Image 1');
    expect(chip.querySelector('img')?.getAttribute('src')).toContain('55');
  });

  it('still sets the image as the i2i source', async () => {
    const data = seedWithUpstream();
    renderNode(data);

    fireEvent.keyDown(screen.getByTestId('prompt-body-editor'), { key: '@' });
    fireEvent.mouseDown(await screen.findByTestId('mention-input-option'));

    await waitFor(() => {
      const node = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as { id: string }).id === 'p1') as {
        data: { source_ref?: string };
      };
      expect(node.data.source_ref).toBe(UP_URL);
    });
  });

  it('writes the chip into the body text as @alias', async () => {
    const data = seedWithUpstream();
    renderNode(data);

    fireEvent.keyDown(screen.getByTestId('prompt-body-editor'), { key: '@' });
    fireEvent.mouseDown(await screen.findByTestId('mention-input-option'));

    await waitFor(() => {
      const node = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as { id: string }).id === 'p1') as {
        data: { body?: string };
      };
      expect(node.data.body).toContain('@Image 1');
    });
  });

  it('restores chips from node data after a reload', async () => {
    const data = {
      ...seedWithUpstream(),
      image_refs: [{ url: UP_URL, alias: 'Image 1', kind: 'image' }],
    };
    renderNode(data);
    const chip = await screen.findByTestId('prompt-image-chip');
    expect(chip).toHaveTextContent('Image 1');
  });
});
