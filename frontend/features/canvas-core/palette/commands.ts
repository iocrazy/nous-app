/**
 * Cmd+K command palette — command registry (Phase 6c).
 *
 * Pure module: no React, no side effects beyond delegating to the canvas
 * store. Importable in both browser and vitest (jsdom) environments.
 *
 * Usage:
 *   const cmds = buildCanvasCommands();        // all commands
 *   const visible = filterCommands(query, cmds); // narrow by search
 */

import {
  createLoopNode,
  createOutputNode,
  createPromptNode,
  createShotNode,
} from '../smart/factories';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

// ---- Public types --------------------------------------------------------

export interface Command {
  id: string;
  title: string;
  /** Short descriptor shown in the palette row (e.g. key hint, category). */
  hint?: string;
  /** Execute the command. Called on Enter / click in the palette. */
  run(): void;
  /**
   * Optional availability predicate. When supplied and returns `false`, the
   * command is shown grayed-out and is not selectable. Evaluated at render
   * time so it always reflects the current store snapshot.
   */
  enabled?(): boolean;
}

// ---- Filtering -----------------------------------------------------------

/**
 * Return the subset of `commands` whose `title` or `hint` contains a
 * case-insensitive **subsequence** match for `query`. An empty / whitespace-
 * only query returns every command unchanged.
 */
export function filterCommands(query: string, commands: Command[]): Command[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  return commands.filter(
    (cmd) =>
      subsequenceMatch(q, cmd.title.toLowerCase()) ||
      (cmd.hint !== undefined && subsequenceMatch(q, cmd.hint.toLowerCase())),
  );
}

/** True when every character of `query` appears in `target` in order. */
function subsequenceMatch(query: string, target: string): boolean {
  let qi = 0;
  for (let i = 0; i < target.length && qi < query.length; i++) {
    if (target[i] === query[qi]) qi++;
  }
  return qi === query.length;
}

// ---- Registry builder ----------------------------------------------------

/**
 * Build the complete command list for the currently open canvas.
 *
 * `enabled()` closures read `useCanvasCoreStore.getState()` lazily so they
 * always reflect live store data — they are NOT snapshotted at build time.
 * Similarly `run()` delegates to `getState()` at call time; no stale refs.
 *
 * Returns a new array on every call; callers should memoize if needed.
 */
export function buildCanvasCommands(): Command[] {
  // ---- Store actions -------------------------------------------------------

  const storeActions: Command[] = [
    {
      id: 'undo',
      title: 'Undo',
      hint: '⌘Z',
      run() {
        useCanvasCoreStore.getState().undo();
      },
      enabled() {
        return useCanvasCoreStore.getState().canUndo();
      },
    },
    {
      id: 'redo',
      title: 'Redo',
      hint: '⌘⇧Z',
      run() {
        useCanvasCoreStore.getState().redo();
      },
      enabled() {
        return useCanvasCoreStore.getState().canRedo();
      },
    },
    {
      id: 'select-all',
      title: 'Select All',
      hint: '⌘A',
      run() {
        useCanvasCoreStore.getState().selectAll();
      },
    },
    {
      id: 'clear-selection',
      title: 'Clear Selection',
      hint: 'Esc',
      run() {
        useCanvasCoreStore.getState().clearSelection();
      },
      enabled() {
        return useCanvasCoreStore.getState().selection.length > 0;
      },
    },
    {
      id: 'save',
      title: 'Save',
      hint: '⌘S',
      run() {
        void useCanvasCoreStore.getState().flushSave();
      },
    },
  ];

  // ---- Node-add commands (smart canvas only) --------------------------------

  function smartOnly(): boolean {
    return useCanvasCoreStore.getState().kind === 'smart';
  }

  const nodeAddCommands: Command[] = [
    {
      id: 'add-shot',
      title: 'Add Shot Node',
      hint: 'Node',
      run() {
        const s = useCanvasCoreStore.getState();
        s.setNodes([...s.nodes, createShotNode()]);
      },
      enabled: smartOnly,
    },
    {
      id: 'add-prompt',
      title: 'Add Prompt Node',
      hint: 'Node',
      run() {
        const s = useCanvasCoreStore.getState();
        s.setNodes([...s.nodes, createPromptNode()]);
      },
      enabled: smartOnly,
    },
    {
      id: 'add-output',
      title: 'Add Output Node',
      hint: 'Node',
      run() {
        const s = useCanvasCoreStore.getState();
        s.setNodes([...s.nodes, createOutputNode()]);
      },
      enabled: smartOnly,
    },
    {
      id: 'add-loop',
      title: 'Add Loop Node',
      hint: 'Node',
      run() {
        const s = useCanvasCoreStore.getState();
        s.setNodes([...s.nodes, createLoopNode()]);
      },
      enabled: smartOnly,
    },
  ];

  return [...storeActions, ...nodeAddCommands];
}
