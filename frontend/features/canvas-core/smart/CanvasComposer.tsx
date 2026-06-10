/**
 * Smart-mode composer (Phase 2 of canvas + AI upgrade).
 *
 * Bottom toolbar with "Add Shot / Prompt / Output" buttons. Run /
 * Cascade Run land in the next PR once the run protocol is wired.
 *
 * New nodes are placed in the centre of the current viewport — the
 * user can drag them anywhere after creation. The store's existing
 * mutation pipeline (setNodes) takes care of history + save.
 */

import { useCallback } from 'react';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import {
  createOutputNode,
  createPromptNode,
  createShotNode,
} from './factories';

interface CanvasComposerOptions {
  /** When passed, new nodes are positioned at the centre of this DOM
   *  rect (translated into world coords). Falls back to viewport
   *  origin when not provided — useful for tests / headless renders. */
  surfaceRef?: React.RefObject<HTMLElement | null>;
}

export function CanvasComposer({ surfaceRef }: CanvasComposerOptions = {}) {
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);

  const dropPosition = useCallback((): { x: number; y: number } => {
    const rect = surfaceRef?.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    const screenCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
    return screenToWorld(screenCenter, viewport);
  }, [surfaceRef, viewport]);

  const addNode = useCallback(
    (kind: 'shot' | 'prompt' | 'output') => {
      const position = dropPosition();
      const node =
        kind === 'shot'
          ? createShotNode({}, { position })
          : kind === 'prompt'
            ? createPromptNode({}, { position })
            : createOutputNode({}, { position });
      setNodes([...nodes, node]);
      setSelection([node.id]);
    },
    [dropPosition, nodes, setNodes, setSelection],
  );

  return (
    <div
      role="toolbar"
      aria-label="Smart canvas composer"
      className="pointer-events-auto absolute inset-x-0 bottom-4 mx-auto flex w-fit gap-1 rounded-md border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
    >
      <ComposerButton onClick={() => addNode('shot')}>+ Shot</ComposerButton>
      <ComposerButton onClick={() => addNode('prompt')}>+ Prompt</ComposerButton>
      <ComposerButton onClick={() => addNode('output')}>+ Output</ComposerButton>
    </div>
  );
}

function ComposerButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
    >
      {children}
    </button>
  );
}
