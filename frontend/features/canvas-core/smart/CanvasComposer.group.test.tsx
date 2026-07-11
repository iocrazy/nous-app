// features/canvas-core/smart/CanvasComposer.group.test.tsx
// Composer Group/Ungroup (②-3): grouping wraps the selection and selects
// the container; the button flips to Ungroup when a group is selected.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

import { CanvasComposer } from './CanvasComposer';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

function seed(selection: string[]): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [
      { id: 'a', type: 'shot', position: { x: 100, y: 100 }, data: {} },
      { id: 'b', type: 'shot', position: { x: 300, y: 200 }, data: {} },
    ] as CanvasNode[],
    connections: [],
    selection,
  });
}

describe('CanvasComposer — Group / Ungroup', () => {
  it('Group wraps the selection, selects the container, and flips to Ungroup', () => {
    seed(['a', 'b']);
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: 'Group' }));

    const s = useCanvasCoreStore.getState();
    const group = s.nodes.find((n) => (n as Record<string, unknown>).type === 'group');
    expect(group).toBeTruthy();
    const a = s.nodes.find((n) => (n as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBe((group as { id: string }).id);
    expect(s.selection).toEqual([(group as { id: string }).id]);
    expect(screen.getByRole('button', { name: 'Ungroup' })).toBeTruthy();
  });

  it('Ungroup releases members and removes the container', () => {
    seed(['a', 'b']);
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: 'Group' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ungroup' }));

    const s = useCanvasCoreStore.getState();
    expect(s.nodes.find((n) => (n as Record<string, unknown>).type === 'group')).toBeUndefined();
    const a = s.nodes.find((n) => (n as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBeUndefined();
    expect(a.position).toEqual({ x: 100, y: 100 });
  });

  it('Group disabled with fewer than 2 selected', () => {
    seed(['a']);
    render(<CanvasComposer />);
    expect(
      (screen.getByRole('button', { name: 'Group' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
