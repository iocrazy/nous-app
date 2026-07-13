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
import {
  Clapperboard,
  Film,
  MonitorPlay,
  Repeat2,
  TextCursorInput,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react';
import { isSmartFamily } from '../types';
import { useTranslation } from 'react-i18next';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { CLASSIC_NODE_DEFINITIONS, getClassicNodeDefinition } from '../classic/registry';
import { canConnectSmart } from '../smart/types';
import {
  createLoopNode,
  createMediaNode,
  createOutputNode,
  createPromptNode,
  createShotNode,
  createTimelineNode,
} from '../smart/factories';

export interface DragCreateMenuProps {
  /** Container-relative screen position to anchor the menu at. */
  screenPosition: { x: number; y: number };
  /** Flow-space position where the new node is created. */
  flowPosition: { x: number; y: number };
  fromNodeId: string | null;
  fromHandle: string | null;
  onClose: () => void;
}

interface MenuItem {
  type: string;
  label: string;
  /** Input handle to wire the incoming edge into (null for smart nodes). */
  targetHandle: string | null;
  make: (position: { x: number; y: number }) => CanvasNode;
  /** Card chrome (smart only) — Infinite-style icon + one-line description. */
  icon?: LucideIcon;
  descKey?: string;
  descDefault?: string;
}

const SMART_ITEMS: MenuItem[] = [
  {
    type: 'media',
    label: 'Upload',
    targetHandle: null,
    make: (p) => createMediaNode({}, { position: p }) as CanvasNode,
    icon: UploadCloud,
    descKey: 'canvas.dragCreate.desc.media',
    descDefault: 'Import images or video onto the canvas',
  },
  {
    type: 'shot',
    label: 'Shot',
    targetHandle: null,
    make: (p) => createShotNode({}, { position: p }) as CanvasNode,
    icon: Clapperboard,
    descKey: 'canvas.dragCreate.desc.shot',
    descDefault: 'Shot card — media for one storyboard beat',
  },
  {
    type: 'prompt',
    label: 'Prompt',
    targetHandle: null,
    make: (p) => createPromptNode({}, { position: p }) as CanvasNode,
    icon: TextCursorInput,
    descKey: 'canvas.dragCreate.desc.prompt',
    descDefault: 'Write or AI-generate text, then run it',
  },
  {
    type: 'output',
    label: 'Output',
    targetHandle: null,
    make: (p) => createOutputNode({}, { position: p }) as CanvasNode,
    icon: MonitorPlay,
    descKey: 'canvas.dragCreate.desc.output',
    descDefault: 'Collects generated results',
  },
  {
    type: 'loop',
    label: 'Loop',
    targetHandle: null,
    make: (p) => createLoopNode({}, { position: p }) as CanvasNode,
    icon: Repeat2,
    descKey: 'canvas.dragCreate.desc.loop',
    descDefault: 'Controls rounds, batches and variables',
  },
  {
    type: 'timeline',
    label: 'Timeline',
    targetHandle: null,
    make: (p) => createTimelineNode({}, { position: p }) as CanvasNode,
    icon: Film,
    descKey: 'canvas.dragCreate.desc.timeline',
    descDefault: 'Multi-segment film, stitched in order',
  },
];

/**
 * Classic menu items that can LEGALLY receive a wire from the source output
 * (port type `srcOutType`): only node types with an input port of the matching
 * type, wired into THAT input (not blindly `inputs[0]`). Zero-input and
 * type-incompatible nodes are dropped so drag-create can't produce an edge the
 * connection validator would reject.
 */
function classicItems(srcOutType: string | undefined, all = false): MenuItem[] {
  const items: MenuItem[] = [];
  for (const def of CLASSIC_NODE_DEFINITIONS) {
    if (def.type === 'group') continue;
    const input = srcOutType
      ? def.inputs.find((i) => i.type === srcOutType)
      : undefined;
    if (!input && !all) continue;
    items.push({
      type: def.type,
      label: def.label,
      targetHandle: input?.id ?? null,
      make: (p) => ({
        id: `${def.type}-${crypto.randomUUID()}`,
        type: def.type,
        position: p,
        data: { label: def.label },
      }),
    });
  }
  return items;
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

  const items = useMemo<MenuItem[]>(() => {
    const srcNode = nodes.find(
      (n) => (n as { id?: string }).id === fromNodeId,
    ) as { type?: string } | undefined;
    const srcType = srcNode?.type;
    if (isSmartFamily(kind)) {
      // No origin (pane double-click / right-click, P1-1): every node type.
      if (!fromNodeId) return SMART_ITEMS;
      // Only node types the source may legally feed (excludes e.g. Shot as a
      // target and anything when the source is an Output).
      return SMART_ITEMS.filter((item) => canConnectSmart(srcType, item.type));
    }
    if (kind === 'classic') {
      if (!fromNodeId) return classicItems(undefined, true);
      const srcDef = getClassicNodeDefinition(srcType);
      const srcOutType = srcDef?.outputs.find((o) => o.id === fromHandle)?.type;
      return classicItems(srcOutType);
    }
    return [];
  }, [kind, nodes, fromNodeId, fromHandle]);

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
      // Wire back to the origin only when the menu came from a wire drop —
      // the pane double-click create (P1-1) drops a free-standing node.
      if (fromNodeId) {
        const edge: CanvasConnection = {
          id: `edge-${crypto.randomUUID()}`,
          source: fromNodeId,
          target: id,
          sourceHandle: fromHandle,
          targetHandle: item.targetHandle,
        };
        setConnections([...connections, edge]);
      }
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
        className={`mh-pop-in absolute z-50 rounded-xl border border-ink-700 bg-ink-900/95 shadow-xl backdrop-blur ${
          isSmartFamily(kind) ? 'w-[22rem] max-w-[90vw] p-1.5' : 'min-w-[9rem] p-1'
        }`}
        style={{ left: screenPosition.x, top: screenPosition.y }}
        onClick={(e) => e.stopPropagation()}
      >
        {items.length === 0 ? (
          <div className="px-2 py-1 text-xs text-ink-500">
            {t('canvas.dragCreate.noCompatible', 'No compatible node')}
          </div>
        ) : isSmartFamily(kind) ? (
          /* Infinite-style card grid: icon + title + one-line description. */
          <div className="grid grid-cols-2 gap-1">
            {items.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.type}
                  type="button"
                  role="menuitem"
                  onClick={() => create(item)}
                  className="flex items-start gap-2.5 rounded-lg p-2.5 text-left transition-colors hover:bg-ink-800"
                >
                  {Icon && (
                    <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-950/40 text-ink-300">
                      <Icon size={15} />
                    </span>
                  )}
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold text-ink-100">
                      {t(`canvas.dragCreate.node.${item.type}`, item.label)}
                    </span>
                    {item.descKey && (
                      <span className="mt-0.5 block text-[11px] leading-snug text-ink-500">
                        {t(item.descKey, item.descDefault ?? '')}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <>
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
          </>
        )}
      </div>
    </>
  );
}
