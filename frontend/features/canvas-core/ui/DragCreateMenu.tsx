/**
 * DragCreateMenu — the floating node picker shown when a wire is dropped into
 * empty canvas (drag-to-create, PR-C2).
 *
 * Lists the node types for the current canvas kind. Picking one creates the
 * node at the release point and auto-wires it from the origin handle — all
 * through the existing canvasCoreStore actions (setNodes / setConnections /
 * setSelection), never bypassing them. Escape or a backdrop click dismisses.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BotMessageSquare,
  Boxes,
  Clapperboard,
  Film,
  Group as GroupIcon,
  MonitorPlay,
  Repeat2,
  TextCursorInput,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react';
import { isSmartFamily } from '../types';
import { useTranslation } from 'react-i18next';

import { AssetPickerDialog } from '../smart/nodes/AssetPickerDialog';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { canConnectSmart } from '../smart/types';
import { createEmptyGroup } from '../smart/grouping';
import {
  createAssetNode,
  createLlmNode,
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
  /** Absent on `pick` items — see {@link MenuItem.pick}. */
  make?: (position: { x: number; y: number }) => CanvasNode;
  /** Asks the library which asset before creating anything. */
  pick?: true;
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
    type: 'group',
    label: 'Group',
    targetHandle: null,
    make: (p) => createEmptyGroup(p),
    icon: GroupIcon,
    descKey: 'canvas.dragCreate.desc.group',
    descDefault: 'Collect media, prompts and loops together',
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
    type: 'llm',
    label: 'LLM',
    targetHandle: null,
    make: (p) => createLlmNode({}, { position: p }) as CanvasNode,
    icon: BotMessageSquare,
    descKey: 'canvas.dragCreate.desc.llm',
    descDefault: 'Chat model in a node — text in, text out',
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
  // Asset-library reference (P4 Task 4). `pick` rather than `make`: which
  // asset it points at is a question only the library can answer, and the
  // seed for `selected_file_ids` needs the asset's detail row.
  {
    type: 'asset',
    label: 'Asset',
    targetHandle: null,
    pick: true,
    icon: Boxes,
    descKey: 'canvas.dragCreate.desc.asset',
    descDefault: 'Reference a character, location, prop or prompt',
  },
];

export function DragCreateMenu({
  screenPosition,
  flowPosition,
  fromNodeId,
  fromHandle,
  onClose,
}: DragCreateMenuProps) {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
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
      // Lite ("Smart") canvases keep IC's four-card menu: upload / group /
      // prompt / loop — the workflow-only nodes stay Standard-canvas tools.
      const items =
        kind === 'lite'
          ? SMART_ITEMS.filter((i) =>
              ['media', 'group', 'prompt', 'loop'].includes(i.type),
            )
          : SMART_ITEMS;
      // No origin (pane double-click / right-click, P1-1): every node type.
      if (!fromNodeId) return items;
      // Only node types the source may legally feed (excludes e.g. Shot as a
      // target and anything when the source is an Output).
      return items.filter((item) => canConnectSmart(srcType, item.type));
    }
    return [];
  }, [kind, nodes, fromNodeId]);

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

  const place = useCallback(
    (node: CanvasNode, targetHandle: string | null) => {
      const id = (node as Record<string, unknown>).id as string;
      // The LIVE lists, not this closure's: the asset picker resolves after a
      // round trip, and a stale snapshot would drop whatever arrived while
      // its detail fetch was in flight.
      const store = useCanvasCoreStore.getState();
      setNodes([...store.nodes, node]);
      // Wire back to the origin only when the menu came from a wire drop —
      // the pane double-click create (P1-1) drops a free-standing node.
      if (fromNodeId) {
        const edge: CanvasConnection = {
          id: `edge-${crypto.randomUUID()}`,
          source: fromNodeId,
          target: id,
          sourceHandle: fromHandle,
          targetHandle,
        };
        setConnections([...store.connections, edge]);
      }
      setSelection([id]);
      onClose();
    },
    [fromNodeId, fromHandle, setNodes, setConnections, setSelection, onClose],
  );

  const create = useCallback(
    (item: MenuItem) => {
      if (item.pick) {
        // The menu stays mounted behind the picker so its origin handle
        // survives; `place` is called from the picker's onPick instead.
        setPickerOpen(true);
        return;
      }
      if (!item.make) return;
      place(item.make(flowPosition), item.targetHandle);
    },
    [flowPosition, place],
  );

  return (
    <>
      {/* Backdrop — a click anywhere off the menu dismisses it. */}
      <div className="absolute inset-0 z-40" onClick={onClose} aria-hidden="true" />
      <div
        role="menu"
        aria-label={t('canvas.dragCreate.title', 'Add node')}
        className="mh-pop-in absolute z-50 w-[22rem] max-w-[90vw] rounded-xl border border-ink-700 bg-ink-900/95 p-1.5 shadow-xl backdrop-blur"
        style={{ left: screenPosition.x, top: screenPosition.y }}
        onClick={(e) => e.stopPropagation()}
      >
        {items.length === 0 ? (
          <div className="px-2 py-1 text-xs text-ink-500">
            {t('canvas.dragCreate.noCompatible', 'No compatible node')}
          </div>
        ) : (
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
        )}
      </div>
      {pickerOpen && (
        <AssetPickerDialog
          onPick={(asset) =>
            place(
              createAssetNode(asset, { position: flowPosition }) as CanvasNode,
              null,
            )
          }
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
