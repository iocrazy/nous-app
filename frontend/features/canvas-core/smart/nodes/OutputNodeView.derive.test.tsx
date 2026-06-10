import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

// ----- Module mocks ---------------------------------------------------

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCrop: vi.fn(),
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

const { deriveCrop } = await import('../../services/canvasService');

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

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
  (deriveCrop as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
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
    nodes: [{ id: 'o1', type: 'output', data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

// ============================================================
// Commit calls deriveCrop and patches the node
// ============================================================

describe('OutputNodeView — Commit derives and swaps the resource', () => {
  it('calls deriveCrop with the source resource_id and patches resource_id + preview_url', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveCrop as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: '9999000000000001',
      filename: 'crop-orig.png',
      file_path: 'teams/s/derived/9999000000000001/v1/crop-orig.png',
      mime_type: 'image/png',
      file_size_bytes: 1234,
    });

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    fireEvent.click(screen.getByTestId('crop-editor-commit'));

    await waitFor(() => {
      expect(deriveCrop).toHaveBeenCalledTimes(1);
    });
    expect(deriveCrop).toHaveBeenCalledWith(
      'source-123',
      expect.objectContaining({ x: 0, y: 0, width: 1, height: 1 }),
    );

    await waitFor(() => {
      const node = useCanvasCoreStore.getState().nodes[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(node.data.resource_id).toBe('9999000000000001');
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.preview_url).toBe(
      'https://example.test/api/v1/resources/9999000000000001/file?token=fake-token',
    );
    // After a successful derive, the new resource IS the cropped image,
    // so the in-node crop should be cleared.
    expect(node.data.crop_region).toBeNull();
    // Modal closed.
    await waitFor(() => {
      expect(screen.queryByTestId('crop-editor-modal')).not.toBeInTheDocument();
    });
  });

  it('shows an error banner + keeps the modal open when deriveCrop rejects', async () => {
    const fullData = seedImageOutput('source-123');
    (deriveCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('HTTP 400: invalid region'),
    );

    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    fireEvent.click(screen.getByTestId('crop-editor-commit'));

    await waitFor(() => {
      expect(deriveCrop).toHaveBeenCalledTimes(1);
    });
    // Modal stays open.
    expect(screen.getByTestId('crop-editor-modal')).toBeInTheDocument();
    // Error banner shows the backend message.
    const banner = await screen.findByTestId('crop-commit-error');
    expect(banner.textContent).toMatch(/invalid region/i);
    // resource_id unchanged.
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.resource_id).toBe('source-123');
  });

  it('falls back to crop_region-only patch when resource_id is null', async () => {
    const fullData = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    fireEvent.click(screen.getByTestId('crop-editor-commit'));

    // deriveCrop must NOT be called when there's no source resource.
    expect(deriveCrop).not.toHaveBeenCalled();
    await waitFor(() => {
      const node = useCanvasCoreStore.getState().nodes[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(node.data.crop_region).toEqual({
        x: 0,
        y: 0,
        width: 1,
        height: 1,
      });
    });
  });
});
