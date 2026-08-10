/**
 * Undo state for one agent run — drives the Undo button in TurnWriteSummary.
 *
 * ── Why this is its own hook, not a ride on useRunToolActivity ─────────────
 * Spec §5 says "undone_at rides the run data into useRunToolActivity's data
 * flow." That would be the tidier design, but it doesn't fit the two real
 * mount points:
 *   - The button's only surface is the chat panel's AIChatBubble, which
 *     deliberately does NOT call useRunToolActivity (it would render the
 *     trace twice — see that hook's own header comment).
 *   - The issue timeline DOES call useRunToolActivity, but with
 *     interactive=false, i.e. no button there at all.
 * So there is no live call site where both hooks would ever run together —
 * threading undone_at through useRunToolActivity would add a data path
 * nothing reads. This hook is deliberately parallel to it instead: same
 * "fetch once, cache module-wide" shape as useRunToolActivity's settledCache,
 * same test-clear escape hatch, kept separate so neither surface's division
 * of labour changes.
 */

import { useCallback, useEffect, useState } from 'react';

import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentRunUndoReport } from '../../types';
import { requestStoryboardRefresh } from './shotFocusBus';

export type RunUndoState = 'loading' | 'ready' | 'busy' | 'undone' | 'hidden';

/** runId -> already undone, for runs whose undone-ness we've already resolved. */
const undoneCache = new Map<string, boolean>();

/** Exposed for tests — module-level caches otherwise leak between cases. */
export function __clearRunUndoCache(): void {
  undoneCache.clear();
}

export interface UseRunUndoResult {
  state: RunUndoState;
  report: AgentRunUndoReport | null;
  undo: () => Promise<void>;
}

export function useRunUndo(
  runId: string | null | undefined,
  enabled: boolean,
): UseRunUndoResult {
  const [state, setState] = useState<RunUndoState>(() => {
    if (!enabled || !runId) return 'hidden';
    const cached = undoneCache.get(runId);
    if (cached === true) return 'undone';
    if (cached === false) return 'ready';
    return 'loading';
  });
  const [report, setReport] = useState<AgentRunUndoReport | null>(null);

  useEffect(() => {
    if (!enabled || !runId) {
      setState('hidden');
      return;
    }

    const cached = undoneCache.get(runId);
    if (cached !== undefined) {
      setState(cached ? 'undone' : 'ready');
      return;
    }

    let cancelled = false;
    setState('loading');

    aiLibraryService
      .getRun(runId)
      .then((run) => {
        if (cancelled) return;
        const undone = Boolean(run.undone_at);
        undoneCache.set(runId, undone);
        setState(undone ? 'undone' : 'ready');
      })
      .catch((err) => {
        // A run owned by another user (or already gone) reads as 404 — no
        // button beats a broken one, so it just hides rather than erroring.
        console.error('[useRunUndo] failed to load run:', err);
        if (!cancelled) setState('hidden');
      });

    return () => {
      cancelled = true;
    };
  }, [runId, enabled]);

  const undo = useCallback(async () => {
    if (!runId) return;
    setState('busy');
    try {
      const result = await aiLibraryService.undoRun(runId);
      setReport(result);
      undoneCache.set(runId, true);
      setState('undone');
      requestStoryboardRefresh();
    } catch (err) {
      console.error('[useRunUndo] undo failed:', err);
      setState('ready');
    }
  }, [runId]);

  return { state, report, undo };
}
