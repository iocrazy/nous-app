// Shared node-deletion helper (IC node-delete mini-x + keyboard Delete both
// route here): removes nodes, strips dangling connections, frees group
// children back to absolute coords.
import { beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { deleteNodesById } from './deleteNodes';

describe('deleteNodesById', () => {
  beforeEach(() => {
    useCanvasCoreStore.setState({
      nodes: [
        { id: 'a', type: 'prompt', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'output', position: { x: 10, y: 0 }, data: {} },
        {
          id: 'child',
          type: 'media',
          position: { x: 5, y: 5 },
          parentId: 'a',
          data: {},
        },
      ] as never,
      connections: [
        { id: 'e1', source: 'a', target: 'b' },
        { id: 'e2', source: 'b', target: 'child' },
      ] as never,
      selection: ['a', 'b'],
    });
  });

  it('removes the node, its connections, and frees group children', () => {
    deleteNodesById(['a']);
    const s = useCanvasCoreStore.getState();
    const ids = s.nodes.map((n) => (n as { id: string }).id);
    expect(ids).not.toContain('a');
    expect(ids).toContain('child');
    const child = s.nodes.find((n) => (n as { id: string }).id === 'child') as {
      parentId?: string;
    };
    expect(child.parentId).toBeUndefined();
    expect(s.connections.map((c) => (c as { id: string }).id)).toEqual(['e2']);
  });

  it('clears deleted ids from the selection', () => {
    deleteNodesById(['b']);
    expect(useCanvasCoreStore.getState().selection).toEqual(['a']);
  });
});
