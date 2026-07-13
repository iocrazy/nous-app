/**
 * TopNodeBar — Standard canvas top node strip (Phase 2.2). Pins: a chip
 * click drops the node at the viewport centre through the store; the
 * Image/Video Gen chips create prompts pre-set to that generation kind.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { TopNodeBar } from './TopNodeBar';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const surfaceRef = {
  current: {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
  } as unknown as HTMLDivElement,
};

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    kind: 'smart',
    loadStatus: 'ready',
    viewport: { x: 0, y: 0, zoom: 1 },
  } as never);
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('TopNodeBar', () => {
  it('renders one chip per node type', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    for (const label of [
      'Upload',
      'Shot',
      'Prompt',
      'LLM',
      'Image Gen',
      'Video Gen',
      'Loop',
      'Timeline',
      'Output',
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
  });

  it('a chip click appends the node at the viewport centre and selects it', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    fireEvent.click(screen.getByRole('button', { name: 'LLM' }));
    const state = useCanvasCoreStore.getState();
    expect(state.nodes).toHaveLength(1);
    const node = state.nodes[0] as Record<string, unknown>;
    expect(node.type).toBe('llm');
    expect(node.position).toEqual({ x: 400, y: 300 });
    expect(state.selection).toEqual([node.id]);
  });

  it('Image Gen / Video Gen create prompts pre-set to that kind', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    fireEvent.click(screen.getByRole('button', { name: 'Image Gen' }));
    fireEvent.click(screen.getByRole('button', { name: 'Video Gen' }));
    const [img, vid] = useCanvasCoreStore.getState().nodes as Array<{
      type: string;
      data: { gen?: { kind: string } };
    }>;
    expect(img.type).toBe('prompt');
    expect(img.data.gen?.kind).toBe('image');
    expect(vid.data.gen?.kind).toBe('video');
  });
});
