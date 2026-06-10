/**
 * Optimistic-lock conflict dialog (Phase 1 Week 3 surface — landed here
 * with the React Flow PR because the surface is useless without it).
 *
 * Shown when the canvas store's `conflict` field is populated (i.e. a
 * PUT returned 409). Two user actions:
 *
 *   - Adopt server: discard local edits since the last save, take the
 *     server row, resume auto-save.
 *   - Keep mine:   dismiss the conflict UI; the user owns the next save.
 *                  The store's `revision` is still ahead of
 *                  `persistedRevision`, but auto-save stays frozen
 *                  because `conflict` is null and saveStatus is idle —
 *                  the next mutation will trigger a new save with the
 *                  stale token, which will 409 again and re-open this
 *                  dialog. (That's intentional: we don't fake a
 *                  successful merge.)
 */

import { useCanvasCoreStore } from '../store/canvasCoreStore';

export function CanvasConflictDialog() {
  const conflict = useCanvasCoreStore((s) => s.conflict);
  const resolveWithServer = useCanvasCoreStore(
    (s) => s.resolveConflictWithServer,
  );
  const dismiss = useCanvasCoreStore((s) => s.dismissConflict);

  if (!conflict) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="canvas-conflict-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
      <div className="w-[420px] max-w-[90vw] rounded-lg bg-white p-6 shadow-xl dark:bg-slate-900">
        <h2
          id="canvas-conflict-title"
          className="text-lg font-semibold text-slate-900 dark:text-slate-100"
        >
          Canvas updated by someone else
        </h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          The server has a newer version of this canvas than the one you have
          open. Pick which copy to keep — there is no auto-merge.
        </p>

        <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
          <dt>Their last edit</dt>
          <dd>{conflict.base_updated_at}</dd>
          <dt>Their node count</dt>
          <dd>{conflict.nodes_json.length}</dd>
        </dl>

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={dismiss}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Keep mine
          </button>
          <button
            type="button"
            onClick={resolveWithServer}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Adopt server
          </button>
        </div>
      </div>
    </div>
  );
}
