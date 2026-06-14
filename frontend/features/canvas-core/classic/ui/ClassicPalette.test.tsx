import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { CLASSIC_NODE_DEFINITIONS } from '../registry';
import { ClassicPalette } from './ClassicPalette';

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('ClassicPalette — surface', () => {
  it('exposes an accessible toolbar name', () => {
    render(<ClassicPalette />);
    expect(
      screen.getByRole('toolbar', { name: /classic canvas palette/i }),
    ).toBeInTheDocument();
  });

  it('renders one add button per classic node definition', () => {
    render(<ClassicPalette />);
    for (const def of CLASSIC_NODE_DEFINITIONS) {
      expect(
        screen.getByRole('button', {
          name: new RegExp(`^add ${def.label}$`, 'i'),
        }),
      ).toBeInTheDocument();
    }
  });
});

describe('ClassicPalette — add node', () => {
  it('clicking an add button appends a node of that type with id/position/data.label + selects it', () => {
    render(<ClassicPalette />);
    fireEvent.click(screen.getByRole('button', { name: /add image gen/i }));

    const state = useCanvasCoreStore.getState();
    expect(state.nodes).toHaveLength(1);
    const node = state.nodes[0] as Record<string, unknown>;
    expect(node.type).toBe('image_gen');
    expect(typeof node.id).toBe('string');
    expect((node.id as string).length).toBeGreaterThan(0);
    expect(node.position).toEqual(
      expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
    );
    expect((node.data as Record<string, unknown>).label).toBe('Image Gen');
    // the freshly added node is selected
    expect(state.selection).toEqual([node.id]);
  });

  it('appends to the EXISTING nodes (does not replace) and gives unique ids', () => {
    useCanvasCoreStore.setState({
      nodes: [{ id: 'pre-existing', type: 'note', data: { label: 'Note' } }],
    });
    render(<ClassicPalette />);

    fireEvent.click(screen.getByRole('button', { name: /add prompt/i }));
    fireEvent.click(screen.getByRole('button', { name: /add prompt/i }));

    const nodes = useCanvasCoreStore.getState().nodes as Record<
      string,
      unknown
    >[];
    expect(nodes).toHaveLength(3);
    expect(nodes[0].id).toBe('pre-existing');
    expect(nodes[1].type).toBe('prompt');
    expect(nodes[2].type).toBe('prompt');
    // ids are unique
    const ids = nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(3);
  });

  it('cascades the drop position so repeated adds do not stack exactly', () => {
    render(<ClassicPalette />);
    fireEvent.click(screen.getByRole('button', { name: /add text/i }));
    fireEvent.click(screen.getByRole('button', { name: /add text/i }));

    const nodes = useCanvasCoreStore.getState().nodes as Record<
      string,
      { x: number; y: number }
    >[];
    expect(nodes[0].position).not.toEqual(nodes[1].position);
  });
});
