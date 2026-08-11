/**
 * PromoteShotDialog — the scene picker for an unbound shot node's "Promote
 * to Shot" menu entry (shot-nodes-on-canvas epic, Task 4).
 *
 * `promoteShotBus`'s `requestPromoteShot(nodeId)` only asks; `CanvasPage.tsx`
 * owns the actual binding flow and mounts this dialog to collect the one
 * thing `POST /scenes/{scene_id}/shots` requires that the node itself
 * doesn't carry — which scene the new `script_shots` row belongs to.
 * Clicking a scene row submits immediately (no separate scene-then-confirm
 * step — matches "scene 必选" simply: there's nothing else to configure).
 */

import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { SceneDoc } from '../../../editor/types';

/** `INT/EXT · Location · TIME`, empty parts dropped — same composition as
 *  `StoryboardView.tsx`'s `headingLine` so a scene reads identically in
 *  both places. */
function headingLine(scene: SceneDoc): string {
  return [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
}

export interface PromoteShotDialogProps {
  open: boolean;
  /** Scenes in display order (index 0 = column 0 / "1" in labels). */
  scenes: SceneDoc[];
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onPickScene: (sceneId: string) => void;
}

export function PromoteShotDialog({
  open,
  scenes,
  submitting,
  error,
  onCancel,
  onPickScene,
}: PromoteShotDialogProps) {
  const { t } = useTranslation();
  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="promote-shot-title"
      data-testid="promote-shot-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !submitting) onCancel();
      }}
    >
      <div className="w-[320px] max-w-full rounded-2xl border border-canvas-line bg-canvas-surface p-5 shadow-2xl">
        <h2 id="promote-shot-title" className="text-sm font-semibold text-canvas-text">
          {t('canvas.shotNode.promoteDialog.title')}
        </h2>
        <p className="mt-1 text-xs text-canvas-muted">
          {t('canvas.shotNode.promoteDialog.hint')}
        </p>

        {scenes.length === 0 ? (
          <p className="mt-4 text-xs text-canvas-muted" data-testid="promote-shot-no-scenes">
            {t('canvas.shotNode.promoteDialog.noScenes')}
          </p>
        ) : (
          <div className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-canvas-line">
            {scenes.map((scene, idx) => (
              <button
                key={scene.id}
                type="button"
                disabled={submitting}
                data-testid="promote-shot-scene-option"
                onClick={() => onPickScene(scene.id)}
                className="flex w-full items-center gap-2 border-b border-canvas-line px-3 py-2 text-left text-xs text-canvas-text last:border-b-0 hover:bg-canvas-line/30 disabled:opacity-60"
              >
                <span className="mh-accent-chip shrink-0 rounded-full px-1.5 py-0.5 text-[10px]">
                  {idx + 1}
                </span>
                <span className="truncate">{headingLine(scene) || scene.id}</span>
              </button>
            ))}
          </div>
        )}

        {error && (
          <p className="mt-3 text-xs text-danger" data-testid="promote-shot-error">
            {error}
          </p>
        )}

        <div className="mt-4 flex gap-2">
          {submitting && (
            <span className="flex items-center gap-1.5 text-xs text-canvas-muted">
              <Loader2 size={14} className="animate-spin" />
            </span>
          )}
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="ml-auto rounded-lg border border-canvas-line px-3 py-2 text-sm text-canvas-text transition-colors hover:bg-canvas-line/30 disabled:opacity-60"
          >
            {t('canvas.shotNode.promoteDialog.cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default PromoteShotDialog;
