/**
 * StoryboardCanvasEmbed — the storyboard page's "Canvas" tab (shot-nodes-
 * on-canvas Task 5). Resolves the episode's system storyboard canvas
 * (idempotent get-or-create, Task 1's `GET /canvases/storyboard?episode_id=`)
 * and mounts `CanvasView` (extracted from the standalone `/canvas/:id` route
 * in this same task, see `CanvasPage.tsx`'s file doc comment) by `canvasId`
 * — the SAME reconcile/focus/composer orchestration as the real route, just
 * without React Router: no `onBack` (the page's own tabs are the way back),
 * and `focusShotId`/`onFocusHandled` thread straight through to `CanvasView`
 * for the three focus entry points (shot card click / `?view=canvas&shot=`
 * URL deep link / `shotFocusBus`) the storyboard page owns.
 *
 * Deliberately does NOT reuse `WorkspaceCanvas` (the sidebar "Canvas"
 * module's materials-canvas LIBRARY list) — that component is unrelated and
 * stays exactly as-is; this is a single specific canvas, not a picker.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loading } from '../../../components/common/Loading';
import { getOrCreateStoryboardCanvas } from '../services/canvasService';
import { CanvasView } from './CanvasPage';

export interface StoryboardCanvasEmbedProps {
  episodeId: string;
  teamId?: string;
  /**
   * Whether the storyboard page's Canvas tab is genuinely on screen (Task 7,
   * shot-nodes-on-canvas epic — keep-alive 显隐切换). Defaults to `true` (a
   * bare embed, or one whose caller doesn't care, behaves exactly as before
   * this prop existed — every current test in this file omits it).
   *
   * `CanvasView` owns a SINGLETON store (`canvasCoreStore`'s module-level
   * `useCanvasCoreStore`) — only one canvas can be "live" in it at a time.
   * Before Task 7, this embed only ever mounted while its host page was the
   * active module (an exclusive ternary in `ProjectWorkspace`), so `active`
   * was implicitly always true. Now that the storyboard PAGE is kept alive
   * (mounted-but-hidden across module switches), this embed would otherwise
   * keep resolving/rendering `CanvasView` — and therefore keep the store's
   * autosave debounce, `useCanvasRealtime` websocket subscription, and
   * shot-reconcile fetches — running indefinitely in the background for a
   * canvas nobody is looking at.
   *
   * Deliberately UNMOUNTS `CanvasView` while `active` is false rather than
   * merely pausing its internal effects: `CanvasView`'s own unmount cleanup
   * (`flushSave()` then `reset()`, guarded by `mountEpoch` — see that
   * component's doc comment) already correctly releases the singleton store,
   * so "go inactive" reuses that existing, already-hardened safety net
   * instead of teaching every one of `CanvasView`'s effects a second
   * "am I visible" branch. The trade-off: re-activating re-resolves the
   * canvas id and re-mounts `CanvasView` from scratch (a same-shape "Loading
   * canvas…" beat as a first-ever visit, including losing pan/zoom) — an
   * accepted cost given the store-singleton constraint, and out of scope for
   * this task's primary target (making the Storyboard/Shot-List views, and
   * the module switch itself, instant — see `EpisodeStoryboardPage`'s own
   * `active` prop doc comment).
   */
  active?: boolean;
  /** See `CanvasViewProps.focusShotId` — threaded straight through once the
   *  canvas id has resolved. */
  focusShotId?: string | null;
  onFocusHandled?: () => void;
  /** See `CanvasViewProps.reconcileRefreshToken` — threaded straight
   *  through once the canvas id has resolved. */
  reconcileRefreshToken?: number;
}

type ResolveState =
  | { status: 'loading' }
  | { status: 'ready'; canvasId: string }
  | { status: 'error' };

export function StoryboardCanvasEmbed({
  episodeId,
  teamId,
  active = true,
  focusShotId = null,
  onFocusHandled,
  reconcileRefreshToken,
}: StoryboardCanvasEmbedProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<ResolveState>({ status: 'loading' });

  // Re-resolves on every episode-id change (switching episodes via the
  // sidebar ⇄ card while the Canvas tab is open must not leave the PREVIOUS
  // episode's canvas mounted). Idempotent get-or-create — safe to call on
  // every mount, matching the design's "lazy creation" rule (Task 1).
  //
  // Task 7: skipped entirely while `active` is false — see that prop's doc
  // comment. Re-running once `active` flips back to true (it's in the dep
  // array) resets `state` to 'loading' first, so a re-activation never
  // renders a stale `ready` canvasId from before it went inactive, even for
  // one frame.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setState({ status: 'loading' });
    getOrCreateStoryboardCanvas(episodeId)
      .then((canvas) => {
        if (cancelled) return;
        setState({ status: 'ready', canvasId: canvas.id });
      })
      .catch((err) => {
        console.error('[StoryboardCanvasEmbed] failed to resolve storyboard canvas:', err);
        if (!cancelled) setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, [episodeId, active]);

  if (!active) {
    // Nothing rendered while inactive. If `state` was 'ready' with a live
    // `CanvasView` below, this UNMOUNTS it — releasing the singleton store
    // via ITS OWN flushSave+reset cleanup (see the `active` prop's doc
    // comment on `StoryboardCanvasEmbedProps` for why that's the right
    // safety boundary rather than teaching `CanvasView` a second
    // visibility-aware branch).
    return null;
  }

  if (state.status === 'loading') {
    return (
      <div data-testid="storyboard-canvas-embed-loading" className="flex justify-center py-10">
        <Loading center />
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div
        data-testid="storyboard-canvas-embed-error"
        className="rounded-lg border border-dashed border-line bg-island-2/40 px-6 py-10 text-center text-sm text-content-2"
      >
        {t('projects.storyboardPage.canvasLoadError', 'Failed to load the storyboard canvas.')}
      </div>
    );
  }

  return (
    <div data-testid="storyboard-canvas-embed" className="relative h-[70vh] min-h-[28rem] w-full">
      <CanvasView
        canvasId={state.canvasId}
        teamId={teamId}
        focusShotId={focusShotId}
        onFocusHandled={onFocusHandled}
        reconcileRefreshToken={reconcileRefreshToken}
      />
    </div>
  );
}

export default StoryboardCanvasEmbed;
