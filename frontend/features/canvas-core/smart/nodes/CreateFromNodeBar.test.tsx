import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { CreateFromNodeBar } from './CreateFromNodeBar';

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('CreateFromNodeBar', () => {
  it('clicking Create spawns a wired prompt below the node', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        {
          id: 'm1',
          type: 'media',
          position: { x: 0, y: 0 },
          data: { title: 'Media', items: [] },
        } as unknown as CanvasNode,
      ],
      connections: [],
      selection: [],
    });
    render(<CreateFromNodeBar nodeId="m1" pinned />);
    fireEvent.click(screen.getByRole('button', { name: 'Create from this' }));
    const s = useCanvasCoreStore.getState();
    expect(s.nodes.some((n) => (n as { type: string }).type === 'prompt')).toBe(true);
    expect(s.connections).toHaveLength(1);
  });

  it('read-only renders nothing', () => {
    render(<CreateFromNodeBar nodeId="m1" readOnly />);
    expect(screen.queryByTestId('create-from-node-bar')).toBeNull();
  });
});
