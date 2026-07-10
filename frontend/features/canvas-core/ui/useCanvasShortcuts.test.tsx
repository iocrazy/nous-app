import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearClipboard } from '../store/clipboard';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useCanvasShortcuts } from './useCanvasShortcuts';

function Host({
  enabled,
  onOpenPalette,
}: {
  enabled?: boolean;
  onOpenPalette?: () => void;
}) {
  useCanvasShortcuts({ enabled, onOpenPalette });
  return null;
}

function fireKey(opts: {
  key: string;
  meta?: boolean;
  shift?: boolean;
  target?: HTMLElement;
}) {
  const event = new KeyboardEvent('keydown', {
    key: opts.key,
    metaKey: opts.meta ?? false,
    ctrlKey: opts.meta ?? false,
    shiftKey: opts.shift ?? false,
    bubbles: true,
    cancelable: true,
  });
  (opts.target ?? window).dispatchEvent(event);
  return event;
}

beforeEach(() => {
  vi.useFakeTimers();
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    nodes: [
      { id: 'a', position: { x: 0, y: 0 } },
      { id: 'b', position: { x: 100, y: 0 } },
      { id: 'c', position: { x: 200, y: 0 } },
    ],
    connections: [{ id: 'e1', source: 'a', target: 'b' }],
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
    loadStatus: 'ready',
  });
  clearClipboard();
});

afterEach(() => {
  vi.useRealTimers();
  useCanvasCoreStore.getState().reset();
});

describe('useCanvasShortcuts — selection', () => {
  it('Cmd+A selects all node ids', () => {
    render(<Host />);
    fireKey({ key: 'a', meta: true });
    expect(useCanvasCoreStore.getState().selection).toEqual(['a', 'b', 'c']);
  });

  it('Esc with a selection clears it; Esc with empty selection no-ops', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setSelection(['a']);
    fireKey({ key: 'Escape' });
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
    // No-op path: Escape doesn't preventDefault when nothing is selected
    const ev = fireKey({ key: 'Escape' });
    expect(ev.defaultPrevented).toBe(false);
  });
});

describe('useCanvasShortcuts — undo / redo', () => {
  it('Cmd+Z calls undo', () => {
    render(<Host />);
    // Trigger one history burst so undo has something to do.
    useCanvasCoreStore.getState().setNodes([
      ...useCanvasCoreStore.getState().nodes,
      { id: 'new' },
    ]);
    vi.advanceTimersByTime(300);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(4);
    fireKey({ key: 'z', meta: true });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(3);
  });

  it('Cmd+Shift+Z calls redo', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setNodes([
      ...useCanvasCoreStore.getState().nodes,
      { id: 'new' },
    ]);
    vi.advanceTimersByTime(300);
    fireKey({ key: 'z', meta: true });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(3);
    fireKey({ key: 'z', meta: true, shift: true });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(4);
  });

  it('Cmd+Y calls redo (Windows convention)', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setNodes([
      ...useCanvasCoreStore.getState().nodes,
      { id: 'new' },
    ]);
    vi.advanceTimersByTime(300);
    fireKey({ key: 'z', meta: true });
    fireKey({ key: 'y', meta: true });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(4);
  });
});

describe('useCanvasShortcuts — copy / paste', () => {
  it('Cmd+C copies selected; Cmd+V pastes with offset + re-id', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setSelection(['a', 'b']);
    fireKey({ key: 'c', meta: true });
    fireKey({ key: 'v', meta: true });
    const state = useCanvasCoreStore.getState();
    // Started with 3, pasted 2 → 5
    expect(state.nodes).toHaveLength(5);
    // Pasted ids are re-id'd to avoid collision with originals.
    const ids = state.nodes.map((n) => (n as Record<string, unknown>).id);
    expect(ids).toContain('a-2');
    expect(ids).toContain('b-2');
    // Selection switches to the pasted clones.
    expect(state.selection).toEqual(['a-2', 'b-2']);
  });

  it('Cmd+V re-wires edges internal to the copied selection (no orphaned paste)', () => {
    render(<Host />);
    // a→b (e1) is internal to the {a,b} selection.
    useCanvasCoreStore.getState().setSelection(['a', 'b']);
    fireKey({ key: 'c', meta: true });
    fireKey({ key: 'v', meta: true });
    const state = useCanvasCoreStore.getState();
    // Original e1 + a re-mapped a-2→b-2 edge.
    expect(state.connections).toHaveLength(2);
    const pasted = state.connections.find((c) => c.id !== 'e1')!;
    expect(pasted).toMatchObject({ source: 'a-2', target: 'b-2' });
  });

  it('Cmd+C with empty selection is a no-op', () => {
    render(<Host />);
    const ev = fireKey({ key: 'c', meta: true });
    expect(ev.defaultPrevented).toBe(false);
  });

  it('Cmd+V with empty clipboard is a no-op', () => {
    render(<Host />);
    const ev = fireKey({ key: 'v', meta: true });
    expect(ev.defaultPrevented).toBe(false);
  });

  it('Cmd+V does not paste a copy from a different canvas kind', () => {
    render(<Host />);
    useCanvasCoreStore.setState({ kind: 'smart' });
    useCanvasCoreStore.getState().setSelection(['a']);
    fireKey({ key: 'c', meta: true }); // copied under kind 'smart'
    useCanvasCoreStore.setState({ kind: 'classic' }); // switch canvas kind
    const before = useCanvasCoreStore.getState().nodes.length;
    const ev = fireKey({ key: 'v', meta: true });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(before); // no paste
    expect(ev.defaultPrevented).toBe(false);
  });
});

describe('useCanvasShortcuts — delete', () => {
  it('Delete removes selected nodes AND dangling connections', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setSelection(['a']);
    fireKey({ key: 'Delete' });
    const state = useCanvasCoreStore.getState();
    expect(
      state.nodes.find((n) => (n as Record<string, unknown>).id === 'a'),
    ).toBeUndefined();
    // e1 referenced 'a' → also gone.
    expect(state.connections).toHaveLength(0);
    expect(state.selection).toEqual([]);
  });

  it('Backspace works the same as Delete', () => {
    render(<Host />);
    useCanvasCoreStore.getState().setSelection(['c']);
    fireKey({ key: 'Backspace' });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(2);
  });
});

describe('useCanvasShortcuts — editable focus', () => {
  it('skips when the keystroke originates inside an INPUT', () => {
    render(<Host />);
    const input = document.createElement('input');
    document.body.appendChild(input);
    useCanvasCoreStore.getState().setSelection(['a', 'b', 'c']);
    fireKey({ key: 'Escape', target: input });
    expect(useCanvasCoreStore.getState().selection).toEqual(['a', 'b', 'c']);
    document.body.removeChild(input);
  });
});

describe('useCanvasShortcuts — enabled flag', () => {
  it('when enabled=false the bindings do not fire', () => {
    render(<Host enabled={false} />);
    fireKey({ key: 'a', meta: true });
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
  });
});

describe('useCanvasShortcuts — Cmd+K palette (Phase 6c)', () => {
  it('Cmd+K calls the onOpenPalette callback', () => {
    const onOpenPalette = vi.fn();
    render(<Host onOpenPalette={onOpenPalette} />);
    fireKey({ key: 'k', meta: true });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('Ctrl+K also calls the callback (Windows/Linux)', () => {
    const onOpenPalette = vi.fn();
    render(<Host onOpenPalette={onOpenPalette} />);
    fireKey({ key: 'K', meta: true }); // upper-case variant from shift state
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('Cmd+K is ignored when focus is inside an editable element', () => {
    const onOpenPalette = vi.fn();
    render(<Host onOpenPalette={onOpenPalette} />);
    const input = document.createElement('input');
    document.body.appendChild(input);
    fireKey({ key: 'k', meta: true, target: input });
    expect(onOpenPalette).not.toHaveBeenCalled();
    document.body.removeChild(input);
  });

  it('Cmd+K is a no-op when no onOpenPalette callback is provided', () => {
    render(<Host />);
    // Should not throw even without a callback
    expect(() => fireKey({ key: 'k', meta: true })).not.toThrow();
  });
});

describe('useCanvasShortcuts — duplicate (G6, Infinite alt-drag-copy)', () => {
  it('Cmd+D clones the selection + internal edges, offset, and selects the copies', () => {
    render(<Host />);
    useCanvasCoreStore.setState({ selection: ['a', 'b'] });
    const evt = fireKey({ key: 'd', meta: true });
    expect(evt.defaultPrevented).toBe(true);

    const s = useCanvasCoreStore.getState();
    expect(s.nodes).toHaveLength(5);
    // Copies are offset and selected; originals stay put.
    const copies = s.nodes.filter(
      (n) => !['a', 'b', 'c'].includes((n as { id: string }).id),
    ) as Array<{ id: string; position: { x: number; y: number } }>;
    expect(copies).toHaveLength(2);
    expect(copies[0].position).toEqual({ x: 24, y: 24 });
    expect(new Set(s.selection)).toEqual(new Set(copies.map((n) => n.id)));
    // Internal edge a→b was cloned onto the copies.
    expect(s.connections).toHaveLength(2);
    const clone = s.connections.find((c) => c.id !== 'e1')!;
    expect(copies.map((n) => n.id)).toContain(clone.source);
    expect(copies.map((n) => n.id)).toContain(clone.target);
  });

  it('Cmd+D does not touch the copy/paste clipboard', () => {
    render(<Host />);
    useCanvasCoreStore.setState({ selection: ['a'] });
    fireKey({ key: 'c', meta: true }); // user copies 'a'
    useCanvasCoreStore.setState({ selection: ['b'] });
    fireKey({ key: 'd', meta: true }); // duplicating 'b' must not clobber it
    fireKey({ key: 'v', meta: true }); // paste still yields the copy of 'a'
    const s = useCanvasCoreStore.getState();
    // 3 originals + 1 duplicate of b + 1 paste of a
    expect(s.nodes).toHaveLength(5);
  });

  it('Cmd+D with empty selection is a no-op (browser bookmark untouched)', () => {
    render(<Host />);
    const evt = fireKey({ key: 'd', meta: true });
    expect(evt.defaultPrevented).toBe(false);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(3);
  });

  it('remaps smart data tags to the cloned ids (gen_slot never points at the original)', () => {
    render(<Host />);
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'x' } },
        {
          id: 'out1',
          type: 'output',
          position: { x: 320, y: 0 },
          data: { kind: 'image', gen_slot: { node_id: 'p1', index: 0 } },
        },
      ],
      connections: [],
      selection: ['p1', 'out1'],
    });
    fireKey({ key: 'd', meta: true });
    const s = useCanvasCoreStore.getState();
    const dupSlot = s.nodes.find((n) => {
      const o = n as { id: string; data?: { gen_slot?: { node_id: string } } };
      return o.id !== 'out1' && o.data?.gen_slot;
    }) as { data: { gen_slot: { node_id: string } } };
    const dupPrompt = s.nodes.find((n) => {
      const o = n as { id: string; type?: string };
      return o.type === 'prompt' && o.id !== 'p1';
    }) as { id: string };
    expect(dupSlot.data.gen_slot.node_id).toBe(dupPrompt.id);
  });

  it('strips smart data tags whose referent was NOT duplicated', () => {
    render(<Host />);
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'x' } },
        {
          id: 'out1',
          type: 'output',
          position: { x: 320, y: 0 },
          data: { kind: 'image', gen_slot: { node_id: 'p1', index: 0 } },
        },
      ],
      connections: [],
      selection: ['out1'], // slot only — its prompt stays behind
    });
    fireKey({ key: 'd', meta: true });
    const s = useCanvasCoreStore.getState();
    const dup = s.nodes.find((n) => {
      const o = n as { id: string; type?: string };
      return o.type === 'output' && o.id !== 'out1';
    }) as { data: { gen_slot?: unknown } };
    // Without the strip, the copy would STEAL the original slot's updates
    // (upsertGenerationSlots finds the first matching tag).
    expect(dup.data.gen_slot).toBeUndefined();
  });
});
