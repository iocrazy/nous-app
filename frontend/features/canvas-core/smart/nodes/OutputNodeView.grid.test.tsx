import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

// ----- Module mocks ---------------------------------------------------

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCanvasGrid: vi.fn(),
  };
});

const { deriveCanvasGrid } = await import('../../services/canvasService');

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  type: 'output',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
    // Individual renders pass `selected` where they drive the floating
  // toolbar: since fluency T5 the bar is mounted only while the card is
  // pinned (selected) or hovered.
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function seedImageOutput() {
  const fullData = {
    kind: 'image',
    preview_text: '',
    preview_url: '/api/v1/generated-media/5/cover',
    crop_region: null,
  };
  useCanvasCoreStore.setState({
    nodes: [
      {
        id: 'o1',
        type: 'output',
        data: fullData,
        position: { x: 100, y: 50 },
      },
    ],
  });
  return fullData;
}

function fakeTile(row: number, col: number) {
  const id = `9${row}${col}`;
  return { id, url: `/api/v1/generated-media/${id}/cover`, kind: 'image', row, col };
}

// ============================================================
// Split button visibility
// ============================================================

describe('OutputNodeView — Split button', () => {
  it('shows for any image output on a canvas — no resource_id needed', () => {
    const fullData = seedImageOutput();
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByRole('button', { name: 'Split' })).toBeInTheDocument();
  });
});

// ============================================================
// Commit derives tiles and spawns nodes
// ============================================================

describe('OutputNodeView — grid commit spawns tile nodes', () => {
  it('derives from the shown image and adds one output node per tile', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      fakeTile(0, 0),
      fakeTile(0, 1),
      fakeTile(1, 0),
      fakeTile(1, 1),
    ]);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Split' }));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(deriveCanvasGrid).toHaveBeenCalledTimes(1));
    expect(deriveCanvasGrid).toHaveBeenCalledWith(
      '4242',
      '/api/v1/generated-media/5/cover',
      { xs: [0.5], ys: [0.5] },
      { nodeId: 'o1' },
    );
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(5));
    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      type: string;
      position: { x: number; y: number };
      data: Record<string, unknown>;
    }>;
    expect(nodes[0].id).toBe('o1');
    expect(nodes[0].data.preview_url).toBe('/api/v1/generated-media/5/cover');
    const tiles = nodes.slice(1);
    expect(tiles.every((node) => node.type === 'output')).toBe(true);
    expect(tiles.map((node) => node.data.preview_url)).toEqual([
      '/api/v1/generated-media/900/cover',
      '/api/v1/generated-media/901/cover',
      '/api/v1/generated-media/910/cover',
      '/api/v1/generated-media/911/cover',
    ]);
    expect(tiles[0].position.y).toBe(tiles[1].position.y);
    expect(tiles[1].position.x).toBeGreaterThan(tiles[0].position.x);
    expect(tiles[2].position.y).toBeGreaterThan(tiles[0].position.y);
    expect(tiles[2].position.x).toBe(tiles[0].position.x);
    expect(tiles[0].position.x).toBeGreaterThan(nodes[0].position.x);
    await waitFor(() =>
      expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument(),
    );
  });

  it('shows an error banner + keeps the modal open when the derive rejects', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('at least one split line is required'),
    );
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Split' }));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(deriveCanvasGrid).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('grid-commit-error');
    expect(banner.textContent).toMatch(/split line/);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});

describe('generation placeholders (P0-3)', () => {
  it('renders shimmer cells while gen_pending and a failed chip after settle', () => {
    const data = {
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: null,
      crop_region: null,
      images: [{ url: '/gm/1/cover', kind: 'image' }],
      gen_pending: 2,
      gen_failed: 1,
      gen_slot: { node_id: 'p1', index: 0 },
    };
    useCanvasCoreStore.setState({
      nodes: [{ id: 'out1', type: 'output', position: { x: 0, y: 0 }, data }],
      connections: [],
    });
    const { container } = render(
      <Wrap>
        <OutputNodeView {...baseProps} id="out1" data={data} />
      </Wrap>,
    );
    expect(screen.getAllByTestId('output-pending-cell')).toHaveLength(2);
    expect(container.querySelectorAll('img')).toHaveLength(1);
    expect(screen.getByTestId('output-failed-chip').textContent).toContain('1 item failed');
  });

  it('shows the grid even before the first image lands', () => {
    const data = {
      kind: 'image',
      resource_id: null,
      preview_text: '',
      preview_url: null,
      crop_region: null,
      images: [],
      gen_pending: 4,
      gen_slot: { node_id: 'p1', index: 0 },
    };
    useCanvasCoreStore.setState({
      nodes: [{ id: 'out1', type: 'output', position: { x: 0, y: 0 }, data }],
      connections: [],
    });
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="out1" data={data} />
      </Wrap>,
    );
    expect(screen.getAllByTestId('output-pending-cell')).toHaveLength(4);
  });
});
