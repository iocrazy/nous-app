// 2026-08-20 regression pair: (1) node width must stay at the fixed default
// (width:'100%' let raw images blow the card up to natural size); (2) the
// editor auto-promotes generated images so the full tab set appears.
import { fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { OutputNodeView } from './OutputNodeView';

vi.mock('../mediaEditBridge', () => ({
  ensureResourceId: vi.fn(() => Promise.resolve('777')),
}));
import { ensureResourceId } from '../mediaEditBridge';

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
    selected: false,
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

  it('opening the editor on a generated image promotes it to a resource', async () => {
    render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    expect(ensureResourceId).toHaveBeenCalledWith('/api/v1/generated-media/9/cover');
    // resource_id lands on the node data once the promote resolves.
    await vi.waitFor(() => {
      const data = (useCanvasCoreStore.getState().nodes[0] as { data: { resource_id?: string } }).data;
      expect(data.resource_id).toBe('777');
    });
  });
});
