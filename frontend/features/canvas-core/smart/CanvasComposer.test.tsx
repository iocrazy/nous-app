import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { CanvasComposer } from './CanvasComposer';
import { _resetIdCounter } from './factories';

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  _resetIdCounter();
});

describe('CanvasComposer', () => {
  it('renders three Add buttons', () => {
    render(<CanvasComposer />);
    expect(screen.getByRole('button', { name: /\+ shot/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ prompt/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ output/i })).toBeInTheDocument();
  });

  it('clicking "+ Shot" appends a shot node + selects it', () => {
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: /\+ shot/i }));
    const state = useCanvasCoreStore.getState();
    expect(state.nodes).toHaveLength(1);
    const node = state.nodes[0] as Record<string, unknown>;
    expect(node.type).toBe('shot');
    expect(state.selection).toEqual([node.id]);
  });

  it('clicking each button appends a node of the matching type', () => {
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: /\+ shot/i }));
    fireEvent.click(screen.getByRole('button', { name: /\+ prompt/i }));
    fireEvent.click(screen.getByRole('button', { name: /\+ output/i }));
    const types = useCanvasCoreStore
      .getState()
      .nodes.map((n) => (n as Record<string, unknown>).type);
    expect(types).toEqual(['shot', 'prompt', 'output']);
  });

  it('toolbar is role="toolbar" with an accessible name', () => {
    render(<CanvasComposer />);
    expect(
      screen.getByRole('toolbar', { name: /smart canvas composer/i }),
    ).toBeInTheDocument();
  });
});
