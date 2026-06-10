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
    deriveOutpaint: vi.fn(),
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

const { deriveOutpaint } = await import('../../services/canvasService');

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;
const ORIGINAL_SET_CAPTURE = HTMLElement.prototype.setPointerCapture;

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  HTMLElement.prototype.getBoundingClientRect = function fakeRect() {
    return {
      x: 0,
      y: 0,
      width: 1000,
      height: 500,
      top: 0,
      left: 0,
      bottom: 500,
      right: 1000,
      toJSON: () => ({}),
    } as DOMRect;
  };
  HTMLElement.prototype.setPointerCapture = () => {};
  (deriveOutpaint as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
  HTMLElement.prototype.setPointerCapture = ORIGINAL_SET_CAPTURE;
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
    preview_text: 'a windswept meadow',
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

function openDragAndCommit() {
  fireEvent.click(screen.getByTestId('outpaint-open'));
  const handle = screen.getByTestId('outpaint-handle-right');
  fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500, clientY: 250 });
  window.dispatchEvent(
    new PointerEvent('pointermove', {
      bubbles: true,
      pointerId: 1,
      clientX: 600,
      clientY: 250,
    }),
  );
  fireEvent.pointerUp(handle, { pointerId: 1 });
  fireEvent.click(screen.getByTestId('outpaint-editor-commit'));
}

describe('OutputNodeView — Expand button', () => {
  it('shows for a persisted image output, hides without resource_id', () => {
    const withResource = seedImageOutput('source-123');
    const { unmount } = render(
      <Wrap>
        <OutputNodeView
          {...baseProps}
          id="o1"
          type="output"
          data={withResource}
        />
      </Wrap>,
    );
    expect(screen.getByTestId('outpaint-open')).toBeInTheDocument();
    unmount();

    const without = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={without} />
      </Wrap>,
    );
    expect(screen.queryByTestId('outpaint-open')).not.toBeInTheDocument();
  });
});

describe('OutputNodeView — outpaint commit spawns the extended node', () => {
  it('calls deriveOutpaint with padding + prompt and adds one node', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveOutpaint as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: 'extended-1',
      filename: 'outpaint-orig.png',
      file_path: 'teams/s/derived/extended-1/v1/outpaint-orig.png',
      mime_type: 'image/png',
      file_size_bytes: 999,
    });

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    openDragAndCommit();

    await waitFor(() => {
      expect(deriveOutpaint).toHaveBeenCalledTimes(1);
    });
    const [sourceId, padding, opts] = (
      deriveOutpaint as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(sourceId).toBe('source-123');
    expect(padding.right).toBeCloseTo(0.1);
    expect(padding.left).toBe(0);
    // Prompt auto-filled from the node's preview_text.
    expect(opts).toEqual({ prompt: 'a windswept meadow' });

    await waitFor(() => {
      expect(useCanvasCoreStore.getState().nodes).toHaveLength(2);
    });
    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      type: string;
      position: { x: number; y: number };
      data: Record<string, unknown>;
    }>;
    expect(nodes[0].id).toBe('o1');
    const extended = nodes[1];
    expect(extended.type).toBe('output');
    expect(extended.data.resource_id).toBe('extended-1');
    expect(extended.data.preview_url).toBe(
      'https://example.test/api/v1/resources/extended-1/file?token=fake-token',
    );
    expect(extended.position.x).toBeGreaterThan(nodes[0].position.x);
    expect(extended.position.y).toBe(nodes[0].position.y);

    await waitFor(() => {
      expect(
        screen.queryByTestId('outpaint-editor-modal'),
      ).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when the derive rejects', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveOutpaint as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('HTTP 400: at least one side must have padding > 0'),
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    openDragAndCommit();

    await waitFor(() => {
      expect(deriveOutpaint).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId('outpaint-editor-modal')).toBeInTheDocument();
    const banner = await screen.findByTestId('outpaint-commit-error');
    expect(banner.textContent).toMatch(/padding/i);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
