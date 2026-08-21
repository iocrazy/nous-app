import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { createMediaNodeFromFiles } from '../dropCreate';
import { OutputNodeView } from './OutputNodeView';

// ----- Module mocks ---------------------------------------------------

vi.mock('../dropCreate', () => ({
  createMediaNodeFromFiles: vi.fn(() => Promise.resolve('mask-node-1')),
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
  (createMediaNodeFromFiles as ReturnType<typeof vi.fn>)
    .mockReset()
    .mockResolvedValue('mask-node-1');
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
});

describe('OutputNodeView — mask commit creates a MASK NODE (IC 生成遮罩节点)', () => {
  it('exports the black/white mask and drops it beside the source as a media node', async () => {
    const fullData = seedImageOutput('source-123');
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(createMediaNodeFromFiles).toHaveBeenCalledTimes(1);
    });
    const [files, at] = (createMediaNodeFromFiles as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(files[0].name).toBe('mask.png');
    expect(files[0].type).toBe('image/png');
    expect(at.x).toBeGreaterThan(0);
    // Export used the fallback raster size (jsdom images never load).
    expect(strokesToMaskPngBase64).toHaveBeenCalledWith(
      expect.any(Array),
      1024,
      1024,
    );
    await waitFor(() => {
      expect(
        screen.queryByTestId('unified-image-editor'),
      ).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when the node drop fails', async () => {
    const fullData = seedImageOutput('source-123');
    (createMediaNodeFromFiles as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('upload exploded'),
    );
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(createMediaNodeFromFiles).toHaveBeenCalled();
    });
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('mask-commit-error');
    expect(banner.textContent).toMatch(/upload exploded/i);
  });
});

