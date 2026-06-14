/**
 * ClassicMode node palette (Phase 5a — the "add nodes from UI" last mile).
 *
 * ClassicMode ships a fully-wired cascade engine + run bar (#711) but no
 * way to ADD nodes to the graph through the UI. This overlay is that last
 * mile, mirroring SmartMode's `CanvasComposer`: an add-button per classic
 * node type that drops a fresh node onto the live store graph and selects
 * it.
 *
 * Layout: the palette is the TOP-LEFT overlay; the run bar (`ClassicRunBar`)
 * is the bottom-center overlay, so the two never overlap.
 *
 * MVP scope: just click-to-add at a cascading offset (matching the
 * composer's simplest approach — no drag-from-palette). The node's typed
 * ports come from the registry at render time, so they are NOT stored in
 * `data`; only a display `label` is seeded. Node-data CONFIG editing is an
 * explicit LATER slice and intentionally NOT built here.
 */

import { useCallback, useRef } from 'react';
import { Plus } from 'lucide-react';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { screenToWorld } from '../../utils/viewport';
import { CLASSIC_NODE_DEFINITIONS } from '../registry';

interface ClassicPaletteProps {
  /** When passed, new nodes drop at the centre of this DOM rect (translated
   *  into world coords). Falls back to the viewport origin otherwise —
   *  useful for tests / headless renders. */
  surfaceRef?: React.RefObject<HTMLElement | null>;
}

/** Each add nudges the next drop down-right so repeated adds don't stack
 *  exactly on top of one another (a small ComfyUI-style cascade). */
const CASCADE_STEP = 28;

export function ClassicPalette({ surfaceRef }: ClassicPaletteProps = {}) {
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  // Monotonic cascade counter — bumped on each add so positions fan out.
  const cascadeRef = useRef(0);

  const dropPosition = useCallback((): { x: number; y: number } => {
    const offset = cascadeRef.current * CASCADE_STEP;
    cascadeRef.current += 1;
    const rect = surfaceRef?.current?.getBoundingClientRect();
    if (!rect) return { x: offset, y: offset };
    const screenCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
    const world = screenToWorld(screenCenter, viewport);
    return { x: world.x + offset, y: world.y + offset };
  }, [surfaceRef, viewport]);

  const addNode = useCallback(
    (type: string, label: string) => {
      const node: CanvasNode = {
        id: `${type}-${crypto.randomUUID()}`,
        type,
        position: dropPosition(),
        data: { label },
      };
      setNodes([...nodes, node]);
      setSelection([node.id as string]);
    },
    [dropPosition, nodes, setNodes, setSelection],
  );

  return (
    <div
      role="toolbar"
      aria-label="Classic canvas palette"
      className="pointer-events-auto absolute left-4 top-4 flex w-fit max-w-[12rem] flex-wrap gap-1 rounded-lg border border-ink-700 bg-ink-900/90 p-1.5 shadow-lg backdrop-blur"
    >
      {CLASSIC_NODE_DEFINITIONS.map((def) => (
        <PaletteButton
          key={def.type}
          label={def.label}
          onClick={() => addNode(def.type, def.label)}
        />
      ))}
    </div>
  );
}

function PaletteButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Add ${label}`}
      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-200 hover:bg-ink-800 hover:text-ink-50"
    >
      <Plus size={13} aria-hidden="true" />
      {label}
    </button>
  );
}
