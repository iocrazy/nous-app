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
    deriveGrid: vi.fn(),
  };
});

vi.mock('../../../../services/resourceService', () => ({
  getResourceFileUrl: (id: string, token?: string) =>
    `https://example.test/api/v1/resources/${id}/file${token ? `?token=${token}` : ''}`,
}));

vi.mock('../../../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { access_token: 'fake-token' } },
      }),
    },
  }),
}));

const { deriveGrid } = await import('../../services/canvasService');

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  (deriveGrid as ReturnType<typeof vi.fn>).mockReset();
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
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function seedImageOutput(resourceId: string | null) {
  const fullData = {
    kind: 'image',
    resource_id: resourceId,
    preview_text: '',
    preview_url: 'https://example.test/source.png',
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
  return {
    row,
    col,
    resource: {
      id: `tile-${row}${col}`,
      filename: `grid-r${row + 1}c${col + 1}-orig.png`,
      file_path: `teams/s/derived/tile-${row}${col}/v1/x.png`,
      mime_type: 'image/png',
      file_size_bytes: 100,
    },
  };
}

// ============================================================
// Split button visibility
// ============================================================

describe('OutputNodeView — Split button', () => {
  it('shows the Split button for a persisted image output', () => {
    const fullData = seedImageOutput('source-123');
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByTestId('grid-split-open')).toBeInTheDocument();
  });

  it('hides the Split button when there is no resource_id', () => {
    const fullData = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.queryByTestId('grid-split-open')).not.toBeInTheDocument();
  });
});

// ============================================================
// Commit derives tiles and spawns nodes
// ============================================================

describe('OutputNodeView — grid commit spawns tile nodes', () => {
  it('calls deriveGrid and adds one image output node per tile', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveGrid as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      rows: 2,
      cols: 2,
      tiles: [fakeTile(0, 0), fakeTile(0, 1), fakeTile(1, 0), fakeTile(1, 1)],
    });

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByTestId('grid-split-open'));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('grid-editor-commit'));

    await waitFor(() => {
      expect(deriveGrid).toHaveBeenCalledTimes(1);
    });
    expect(deriveGrid).toHaveBeenCalledWith('source-123', {
      xs: [0.5],
      ys: [0.5],
    });

    await waitFor(() => {
      expect(useCanvasCoreStore.getState().nodes).toHaveLength(5);
    });
    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      type: string;
      position: { x: number; y: number };
      data: Record<string, unknown>;
    }>;
    // Source node untouched.
    expect(nodes[0].id).toBe('o1');
    expect(nodes[0].data.resource_id).toBe('source-123');

    const tiles = nodes.slice(1);
    expect(tiles.every((node) => node.type === 'output')).toBe(true);
    expect(tiles.map((node) => node.data.resource_id)).toEqual([
      'tile-00',
      'tile-01',
      'tile-10',
      'tile-11',
    ]);
    expect(tiles[0].data.preview_url).toBe(
      'https://example.test/api/v1/resources/tile-00/file?token=fake-token',
    );
    // Same row shares y; second column sits right of the first.
    expect(tiles[0].position.y).toBe(tiles[1].position.y);
    expect(tiles[1].position.x).toBeGreaterThan(tiles[0].position.x);
    // Second row sits below the first, same x as its column.
    expect(tiles[2].position.y).toBeGreaterThan(tiles[0].position.y);
    expect(tiles[2].position.x).toBe(tiles[0].position.x);
    // All tiles start right of the source node.
    expect(tiles[0].position.x).toBeGreaterThan(nodes[0].position.x);

    // Modal closed.
    await waitFor(() => {
      expect(
        screen.queryByTestId('grid-editor-modal'),
      ).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when deriveGrid rejects', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveGrid as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('HTTP 400: at least one split line is required'),
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByTestId('grid-split-open'));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('grid-editor-commit'));

    await waitFor(() => {
      expect(deriveGrid).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId('grid-editor-modal')).toBeInTheDocument();
    const banner = await screen.findByTestId('grid-commit-error');
    expect(banner.textContent).toMatch(/split line/i);
    // No tile nodes were added.
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
