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
import { useTranslation } from 'react-i18next';
import {
  BotMessageSquare,
  Boxes,
  Clapperboard,
  Film,
  ImagePlus,
  Library,
  Loader2,
  MonitorPlay,
  Repeat2,
  TextCursorInput,
  UploadCloud,
  Video,
  type LucideIcon,
} from 'lucide-react';

import { useOptionalToast } from '../../../components/Toast';
import { listProjectAssets } from '../../../services/assetsService';
import { AssetPickerDialog } from '../smart/nodes/AssetPickerDialog';
import { buildProjectAssetNodes } from '../smart/assetPlacement';
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
  /** English default. The rendered text is `canvas.nodeBar.<key>`, resolved
   *  with this as the fallback — the same idiom `DragCreateMenu` uses for its
   *  cards, so the two entry points cannot drift apart in one locale. */
  label: string;
  icon: LucideIcon;
  /** Chips that create a node outright. Absent on `pick` chips, which have
   *  to ask the library which asset first. */
  make?: (position: { x: number; y: number }) => CanvasNode;
  /** Opens the asset picker instead of creating immediately. */
  pick?: true;
  /** Pulls the whole project shelf in at once — asks the server, then places
   *  many nodes. Not a `make`: the answer is a request, not arithmetic. */
  bulk?: true;
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
  // The whole project shelf at once (P4 Task 6) — the bulk sibling of the
  // chip above. It sits here rather than in a menu of its own because what it
  // does IS what this strip does: put nodes on the canvas.
  { key: 'project-assets', label: 'Project Assets', icon: Library, bulk: true },
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
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);
  const projectId = useCanvasCoreStore((s) => s.projectId);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [inserting, setInserting] = useState(false);

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

  /**
   * Insert Project Assets — every asset linked to this canvas's project, in
   * four lanes (character / location / prop / costume, plus a lane each for
   * prompt and audio when the project has them).
   *
   * EVERY outcome says something. A project with no assets, a project whose
   * assets are all on the board already, and a failed request are three
   * different answers and each gets its own line — an action that sometimes
   * places nothing and reports nothing is indistinguishable from a broken
   * button, which is the silent no-op this repo keeps re-learning.
   */
  const insertProjectAssets = useCallback(() => {
    if (inserting) return;
    if (!projectId) {
      toast?.addToast(
        t('canvas.projectAssets.noProject', 'This canvas has no project'),
        'error',
      );
      return;
    }
    setInserting(true);
    listProjectAssets(projectId)
      .then((assets) => {
        if (assets.length === 0) {
          toast?.addToast(
            t('canvas.projectAssets.empty', 'This project has no assets yet'),
            'info',
          );
          return;
        }
        // Read the LIVE node list, not the closure's — the request took time
        // and anything created meanwhile must survive.
        const current = useCanvasCoreStore.getState().nodes;
        const plan = buildProjectAssetNodes(assets, current);
        if (plan.inserted === 0) {
          toast?.addToast(
            t('canvas.projectAssets.allPresent', 'Every project asset is already here'),
            'info',
          );
          return;
        }
        setNodes([...current, ...plan.nodes]);
        setSelection(plan.nodes.map((node) => String((node as { id?: unknown }).id)));
        toast?.addToast(
          plan.skipped > 0
            ? t('canvas.projectAssets.insertedWithSkipped', {
                inserted: plan.inserted,
                skipped: plan.skipped,
                defaultValue: 'Added {{inserted}} · {{skipped}} already here',
              })
            : t('canvas.projectAssets.inserted', {
                inserted: plan.inserted,
                defaultValue: 'Added {{inserted}}',
              }),
          'success',
        );
      })
      .catch((err) => {
        console.error('[TopNodeBar] listProjectAssets failed:', err);
        toast?.addToast(
          t('canvas.projectAssets.failed', 'Could not load the project assets'),
          'error',
        );
      })
      .finally(() => setInserting(false));
  }, [inserting, projectId, toast, t, setNodes, setSelection]);

  const addAtCenter = useCallback(
    (chip: Chip) => {
      if (chip.pick) {
        setPickerOpen(true);
        return;
      }
      if (chip.bulk) {
        insertProjectAssets();
        return;
      }
      if (!chip.make) return;
      append(chip.make(centreInWorld()));
    },
    [append, centreInWorld, insertProjectAssets],
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
        const busy = chip.bulk === true && inserting;
        return (
          <button
            key={chip.key}
            type="button"
            data-testid={`top-node-chip-${chip.key}`}
            disabled={busy}
            onClick={() => addAtCenter(chip)}
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-canvas-line bg-canvas-card/60 px-3 py-1.5 text-[11px] font-medium text-canvas-text transition-colors hover:border-[var(--accent-border)] hover:text-[var(--accent-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Icon size={12} />}
            {t(`canvas.nodeBar.${chip.key}`, chip.label)}
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
