// 2026-08-20 regression pair: (1) node width must stay at the fixed default
// (width:'100%' let raw images blow the card up to natural size);
// (2) opening the editor must NOT promote — editors derive from the image url (2026-09-10)
import { fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { OutputNodeView } from './OutputNodeView';

vi.mock('../mediaEditBridge', () => ({
  ensureResourceId: vi.fn(() => Promise.resolve('777')),
  // The node reads this too (P4 Task 6): "As Asset" recovers the
  // `generated_media` id from a durable url when the image ref carries none.
  // A partial mock of a module the component imports is a missing-export
  // crash, not a missing feature — so the stub has to grow with the module.
  genIdFromDurableUrl: vi.fn(() => null),
}));
import { ensureResourceId } from '../mediaEditBridge';
vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCanvasCrop: vi.fn(async () => ({
      id: '901',
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      row: null,
      col: null,
    })),
  };
});
import { deriveCanvasCrop } from '../../services/canvasService';

function seed() {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [
      {
        id: 'o1',
        type: 'output',
        position: { x: 0, y: 0 },
        data: {
          kind: 'image',
          preview_url: '/api/v1/generated-media/9/cover',
          images: [{ url: '/api/v1/generated-media/9/cover', kind: 'image' }],
        },
      },
    ] as never,
    connections: [] as never,
  });
}

const props = () =>
  ({
    id: 'o1',
    data: (useCanvasCoreStore.getState().nodes[0] as { data: unknown }).data,
      // Selected: since fluency T5 the floating toolbar is mounted only while
  // the card is pinned (selected) or hovered, and these cases drive it.
  selected: true,
  }) as unknown as Parameters<typeof OutputNodeView>[0];

describe('OutputNodeView width + promote', () => {
  beforeEach(() => {
    seed();
    vi.clearAllMocks();
  });

  it('card keeps the fixed default width when the node was never resized', () => {
    render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
    expect(screen.getByTestId('smart-output-node').style.width).toBe(
      `${SMART_NODE_DEFAULT_WIDTH.output}px`,
    );
  });

  it('opening the editor never promotes the image into the library', async () => {
    render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-split')).toBeInTheDocument();
    await Promise.resolve();
    expect(ensureResourceId).not.toHaveBeenCalled();
    const data = (useCanvasCoreStore.getState().nodes[0] as { data: { resource_id?: string } }).data;
    expect(data.resource_id).toBeUndefined();
  });
});

it('a stored node_w wins over the default (user dragged the handle)', () => {
  const node = useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> };
  node.data = { ...node.data, node_w: 640 };
  render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
  expect(screen.getByTestId('smart-output-node').style.width).toBe('640px');
});

it('grid images: dblclick edits THAT image; hover delete removes it', async () => {
  const node = useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> };
  node.data = {
    ...node.data,
    images: [
      { url: '/api/v1/generated-media/9/cover', kind: 'image' },
      { url: '/api/v1/generated-media/10/file', kind: 'image', name: 'mask.png' },
    ],
  };
  render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
  const imgs = screen.getAllByAltText(/Generated|mask/i);
  fireEvent.doubleClick(imgs[1]);
  expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
  // delete the second item
  fireEvent.click(screen.getByTestId('output-image-delete-1'));
  const data = (useCanvasCoreStore.getState().nodes[0] as { data: { images: unknown[] } }).data;
  expect(data.images).toHaveLength(1);
});

it('Copy to canvas drops the image as an independent media node', () => {
  render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Copy to canvas' }));
  const nodes = useCanvasCoreStore.getState().nodes as Array<{ type: string; data: { items?: unknown[] } }>;
  const media = nodes.find((n) => n.type === 'media');
  expect(media).toBeTruthy();
  expect(media!.data.items).toHaveLength(1);
});

it('grid dblclick then crop derives from THAT image and swaps only it', async () => {
  const node = useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> };
  node.data = {
    ...node.data,
    images: [
      { url: '/api/v1/generated-media/9/cover', kind: 'image' },
      { url: '/api/v1/generated-media/10/cover', kind: 'image', name: 'mask.png' },
    ],
  };
  render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
  fireEvent.doubleClick(screen.getAllByAltText(/Generated|mask/i)[1]);
  fireEvent.click(screen.getByTestId('editor-tab-crop'));
  fireEvent.click(screen.getByTestId('editor-apply'));

  await vi.waitFor(() =>
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      'c1',
      '/api/v1/generated-media/10/cover',
      expect.anything(),
      { nodeId: 'o1' },
    ),
  );
  await vi.waitFor(() => {
    const data = (useCanvasCoreStore.getState().nodes[0] as {
      data: { preview_url: string; images: Array<{ url: string }> };
    }).data;
    expect(data.images.map((i) => i.url)).toEqual([
      '/api/v1/generated-media/9/cover',
      '/api/v1/generated-media/901/cover',
    ]);
    expect(data.preview_url).toBe('/api/v1/generated-media/9/cover');
  });
});
