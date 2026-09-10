import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { importCanvasMedia } from '../mediaImport';
import { OutputNodeView } from './OutputNodeView';

// ----- Module mocks ---------------------------------------------------

vi.mock('../mediaImport', () => ({
  importCanvasMedia: vi.fn(() =>
    Promise.resolve({ url: '/api/v1/generated-media/77/file', kind: 'image' }),
  ),
}));

vi.mock('../../editor/maskExport', () => ({
  strokesToMaskPngBase64: vi.fn(() => 'MASKB64'),
}));

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

const { strokesToMaskPngBase64 } = await import('../../editor/maskExport');

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
  (importCanvasMedia as ReturnType<typeof vi.fn>)
    .mockReset()
    .mockResolvedValue({ url: '/api/v1/generated-media/77/file', kind: 'image' });
  (strokesToMaskPngBase64 as ReturnType<typeof vi.fn>)
    .mockReset()
    .mockReturnValue('MASKB64');
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

function paintAndCommit() {
  fireEvent.click(screen.getByRole('button', { name: 'Mask' }));
  const surface = screen.getByTestId('mask-brush-tool');
  fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.pointerUp(surface, { pointerId: 1 });
  fireEvent.click(screen.getByTestId('editor-apply'));
}

describe('OutputNodeView — Mask button', () => {
  it('shows for any image output — no resource_id needed (mask is client-side)', () => {
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
    expect(screen.getByRole('button', { name: 'Mask' })).toBeInTheDocument();
    unmount();

    const without = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={without} />
      </Wrap>,
    );
    expect(screen.getByRole('button', { name: 'Mask' })).toBeInTheDocument();
  });

  it('opens the mask editor without a resource_id', () => {
    const without = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={without} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Mask' }));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    expect(screen.getByTestId('mask-brush-tool')).toBeInTheDocument();
  });
});

describe('OutputNodeView — mask commit creates a MASK NODE (IC 生成遮罩节点)', () => {
  it('appends the black/white mask INTO this node beside the original', async () => {
    const fullData = seedImageOutput('source-123');
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(importCanvasMedia).toHaveBeenCalledTimes(1);
      // Classified at the upload, not later: a mask that reaches the backend
      // unclassified is indistinguishable from a user's own file and shows up
      // in the Generated inbox as something to triage.
      expect(
        (importCanvasMedia as ReturnType<typeof vi.fn>).mock.calls[0][3],
      ).toBe('mask');
    });
    const [file] = (importCanvasMedia as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(file.name).toBe('mask.png');
    await waitFor(() => {
      const node = useCanvasCoreStore.getState().nodes[0] as {
        data: { images?: Array<{ url: string; name?: string }> };
      };
      const imgs = node.data.images ?? [];
      expect(imgs[imgs.length - 1]?.name).toBe('mask.png');
    });
    // still ONE node — the mask did not spawn a separate card.
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('shows an error banner + keeps the modal open when the import fails', async () => {
    const fullData = seedImageOutput('source-123');
    (importCanvasMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('upload exploded'),
    );
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(importCanvasMedia).toHaveBeenCalled();
    });
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('mask-commit-error');
    expect(banner.textContent).toMatch(/upload exploded/i);
  });
});

