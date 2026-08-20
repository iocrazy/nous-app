/**
 * Canvas keyboard shortcuts (Phase 1 Week 3).
 *
 *   Cmd/Ctrl + K              → open command palette (Phase 6c)
 *   Cmd/Ctrl + Z              → undo
 *   Cmd/Ctrl + Shift + Z      → redo
 *   Cmd/Ctrl + Y              → redo (Windows convention)
 *   Cmd/Ctrl + A              → select all
 *   Esc                       → clear selection
 *   Cmd/Ctrl + C              → copy selected nodes (in-memory clipboard)
 *   Cmd/Ctrl + V              → paste from in-memory clipboard
 *   Cmd/Ctrl + D              → duplicate selected nodes in place (G6)
 *   Delete / Backspace        → delete selected nodes
 *
 * Bound at window scope while the hook is mounted. Skipped when the
 * keystroke originates inside an editable text field so node-rename
 * inputs (and any future inline editors) keep working.
 *
 * Note: when the command palette is open its search <input> holds focus, so
 * `isInsideEditable` returns true for all palette keystrokes — the palette
 * handles its own Esc / Enter / arrow navigation independently.
 */

import { useEffect, useRef } from 'react';

import { useKnifeStore } from '../../../canvas-kit/knifeStore';
import {
  copyToClipboard,
  prepareDuplicate,
  preparePaste,
  readClipboard,
} from '../store/clipboard';
import { deleteNodesById } from '../smart/deleteNodes';
import { groupSelection, ungroupNode } from '../smart/grouping';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { getPointerWorld } from './pointerWorld';
import { getRfInstance } from './rfInstance';
import { toggleZoomPreview } from './zoomPreview';
import type { CanvasNode } from '../types';

interface UseCanvasShortcutsOptions {
  /** Disable the bindings without unmounting the host component. */
  enabled?: boolean;
  /**
   * Read-only session (`can_edit:false`, or a latched 403). The MUTATING
   * chords are dropped: paste, duplicate, group/ungroup, delete, undo/redo
   * and the knife toggle. The read/navigation ones stay — palette, help,
   * copy, select-all, Esc — because they don't touch the document and are
   * how a viewer actually works with it.
   *
   * Without this, every one of those chords still edited the in-memory doc
   * (the store swallowed the result at `markDirty`): a viewer could press
   * Delete, watch nodes vanish, and lose nothing except their confidence
   * that the canvas is intact.
   */
  readOnly?: boolean;
  /**
   * Called when the user presses Cmd/Ctrl+K outside an editable element.
   * Provided by CanvasPage to open the command palette. Uses a ref internally
   * so stale-closure re-registration is never needed.
   */
  onOpenPalette?: () => void;
  /** Called on `?` (Shift+/) outside an editable element — opens the
   *  keyboard-shortcut help panel. */
  onOpenHelp?: () => void;
}

export function useCanvasShortcuts(options: UseCanvasShortcutsOptions = {}) {
  const enabled = options.enabled ?? true;
  const readOnly = options.readOnly ?? false;

  // Keep a stable ref so the window listener never goes stale without
  // re-subscribing (avoids adding onOpenPalette to the effect dep array).
  const onOpenPaletteRef = useRef(options.onOpenPalette);
  const onOpenHelpRef = useRef(options.onOpenHelp);
  useEffect(() => {
    onOpenPaletteRef.current = options.onOpenPalette;
    onOpenHelpRef.current = options.onOpenHelp;
  });

  useEffect(() => {
    if (!enabled) return;
    const handler = (event: KeyboardEvent) => {
      if (isInsideEditable(event.target)) return;
      const meta = event.metaKey || event.ctrlKey;
      const key = event.key;

      const store = useCanvasCoreStore.getState();

      // Cmd+K — open command palette (before all other meta+key branches so
      // it takes priority and its callback is clearly separated from store ops).
      if (meta && (key === 'k' || key === 'K')) {
        event.preventDefault();
        onOpenPaletteRef.current?.();
        return;
      }

      // ? (Shift+/) — open the shortcut help panel. No modifier: it's a
      // bare punctuation key, not a chord.
      if (!meta && key === '?') {
        event.preventDefault();
        onOpenHelpRef.current?.();
        return;
      }

      if (meta && (key === 'z' || key === 'Z')) {
        // Undo/redo replay document snapshots — a write, even though it
        // only ever restores states this session already saw.
        if (readOnly) return;
        event.preventDefault();
        if (event.shiftKey) store.redo();
        else store.undo();
        return;
      }
      if (meta && (key === 'y' || key === 'Y')) {
        if (readOnly) return;
        event.preventDefault();
        store.redo();
        return;
      }
      if (meta && (key === 'a' || key === 'A')) {
        event.preventDefault();
        store.selectAll();
        return;
      }
      if (!meta && (key === 'x' || key === 'X')) {
        // Knife mode toggle (Infinite parity ②-1). Bare x only — mod+x
        // stays the browser's cut. Knife exists to CUT wires, so a
        // read-only session must not be able to arm it (the surface also
        // refuses to render the overlay — two locks, see CanvasSurface).
        if (readOnly) return;
        useKnifeStore.getState().toggle();
        return;
      }
      if (key === 'Escape') {
        // Knife mode swallows the first Escape — the selection survives.
        if (useKnifeStore.getState().active) {
          event.preventDefault();
          useKnifeStore.getState().exit();
          return;
        }
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
        // Carry the edges internal to the selection + the canvas kind.
        copyToClipboard(store.kind, selected, store.connections);
        return;
      }
      if (meta && (key === 'v' || key === 'V')) {
        if (readOnly) return;
        const buf = readClipboard();
        if (!buf || buf.nodes.length === 0) return;
        // Don't paste a selection copied from a canvas of a different kind:
        // the node types wouldn't render in the other kind's registry.
        if (buf.kind !== store.kind) return;
        event.preventDefault();
        const existing = collectIds(store.nodes);
        // IC pasteNodes: land on the pointer when it's over the canvas,
        // else fall back to the cascading offset.
        const at = getPointerWorld();
        const prepared = preparePaste(existing, at ? { at } : {});
        if (!prepared) return;
        store.setNodes([...store.nodes, ...prepared.nodes]);
        // Re-map internal edges onto the pasted nodes so the pasted subgraph
        // keeps its wiring instead of landing as orphaned nodes.
        if (prepared.connections.length > 0) {
          store.setConnections([...store.connections, ...prepared.connections]);
        }
        store.setSelection(
          prepared.nodes
            .map((n) => idOf(n))
            .filter((v): v is string => v !== null),
        );
        return;
      }
      if (meta && (key === 'g' || key === 'G')) {
        // Group / Ungroup (P1-10, Infinite's Ctrl+G / Ctrl+Shift+G). The
        // data model shipped with ②-3; this is the missing keyboard entry.
        if (readOnly) return;
        event.preventDefault();
        if (event.shiftKey) {
          const sel = new Set(store.selection);
          const g = store.nodes.find(
            (n) =>
              sel.has((n as { id?: string }).id as string) &&
              (n as { type?: string }).type === 'group',
          );
          if (!g) return;
          store.setNodes(ungroupNode(store.nodes, String((g as { id?: string }).id)));
          store.setSelection([]);
        } else {
          const result = groupSelection(store.nodes, store.selection);
          if (!result) return;
          store.setNodes(result.nodes);
          store.setSelection([result.groupId]);
        }
        return;
      }

      if (meta && (key === 'd' || key === 'D')) {
        // Duplicate in place (G6 — Infinite's alt-drag-copy, keyboard form).
        // Only preventDefault when we actually act: with nothing selected the
        // browser keeps its bookmark shortcut.
        if (readOnly) return;
        if (store.selection.length === 0) return;
        event.preventDefault();
        const selected = collectSelectedNodes(store.nodes, store.selection);
        const prepared = prepareDuplicate(
          selected,
          store.connections,
          collectIds(store.nodes),
        );
        if (!prepared) return;
        store.setNodes([...store.nodes, ...prepared.nodes]);
        if (prepared.connections.length > 0) {
          store.setConnections([...store.connections, ...prepared.connections]);
        }
        store.setSelection(
          prepared.nodes
            .map((n) => idOf(n))
            .filter((v): v is string => v !== null),
        );
        return;
      }
      if ((key === 'z' || key === 'Z') && !meta && !event.shiftKey) {
        // IC Z-overview: fit everything / glide back. View-only — allowed
        // in read-only sessions too.
        const inst = getRfInstance();
        if (!inst) return;
        event.preventDefault();
        toggleZoomPreview(inst);
        return;
      }
      if (key === 'Delete' || key === 'Backspace') {
        if (readOnly) return;
        if (store.selection.length === 0) return;
        event.preventDefault();
        deleteNodesById(store.selection);
        store.clearSelection();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [enabled, readOnly]);
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
