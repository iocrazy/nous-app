/**
 * Tests for the Cmd+K command registry (Phase 6c).
 *
 * TDD — written before commands.ts exists. Validates:
 *   - filterCommands: empty query, substring, fuzzy subsequence, hint fallback
 *   - buildCanvasCommands: correct ids, enabled predicates mirror real store,
 *     run() delegates to real store actions.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  buildCanvasCommands,
  filterCommands,
  type Command,
} from './commands';

// ---- Test helpers --------------------------------------------------------

beforeEach(() => {
  vi.useFakeTimers();
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'test-canvas',
    loadStatus: 'ready',
    kind: 'smart',
    nodes: [],
    connections: [],
    baseUpdatedAt: '2026-01-01T00:00:00Z',
  });
});

afterEach(() => {
  vi.useRealTimers();
  useCanvasCoreStore.getState().reset();
});

// ---- filterCommands -------------------------------------------------------

describe('filterCommands', () => {
  const cmds: Command[] = [
    { id: 'undo', title: 'Undo', run: vi.fn() },
    { id: 'redo', title: 'Redo', run: vi.fn() },
    { id: 'select-all', title: 'Select All', run: vi.fn() },
    { id: 'save', title: 'Save', hint: 'Persist', run: vi.fn() },
  ];

  it('empty query returns all commands', () => {
    expect(filterCommands('', cmds)).toHaveLength(4);
  });

  it('whitespace-only query returns all commands', () => {
    expect(filterCommands('   ', cmds)).toHaveLength(4);
  });

  it('exact substring match on title (case-insensitive)', () => {
    const result = filterCommands('undo', cmds);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('undo');
  });

  it('uppercase query matches lowercase title', () => {
    const result = filterCommands('UNDO', cmds);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('undo');
  });

  it('fuzzy subsequence — characters appear in order in title', () => {
    // 's', 'l', 'l' appear in sequence in "Select All"
    const result = filterCommands('sll', cmds);
    expect(result.map((c) => c.id)).toContain('select-all');
  });

  it('matches against hint when title does not match', () => {
    // 'persist' only appears in save's hint
    const result = filterCommands('persist', cmds);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('save');
  });

  it('returns empty array when nothing matches', () => {
    expect(filterCommands('xyzzy', cmds)).toHaveLength(0);
  });
});

// ---- buildCanvasCommands — ids -------------------------------------------

describe('buildCanvasCommands — ids', () => {
  it('includes all store-action command ids', () => {
    const ids = buildCanvasCommands().map((c) => c.id);
    expect(ids).toContain('undo');
    expect(ids).toContain('redo');
    expect(ids).toContain('select-all');
    expect(ids).toContain('clear-selection');
    expect(ids).toContain('save');
  });

  it('includes node-add command ids', () => {
    const ids = buildCanvasCommands().map((c) => c.id);
    expect(ids).toContain('add-shot');
    expect(ids).toContain('add-prompt');
    expect(ids).toContain('add-output');
    expect(ids).toContain('add-loop');
  });
});

// ---- buildCanvasCommands — enabled predicates ----------------------------

describe('buildCanvasCommands — enabled predicates', () => {
  it('undo.enabled() is false when history is empty', () => {
    const undo = buildCanvasCommands().find((c) => c.id === 'undo')!;
    expect(undo.enabled?.()).toBe(false);
  });

  it('undo.enabled() is true after an edit has been made', () => {
    useCanvasCoreStore.getState().setNodes([{ id: 'n1' }]);
    vi.advanceTimersByTime(300); // flush history debounce (250 ms)
    const undo = buildCanvasCommands().find((c) => c.id === 'undo')!;
    expect(undo.enabled?.()).toBe(true);
  });

  it('redo.enabled() is false initially', () => {
    const redo = buildCanvasCommands().find((c) => c.id === 'redo')!;
    expect(redo.enabled?.()).toBe(false);
  });

  it('redo.enabled() is true after undo', () => {
    useCanvasCoreStore.getState().setNodes([{ id: 'n1' }]);
    vi.advanceTimersByTime(300);
    useCanvasCoreStore.getState().undo();
    const redo = buildCanvasCommands().find((c) => c.id === 'redo')!;
    expect(redo.enabled?.()).toBe(true);
  });

  it('clear-selection.enabled() is false when nothing selected', () => {
    const cmd = buildCanvasCommands().find((c) => c.id === 'clear-selection')!;
    expect(cmd.enabled?.()).toBe(false);
  });

  it('clear-selection.enabled() is true when something is selected', () => {
    useCanvasCoreStore.getState().setSelection(['n1']);
    const cmd = buildCanvasCommands().find((c) => c.id === 'clear-selection')!;
    expect(cmd.enabled?.()).toBe(true);
  });

  it('node-add commands are disabled when canvas kind is not smart', () => {
    useCanvasCoreStore.setState({ kind: 'classic' });
    const addShot = buildCanvasCommands().find((c) => c.id === 'add-shot')!;
    expect(addShot.enabled?.()).toBe(false);
  });

  it('node-add commands are enabled when canvas kind is smart', () => {
    const addShot = buildCanvasCommands().find((c) => c.id === 'add-shot')!;
    expect(addShot.enabled?.()).toBe(true);
  });
});

// ---- buildCanvasCommands — run() delegates to real store -----------------

describe('buildCanvasCommands — run() delegates to store', () => {
  it('undo.run() restores the previous node set', () => {
    useCanvasCoreStore.getState().setNodes([{ id: 'n1' }]);
    vi.advanceTimersByTime(300);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);

    buildCanvasCommands().find((c) => c.id === 'undo')!.run();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });

  it('select-all.run() selects every node id', () => {
    useCanvasCoreStore.setState({ nodes: [{ id: 'a' }, { id: 'b' }] });
    buildCanvasCommands().find((c) => c.id === 'select-all')!.run();
    expect(useCanvasCoreStore.getState().selection).toEqual(['a', 'b']);
  });

  it('clear-selection.run() empties the selection', () => {
    useCanvasCoreStore.getState().setSelection(['a', 'b']);
    buildCanvasCommands().find((c) => c.id === 'clear-selection')!.run();
    expect(useCanvasCoreStore.getState().selection).toEqual([]);
  });

  it('add-shot.run() appends a shot node', () => {
    buildCanvasCommands().find((c) => c.id === 'add-shot')!.run();
    const { nodes } = useCanvasCoreStore.getState();
    expect(nodes).toHaveLength(1);
    expect((nodes[0] as Record<string, unknown>).type).toBe('shot');
  });

  it('add-prompt.run() appends a prompt node', () => {
    buildCanvasCommands().find((c) => c.id === 'add-prompt')!.run();
    const { nodes } = useCanvasCoreStore.getState();
    expect(nodes).toHaveLength(1);
    expect((nodes[0] as Record<string, unknown>).type).toBe('prompt');
  });

  it('add-output.run() appends an output node', () => {
    buildCanvasCommands().find((c) => c.id === 'add-output')!.run();
    const { nodes } = useCanvasCoreStore.getState();
    expect(nodes).toHaveLength(1);
    expect((nodes[0] as Record<string, unknown>).type).toBe('output');
  });

  it('add-loop.run() appends a loop node', () => {
    buildCanvasCommands().find((c) => c.id === 'add-loop')!.run();
    const { nodes } = useCanvasCoreStore.getState();
    expect(nodes).toHaveLength(1);
    expect((nodes[0] as Record<string, unknown>).type).toBe('loop');
  });
});
