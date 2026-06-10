/**
 * Canvas keyboard shortcuts (Phase 1 Week 3).
 *
 *   Cmd/Ctrl + Z              → undo
 *   Cmd/Ctrl + Shift + Z      → redo
 *   Cmd/Ctrl + Y              → redo (Windows convention)
 *   Cmd/Ctrl + A              → select all
 *   Esc                       → clear selection
 *   Cmd/Ctrl + C              → copy selected nodes (in-memory clipboard)
 *   Cmd/Ctrl + V              → paste from in-memory clipboard
 *   Delete / Backspace        → delete selected nodes
 *
 * Bound at window scope while the hook is mounted. Skipped when the
 * keystroke originates inside an editable text field so node-rename
 * inputs (and any future inline editors) keep working.
 */

import { useEffect } from 'react';

import {
  copyNodesToClipboard,
  preparePastedNodes,
  readClipboard,
} from '../store/clipboard';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

interface UseCanvasShortcutsOptions {
  /** Disable the bindings without unmounting the host component. */
  enabled?: boolean;
}

export function useCanvasShortcuts(options: UseCanvasShortcutsOptions = {}) {
  const enabled = options.enabled ?? true;

  useEffect(() => {
    if (!enabled) return;
    const handler = (event: KeyboardEvent) => {
      if (isInsideEditable(event.target)) return;
      const meta = event.metaKey || event.ctrlKey;
      const key = event.key;

      const store = useCanvasCoreStore.getState();

      if (meta && (key === 'z' || key === 'Z')) {
        event.preventDefault();
        if (event.shiftKey) store.redo();
        else store.undo();
        return;
      }
      if (meta && (key === 'y' || key === 'Y')) {
        event.preventDefault();
        store.redo();
        return;
      }
      if (meta && (key === 'a' || key === 'A')) {
        event.preventDefault();
        store.selectAll();
        return;
      }
      if (key === 'Escape') {
        // Only meaningful when there IS a selection — let other Esc
        // handlers (e.g. dialog dismiss) take precedence by default,
        // but if we own the focus *and* have a selection, eat it.
        if (store.selection.length === 0) return;
        event.preventDefault();
        store.clearSelection();
        return;
      }
      if (meta && (key === 'c' || key === 'C')) {
        if (store.selection.length === 0) return;
        event.preventDefault();
        const selected = collectSelectedNodes(store.nodes, store.selection);
        copyNodesToClipboard(selected);
        return;
      }
      if (meta && (key === 'v' || key === 'V')) {
        const buf = readClipboard();
        if (!buf || buf.nodes.length === 0) return;
        event.preventDefault();
        const existing = collectIds(store.nodes);
        const pasted = preparePastedNodes(buf.nodes, existing);
        store.setNodes([...store.nodes, ...pasted]);
        store.setSelection(
          pasted
            .map((n) => idOf(n))
            .filter((v): v is string => v !== null),
        );
        return;
      }
      if (key === 'Delete' || key === 'Backspace') {
        if (store.selection.length === 0) return;
        event.preventDefault();
        const selected = new Set(store.selection);
        const remaining = store.nodes.filter((n) => {
          const id = idOf(n);
          return id !== null && !selected.has(id);
        });
        // Also strip dangling connections that referenced the deleted nodes.
        const remainingConnections = store.connections.filter((c) => {
          const obj = c as Record<string, unknown>;
          const source = typeof obj.source === 'string' ? obj.source : null;
          const target = typeof obj.target === 'string' ? obj.target : null;
          if (source && selected.has(source)) return false;
          if (target && selected.has(target)) return false;
          return true;
        });
        store.setNodes(remaining);
        store.setConnections(remainingConnections);
        store.clearSelection();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [enabled]);
}

function isInsideEditable(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return false;
}

function idOf(node: CanvasNode): string | null {
  const obj = node as Record<string, unknown>;
  return typeof obj.id === 'string' ? obj.id : null;
}

function collectIds(nodes: CanvasNode[]): Set<string> {
  const set = new Set<string>();
  for (const n of nodes) {
    const id = idOf(n);
    if (id) set.add(id);
  }
  return set;
}

function collectSelectedNodes(
  nodes: CanvasNode[],
  selection: string[],
): CanvasNode[] {
  const selected = new Set(selection);
  return nodes.filter((n) => {
    const id = idOf(n);
    return id !== null && selected.has(id);
  });
}
