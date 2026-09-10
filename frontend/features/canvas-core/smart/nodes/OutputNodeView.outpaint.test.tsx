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
    deriveCanvasOutpaint: vi.fn(),
  };
});

const { deriveCanvasOutpaint } = await import('../../services/canvasService');

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
  (deriveCanvasOutpaint as ReturnType<typeof vi.fn>).mockReset();
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
    // Selected: since fluency T5 the floating toolbar is mounted only while
  // the card is pinned (selected) or hovered, and these cases drive it.
  selected: true,
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
    preview_text: 'a windswept meadow',
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

function openDragAndCommit() {
  fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
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
  fireEvent.click(screen.getByTestId('editor-apply'));
}

describe('OutputNodeView — Expand button', () => {
  it('shows for any image output on a canvas — no resource_id needed', () => {
    const fullData = seedImageOutput();
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
  });
});

describe('OutputNodeView — outpaint commit spawns the extended node', () => {
  it('calls deriveCanvasOutpaint with the source url + padding + prompt and adds one node', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasOutpaint as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: '901',
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      row: null,
      col: null,
    });

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    openDragAndCommit();

    await waitFor(() => {
      expect(deriveCanvasOutpaint).toHaveBeenCalledTimes(1);
    });
    const [canvasId, sourceUrl, padding, opts] = (
      deriveCanvasOutpaint as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(canvasId).toBe('4242');
    expect(sourceUrl).toBe('/api/v1/generated-media/5/cover');
    expect(padding.right).toBeCloseTo(0.1);
    expect(padding.left).toBe(0);
    // Prompt auto-filled from the node's preview_text.
    expect(opts).toEqual({ nodeId: 'o1', prompt: 'a windswept meadow' });

    await waitFor(() => {
      // IC 扩图联动: source + extended output + the pre-seeded prompt.
      expect(useCanvasCoreStore.getState().nodes).toHaveLength(3);
      const prompt = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as { type?: string }).type === 'prompt') as
        | { data?: { body?: string } }
        | undefined;
      expect(prompt?.data?.body).toMatch(/white area/i);
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
    expect(extended.data.preview_url).toBe('/api/v1/generated-media/901/cover');
    expect(extended.position.x).toBeGreaterThan(nodes[0].position.x);
    expect(extended.position.y).toBe(nodes[0].position.y);

    await waitFor(() => {
      expect(
        screen.queryByTestId('unified-image-editor'),
      ).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when the derive rejects', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasOutpaint as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('HTTP 400: at least one side must have padding > 0'),
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    openDragAndCommit();

    await waitFor(() => {
      expect(deriveCanvasOutpaint).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('outpaint-commit-error');
    expect(banner.textContent).toMatch(/padding/i);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
