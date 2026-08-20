/**
 * MediaNodeView — the Upload card. Pins: empty state opens the file
 * picker; a picked file uploads via importCanvasMedia and lands in
 * data.items through patchNode; non-media files are rejected in place.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MediaNodeView } from './MediaNodeView';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  NodeResizeControl: () => null,
  Position: { Left: 'left', Right: 'right' },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const mockImport = vi.hoisted(() => vi.fn());
vi.mock('../mediaImport', async (importOriginal) => {
  const original = await importOriginal<typeof import('../mediaImport')>();
  return { ...original, importCanvasMedia: mockImport };
});

function seedNode(items: Array<{ url: string; kind: string }> = []) {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [
      {
        id: 'm1',
        type: 'media',
        position: { x: 0, y: 0 },
        data: { title: 'Media', items },
      },
    ],
  } as never);
}

function nodeData(): { items?: Array<{ url: string }>; uploading?: number } {
  return (useCanvasCoreStore.getState().nodes[0] as {
    data: { items?: Array<{ url: string }>; uploading?: number };
  }).data;
}

function renderView(items: Array<{ url: string; kind: string }> = []) {
  seedNode(items);
  const props = {
    id: 'm1',
    data: nodeData(),
    selected: false,
  } as unknown as Parameters<typeof MediaNodeView>[0];
  return render(<MediaNodeView {...props} />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('MediaNodeView', () => {
  it('renders the empty upload invitation', () => {
    renderView();
    expect(screen.getByTestId('media-node-empty')).toBeTruthy();
  });

  it('uploads a picked file and appends the durable item', async () => {
    mockImport.mockResolvedValue({
      url: '/api/v1/generated-media/77/cover',
      kind: 'image',
      name: 'pic.png',
    });
    const { container } = renderView();

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(4)], 'pic.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(mockImport).toHaveBeenCalledWith(file, 'c1', 'm1');
      expect(nodeData().items).toEqual([
        { url: '/api/v1/generated-media/77/cover', kind: 'image', name: 'pic.png' },
      ]);
      expect(nodeData().uploading).toBe(0);
    });
  });

  it('rejects non-media files without calling the import endpoint', async () => {
    const { container } = renderView();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(4)], 'doc.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByText('Only images/videos up to 50MB');
    expect(mockImport).not.toHaveBeenCalled();
  });

  it('renders existing items as a grid', () => {
    renderView([{ url: '/api/v1/generated-media/1/cover', kind: 'image' }]);
    expect(screen.getByTestId('media-node-grid')).toBeTruthy();
    expect(screen.queryByTestId('media-node-empty')).toBeNull();
  });
});
