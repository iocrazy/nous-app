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
  /** See `CanvasViewProps.focusShotId` — threaded straight through once the
   *  canvas id has resolved. */
  focusShotId?: string | null;
  onFocusHandled?: () => void;
}

type ResolveState =
  | { status: 'loading' }
  | { status: 'ready'; canvasId: string }
  | { status: 'error' };

export function StoryboardCanvasEmbed({
  episodeId,
  teamId,
  focusShotId = null,
  onFocusHandled,
}: StoryboardCanvasEmbedProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<ResolveState>({ status: 'loading' });

  // Re-resolves on every episode-id change (switching episodes via the
  // sidebar ⇄ card while the Canvas tab is open must not leave the PREVIOUS
  // episode's canvas mounted). Idempotent get-or-create — safe to call on
  // every mount, matching the design's "lazy creation" rule (Task 1).
  useEffect(() => {
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
  }, [episodeId]);

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
      />
    </div>
  );
}

export default StoryboardCanvasEmbed;
