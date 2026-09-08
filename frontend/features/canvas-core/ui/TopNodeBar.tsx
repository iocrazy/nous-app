/**
 * TopNodeBar — the Standard canvas's top node strip (IC 普通画布's 顶栏
 * 节点条, dual-canvas Phase 2.2): one chip per node type, click drops the
 * node at the viewport centre. Image Gen / Video Gen chips create a
 * Prompt node pre-set to that generation kind (our equivalent of IC's
 * API生成/视频生成 nodes).
 *
 * Shown on the Standard canvas AND on the four entity boards (character /
 * location / prop / costume). The entity kinds were excluded while they seeded
 * a preset workflow and had nothing to add; P4 deleted those templates, and a
 * hand-made entity board then opened blank with no visible way to add a node.
 *
 * `lite` is still excluded, on its own terms: it deliberately offers a
 * four-card create menu rather than the full node set (`DragCreateMenu`
 * filters `SMART_ITEMS` down to upload / group / prompt / loop), and putting
 * eleven chips above it would hand back exactly what that menu withholds.
 */

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BotMessageSquare,
  Clapperboard,
  Film,
  ImagePlus,
  Library,
  MonitorPlay,
  Repeat2,
  TextCursorInput,
  UploadCloud,
  Video,
  type LucideIcon,
} from 'lucide-react';

import { useLibraryStore } from '../library/libraryStore';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import type { CanvasNode } from '../types';
import {
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
  /** English default. The rendered text is `canvas.nodeBar.<key>`, resolved
   *  with this as the fallback — the same idiom `DragCreateMenu` uses for its
   *  cards, so the two entry points cannot drift apart in one locale. */
  label: string;
  icon: LucideIcon;
  /** Chips that create a node outright. Absent on the `panel` chip, which
   *  has to ask the library what to place first. */
  make?: (position: { x: number; y: number }) => CanvasNode;
  /** Opens the Library panel instead of creating anything. */
  panel?: true;
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
  // ONE Library chip where Asset and Project Assets used to be. Both did the
  // same thing at different granularities — pick one asset, or take the whole
  // project shelf — and the panel expresses that as a scope plus a selection,
  // which is a knob rather than a second button.
  { key: 'library', label: 'Library', icon: Library, panel: true },
];

/**
 * The chip keys, in bar order — exported so the i18n parity test enumerates
 * them from THIS array rather than a hand-kept copy. The labels are addressed
 * by a runtime-built key (`canvas.nodeBar.${key}`), so no literal exists in
 * this file for a grep to find.
 */
export const NODE_BAR_CHIP_KEYS: readonly string[] = CHIPS.map((c) => c.key);

export interface TopNodeBarProps {
  surfaceRef: React.RefObject<HTMLDivElement | null>;
}

export function TopNodeBar({ surfaceRef }: TopNodeBarProps) {
  // The store's viewport is SETTLED state since Task 3 (React Flow owns the
  // transform mid-gesture and reports it back on `onMoveEnd`). That is the
  // right source here: a chip click is a discrete action at rest, so the last
  // settled value IS the current one. The same reasoning applies to
  // `CanvasComposer.dropPosition` — both readers sit outside React Flow's
  // provider, so `useViewport()` is not available to either of them anyway.
  const { t } = useTranslation();
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const centreInWorld = useCallback(() => {
    const rect = surfaceRef.current?.getBoundingClientRect();
    const screenCenter = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: 0, y: 0 };
    return screenToWorld(screenCenter, viewport);
  }, [surfaceRef, viewport]);

  const append = useCallback(
    (node: CanvasNode) => {
      // Read the LIVE list rather than the closure's: a stale `nodes` would
      // drop anything created while this render was on screen.
      const current = useCanvasCoreStore.getState().nodes;
      setNodes([...current, node]);
      setSelection([String((node as { id?: unknown }).id)]);
    },
    [setNodes, setSelection],
  );

  const addAtCenter = useCallback(
    (chip: Chip) => {
      if (chip.panel) {
        useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'assets' });
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
      // Centred by `mx-auto` inside a band that ends where the Library
      // panel begins — a `-translate-x-1/2` centres on the full width by
      // definition, which no reservation can reach. The 24rem keeps clear
      // of the back pill and the save badge, as before.
      className="canvas-island pointer-events-auto absolute left-0 right-[var(--canvas-inset-right,0px)] top-4 z-30 mx-auto flex w-fit max-w-[calc(100%_-_var(--canvas-inset-right,0px)_-_24rem)] gap-1 overflow-x-auto p-1.5"
    >
      {CHIPS.map((chip) => {
        const Icon = chip.icon;
        return (
          <button
            key={chip.key}
            type="button"
            data-testid={`top-node-chip-${chip.key}`}
            onClick={() => addAtCenter(chip)}
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-canvas-line bg-canvas-card/60 px-3 py-1.5 text-[11px] font-medium text-canvas-text transition-colors hover:border-[var(--accent-border)] hover:text-[var(--accent-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-border)]"
          >
            <Icon size={12} />
            {t(`canvas.nodeBar.${chip.key}`, chip.label)}
          </button>
        );
      })}
    </div>
  );
}

export default TopNodeBar;
