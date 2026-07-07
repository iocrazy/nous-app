/**
 * DragCreateMenu — the floating node picker shown when a wire is dropped into
 * empty canvas (drag-to-create, PR-C2).
 *
 * Lists the node types for the current canvas kind. Picking one creates the
 * node at the release point and auto-wires it from the origin handle — all
 * through the existing canvasCoreStore actions (setNodes / setConnections /
 * setSelection), never bypassing them. Escape or a backdrop click dismisses.
 */
import { useCallback, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { CLASSIC_NODE_DEFINITIONS } from '../classic/registry';
import {
  createLoopNode,
  createOutputNode,
  createPromptNode,
  createShotNode,
} from '../smart/factories';

export interface DragCreateMenuProps {
  /** Container-relative screen position to anchor the menu at. */
  screenPosition: { x: number; y: number };
  /** Flow-space position where the new node is created. */
  flowPosition: { x: number; y: number };
  fromNodeId: string;
  fromHandle: string | null;
  onClose: () => void;
}

interface MenuItem {
  type: string;
  label: string;
  /** Input handle to wire the incoming edge into (null for smart nodes). */
  targetHandle: string | null;
  make: (position: { x: number; y: number }) => CanvasNode;
}

const SMART_ITEMS: MenuItem[] = [
  { type: 'shot', label: 'Shot', targetHandle: null, make: (p) => createShotNode({}, { position: p }) as CanvasNode },
  { type: 'prompt', label: 'Prompt', targetHandle: null, make: (p) => createPromptNode({}, { position: p }) as CanvasNode },
  { type: 'output', label: 'Output', targetHandle: null, make: (p) => createOutputNode({}, { position: p }) as CanvasNode },
  { type: 'loop', label: 'Loop', targetHandle: null, make: (p) => createLoopNode({}, { position: p }) as CanvasNode },
];

function classicItems(): MenuItem[] {
  return CLASSIC_NODE_DEFINITIONS.filter((def) => def.type !== 'group').map((def) => ({
    type: def.type,
    label: def.label,
    targetHandle: def.inputs[0]?.id ?? null,
    make: (p) => ({ id: `${def.type}-${crypto.randomUUID()}`, type: def.type, position: p, data: { label: def.label } }),
  }));
}

export function DragCreateMenu({
  screenPosition,
  flowPosition,
  fromNodeId,
  fromHandle,
  onClose,
}: DragCreateMenuProps) {
  const { t } = useTranslation();
  const kind = useCanvasCoreStore((s) => s.kind);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setConnections = useCanvasCoreStore((s) => s.setConnections);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const items = useMemo<MenuItem[]>(
    () => (kind === 'smart' ? SMART_ITEMS : kind === 'classic' ? classicItems() : []),
    [kind],
  );

  // Escape closes the menu without creating anything.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const create = useCallback(
    (item: MenuItem) => {
      const node = item.make(flowPosition);
      const id = (node as Record<string, unknown>).id as string;
      setNodes([...nodes, node]);
      const edge: CanvasConnection = {
        id: `edge-${crypto.randomUUID()}`,
        source: fromNodeId,
        target: id,
        sourceHandle: fromHandle,
        targetHandle: item.targetHandle,
      };
      setConnections([...connections, edge]);
      setSelection([id]);
      onClose();
    },
    [flowPosition, fromNodeId, fromHandle, nodes, connections, setNodes, setConnections, setSelection, onClose],
  );

  return (
    <>
      {/* Backdrop — a click anywhere off the menu dismisses it. */}
      <div className="absolute inset-0 z-40" onClick={onClose} aria-hidden="true" />
      <div
        role="menu"
        aria-label={t('canvas.dragCreate.title', 'Add node')}
        className="absolute z-50 min-w-[9rem] rounded-lg border border-ink-700 bg-ink-900/95 p-1 shadow-xl backdrop-blur"
        style={{ left: screenPosition.x, top: screenPosition.y }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-ink-400">
          {t('canvas.dragCreate.title', 'Add node')}
        </div>
        {items.map((item) => (
          <button
            key={item.type}
            type="button"
            role="menuitem"
            onClick={() => create(item)}
            className="block w-full rounded-md px-2 py-1 text-left text-xs font-medium text-ink-100 hover:bg-ink-800 hover:text-white"
          >
            {item.label}
          </button>
        ))}
      </div>
    </>
  );
}
