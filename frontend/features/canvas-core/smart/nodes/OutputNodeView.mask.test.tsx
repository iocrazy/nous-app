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
    deriveMaskCutout: vi.fn(),
  };
});

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

const { deriveMaskCutout } = await import('../../services/canvasService');
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
  (deriveMaskCutout as ReturnType<typeof vi.fn>).mockReset();
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
  fireEvent.click(screen.getByTestId('mask-cutout-open'));
  const surface = screen.getByTestId('mask-brush-tool');
  fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.pointerUp(surface, { pointerId: 1 });
  fireEvent.click(screen.getByTestId('mask-editor-commit'));
}

describe('OutputNodeView — Mask button', () => {
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
    expect(screen.getByTestId('mask-cutout-open')).toBeInTheDocument();
    unmount();

    const without = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={without} />
      </Wrap>,
    );
    expect(screen.queryByTestId('mask-cutout-open')).not.toBeInTheDocument();
  });
});

describe('OutputNodeView — mask commit derives a cutout node', () => {
  it('exports the mask, calls deriveMaskCutout and spawns one node', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveMaskCutout as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: 'cutout-1',
      filename: 'cutout-orig.png',
      file_path: 'teams/s/derived/cutout-1/v1/cutout-orig.png',
      mime_type: 'image/png',
      file_size_bytes: 999,
    });

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(deriveMaskCutout).toHaveBeenCalledTimes(1);
    });
    expect(deriveMaskCutout).toHaveBeenCalledWith('source-123', 'MASKB64');
    // Export used the fallback raster size (jsdom images never load).
    expect(strokesToMaskPngBase64).toHaveBeenCalledWith(
      expect.any(Array),
      1024,
      1024,
    );

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
    expect(nodes[0].data.resource_id).toBe('source-123');
    const cutout = nodes[1];
    expect(cutout.type).toBe('output');
    expect(cutout.data.resource_id).toBe('cutout-1');
    expect(cutout.data.preview_url).toBe(
      'https://example.test/api/v1/resources/cutout-1/file?token=fake-token',
    );
    expect(cutout.position.x).toBeGreaterThan(nodes[0].position.x);
    expect(cutout.position.y).toBe(nodes[0].position.y);

    await waitFor(() => {
      expect(
        screen.queryByTestId('mask-editor-modal'),
      ).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when the derive rejects', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveMaskCutout as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('HTTP 400: mask is empty'),
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    await waitFor(() => {
      expect(deriveMaskCutout).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId('mask-editor-modal')).toBeInTheDocument();
    const banner = await screen.findByTestId('mask-commit-error');
    expect(banner.textContent).toMatch(/mask is empty/i);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('surfaces a local export failure without calling the API', async () => {
    const fullData = seedImageOutput('source-123');
    (strokesToMaskPngBase64 as ReturnType<typeof vi.fn>).mockImplementation(
      () => {
        throw new Error('2D canvas context unavailable');
      },
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    paintAndCommit();

    const banner = await screen.findByTestId('mask-commit-error');
    expect(banner.textContent).toMatch(/canvas context/i);
    expect(deriveMaskCutout).not.toHaveBeenCalled();
  });
});
