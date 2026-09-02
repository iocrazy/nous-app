/**
 * TopNodeBar — the Standard canvas's top node strip (IC 普通画布's 顶栏
 * 节点条, dual-canvas Phase 2.2): one chip per node type, click drops the
 * node at the viewport centre. Image Gen / Video Gen chips create a
 * Prompt node pre-set to that generation kind (our equivalent of IC's
 * API生成/视频生成 nodes). Standard (kind='smart') canvases only — the
 * lite canvas keeps its four-card create menu, entity canvases keep
 * their preset workflows.
 */

import { useCallback, useState } from 'react';
import {
  BotMessageSquare,
  Boxes,
  Clapperboard,
  Film,
  ImagePlus,
  MonitorPlay,
  Repeat2,
  TextCursorInput,
  UploadCloud,
  Video,
  type LucideIcon,
} from 'lucide-react';

import { AssetPickerDialog } from '../smart/nodes/AssetPickerDialog';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import type { CanvasNode } from '../types';
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

interface Chip {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Chips that create a node outright. Absent on `pick` chips, which have
   *  to ask the library which asset first. */
  make?: (position: { x: number; y: number }) => CanvasNode;
  /** Opens the asset picker instead of creating immediately. */
  pick?: true;
}

const CHIPS: Chip[] = [
  { key: 'media', label: 'Upload', icon: UploadCloud, make: (p) => createMediaNode({}, { position: p }) as CanvasNode },
  { key: 'shot', label: 'Shot', icon: Clapperboard, make: (p) => createShotNode({}, { position: p }) as CanvasNode },
  { key: 'prompt', label: 'Prompt', icon: TextCursorInput, make: (p) => createPromptNode({}, { position: p }) as CanvasNode },
  { key: 'llm', label: 'LLM', icon: BotMessageSquare, make: (p) => createLlmNode({}, { position: p }) as CanvasNode },
  {
    key: 'image-gen',
    label: 'Image Gen',
    icon: ImagePlus,
    make: (p) =>
      createPromptNode(
        { gen: { kind: 'image', model: '' } },
        { position: p },
      ) as CanvasNode,
  },
  {
    key: 'video-gen',
    label: 'Video Gen',
    icon: Video,
    make: (p) =>
      createPromptNode(
        { gen: { kind: 'video', model: '' } },
        { position: p },
      ) as CanvasNode,
  },
  { key: 'loop', label: 'Loop', icon: Repeat2, make: (p) => createLoopNode({}, { position: p }) as CanvasNode },
  { key: 'timeline', label: 'Timeline', icon: Film, make: (p) => createTimelineNode({}, { position: p }) as CanvasNode },
  { key: 'output', label: 'Output', icon: MonitorPlay, make: (p) => createOutputNode({}, { position: p }) as CanvasNode },
  // Asset-library reference (P4 Task 4). No `make`: which asset is a
  // question only the library can answer, so this one opens the picker.
  { key: 'asset', label: 'Asset', icon: Boxes, pick: true },
];

export interface TopNodeBarProps {
  surfaceRef: React.RefObject<HTMLDivElement | null>;
}

export function TopNodeBar({ surfaceRef }: TopNodeBarProps) {
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const [pickerOpen, setPickerOpen] = useState(false);

  const centreInWorld = useCallback(() => {
    const rect = surfaceRef.current?.getBoundingClientRect();
    const screenCenter = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: 0, y: 0 };
    return screenToWorld(screenCenter, viewport);
  }, [surfaceRef, viewport]);

  const append = useCallback(
    (node: CanvasNode) => {
      // Read the LIVE list rather than the closure's: the picker resolves
      // asynchronously, and a stale `nodes` would drop anything created
      // while its detail fetch was in flight.
      const current = useCanvasCoreStore.getState().nodes;
      setNodes([...current, node]);
      setSelection([String((node as { id?: unknown }).id)]);
    },
    [setNodes, setSelection],
  );

  const addAtCenter = useCallback(
    (chip: Chip) => {
      if (chip.pick) {
        setPickerOpen(true);
        return;
      }
      if (!chip.make) return;
      append(chip.make(centreInWorld()));
    },
    [append, centreInWorld],
  );

  return (
    <div
      role="toolbar"
      aria-label="Canvas node bar"
      data-testid="top-node-bar"
      className="canvas-island pointer-events-auto absolute left-1/2 top-4 z-30 flex max-w-[calc(100%-24rem)] -translate-x-1/2 gap-1 overflow-x-auto p-1.5"
    >
      {CHIPS.map((chip) => {
        const Icon = chip.icon;
        return (
          <button
            key={chip.key}
            type="button"
            onClick={() => addAtCenter(chip)}
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-canvas-line bg-canvas-card/60 px-3 py-1.5 text-[11px] font-medium text-canvas-text transition-colors hover:border-[var(--accent-border)] hover:text-[var(--accent-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
          >
            <Icon size={12} />
            {chip.label}
          </button>
        );
      })}
      {pickerOpen && (
        <AssetPickerDialog
          onPick={(asset) =>
            append(createAssetNode(asset, { position: centreInWorld() }) as CanvasNode)
          }
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

export default TopNodeBar;
