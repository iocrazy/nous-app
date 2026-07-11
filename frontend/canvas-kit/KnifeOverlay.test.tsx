// canvas-kit/KnifeOverlay.test.tsx
// Knife mode (Infinite parity): while active, dragging draws a trail and
// cuts every edge whose sampled polyline the trail crosses — live during
// the drag, node pass-throughs spare their own edges. Edge sampling is an
// injected dependency so this stays jsdom-testable.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { KnifeOverlay, type EdgeSample } from './KnifeOverlay';

afterEach(() => cleanup());

const EDGES: EdgeSample[] = [
  // Vertical-ish edge at x=50 (polyline of two points)
  { id: 'e1', points: [{ x: 50, y: 0 }, { x: 50, y: 100 }] },
  // Far-away edge that must survive
  { id: 'e2', points: [{ x: 500, y: 0 }, { x: 500, y: 100 }] },
];

const NODES = [{ id: 'n1', rect: { x: 480, y: 0, width: 60, height: 120 } }];

function renderKnife(onCut = vi.fn()) {
  render(
    <KnifeOverlay
      sampleEdges={() => EDGES}
      sampleNodes={() => NODES}
      edgeEndpoints={() => ({ e1: { source: 'a', target: 'b' }, e2: { source: 'n1', target: 'c' } })}
      onCut={onCut}
      onExit={vi.fn()}
    />,
  );
  return onCut;
}

describe('KnifeOverlay', () => {
  it('cuts an edge crossed by the drag and leaves others alone', () => {
    const onCut = renderKnife();
    const overlay = screen.getByTestId('knife-overlay');
    fireEvent.mouseDown(overlay, { clientX: 20, clientY: 50 });
    fireEvent.mouseMove(overlay, { clientX: 80, clientY: 50 });
    expect(onCut).toHaveBeenCalledWith(['e1']);
  });

  it('spares edges attached to a node the trail passes through', () => {
    const onCut = renderKnife();
    const overlay = screen.getByTestId('knife-overlay');
    // Crosses BOTH e2 (x=500) and node n1's rect — e2 hangs off n1, spared.
    fireEvent.mouseDown(overlay, { clientX: 450, clientY: 50 });
    fireEvent.mouseMove(overlay, { clientX: 560, clientY: 50 });
    expect(onCut).not.toHaveBeenCalled();
  });

  it('renders the trail polyline while dragging', () => {
    renderKnife();
    const overlay = screen.getByTestId('knife-overlay');
    fireEvent.mouseDown(overlay, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(overlay, { clientX: 30, clientY: 30 });
    expect(screen.getByTestId('knife-trail')).toBeTruthy();
    fireEvent.mouseUp(overlay);
    expect(screen.queryByTestId('knife-trail')).toBeNull();
  });

  it('Escape exits knife mode', () => {
    const onExit = vi.fn();
    render(
      <KnifeOverlay
        sampleEdges={() => []}
        sampleNodes={() => []}
        edgeEndpoints={() => ({})}
        onCut={vi.fn()}
        onExit={onExit}
      />,
    );
    fireEvent.keyDown(screen.getAllByTestId('knife-overlay')[0], { key: 'Escape' });
    expect(onExit).toHaveBeenCalled();
  });
});
