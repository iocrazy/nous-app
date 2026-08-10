/**
 * EpisodeSceneBoard — view one ("Storyboard") of the episode-node three-view
 * primary work surface (SDD 2026-08-09 Task 1). A vertical stream of scene
 * cards: each carries the scene's real slate metadata (scene_number / I-E /
 * location / time-of-day), an "Open" deep-link into the script editor, an
 * "Auto Storyboard" two-click-confirm dispatch, and a simplified read-only
 * shot list.
 *
 * The confirm+poll dispatch mirrors StoryboardView:153-200 — the LOGIC is
 * ported here, not imported, per the plan's workspace/editor design-language
 * split (this surface uses the content / island / line / accent token family,
 * never the editor's local --ink or --surface CSS, nor its mh-sb- classes).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { autoStoryboard, listShots } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';
import { usePoll } from '../../editor/usePoll';
import { useSceneShots } from './useSceneShots';

/** How long an armed Auto Storyboard "Confirm?" stays live before auto-disarming. */
const CONFIRM_WINDOW_MS = 3000;

export interface EpisodeSceneBoardProps {
  scriptId: string;
  /** Deep-link into the scene (opens the script editor there). */
  onOpenScene: (sceneId: string) => void;
}

/** Slate scene-number read-out: the real value, or a 1-based fallback. */
function sceneNumberLabel(scene: SceneDoc, idx: number): string {
  return scene.scene_number ?? String(idx + 1);
}

export function EpisodeSceneBoard({ scriptId, onOpenScene }: EpisodeSceneBoardProps) {
  const { t } = useTranslation();
  const { startPoll } = usePoll();
  const { scenes, shotsByScene, setShotsForScene } = useSceneShots(scriptId);

  const [autoBusy, setAutoBusy] = useState<Record<string, boolean>>({});
  const [confirmingAuto, setConfirmingAuto] = useState<string | null>(null);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const runAuto = useCallback(
    async (sceneId: string) => {
      const key = String(sceneId);
      const baseline = (shotsByScene[key] ?? []).length;
      setAutoBusy((prev) => ({ ...prev, [key]: true }));
      try {
        await autoStoryboard(sceneId);
      } catch (err) {
        console.error('[EpisodeSceneBoard] autoStoryboard dispatch failed', err);
        setAutoBusy((prev) => ({ ...prev, [key]: false }));
        return;
      }
      startPoll(
        async () => {
          const shots = await listShots(sceneId);
          if (shots.length > baseline) {
            setShotsForScene(sceneId, shots);
            return true;
          }
          return false;
        },
        () => setAutoBusy((prev) => ({ ...prev, [key]: false })),
      );
    },
    [shotsByScene, setShotsForScene, startPoll],
  );

  // Inline confirm for Auto Storyboard: first click arms, second executes.
  const handleAutoClick = useCallback(
    (sceneId: string) => {
      const key = String(sceneId);
      if (autoBusy[key]) return;
      if (confirmingAuto === key) {
        if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
        confirmTimerRef.current = null;
        setConfirmingAuto(null);
        void runAuto(sceneId);
        return;
      }
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      setConfirmingAuto(key);
      confirmTimerRef.current = setTimeout(() => {
        confirmTimerRef.current = null;
        setConfirmingAuto(null);
      }, CONFIRM_WINDOW_MS);
    },
    [autoBusy, confirmingAuto, runAuto],
  );

  return (
    <div className="flex flex-col gap-4" data-testid="episode-scene-board">
      {scenes.map((sceneDoc, idx) => {
        const key = String(sceneDoc.id);
        const shots = shotsByScene[key] ?? [];
        const busy = !!autoBusy[key];
        const confirming = confirmingAuto === key;
        const hasContent = sceneDoc.elements.length > 0;
        const num = sceneNumberLabel(sceneDoc, idx);

        return (
          <div
            key={key}
            data-testid={`ep-scene-card-${key}`}
            className="rounded-lg border border-line bg-island overflow-hidden"
          >
            {/* Clap-stripe header — decorative slate marker, theme-aware via --line-strong. */}
            <div
              className="h-3"
              aria-hidden="true"
              style={{
                background:
                  'repeating-linear-gradient(45deg, var(--line-strong) 0, var(--line-strong) 10px, transparent 10px, transparent 20px)',
              }}
            />

            <div className="p-4">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 mb-3">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-content-3">
                    {t('projects.sceneBoard.sceneLabel')}
                  </div>
                  <div className="text-sm font-medium text-content">{num}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-content-3">
                    {t('projects.sceneBoard.ieLabel')}
                  </div>
                  <div className="text-sm font-medium text-content">
                    {sceneDoc.heading_int_ext ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-content-3">
                    {t('projects.sceneBoard.locationLabel')}
                  </div>
                  <div className="text-sm font-medium text-content">
                    {sceneDoc.location_text ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-content-3">
                    {t('projects.sceneBoard.dayNightLabel')}
                  </div>
                  <div className="text-sm font-medium text-content">
                    {sceneDoc.time_of_day ?? '—'}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 mb-3">
                <button
                  type="button"
                  data-testid={`ep-scene-open-${key}`}
                  onClick={() => onOpenScene(sceneDoc.id)}
                  className="rounded-md border border-line px-3 py-1.5 text-[13px] font-medium text-content hover:bg-island-2"
                >
                  {t('projects.sceneBoard.open')}
                </button>
                <button
                  type="button"
                  data-testid={`ep-scene-auto-${key}`}
                  disabled={busy}
                  aria-busy={busy || undefined}
                  onClick={() => handleAutoClick(sceneDoc.id)}
                  className={`rounded-md border px-3 py-1.5 text-[13px] font-medium disabled:opacity-50 ${
                    confirming
                      ? 'border-warn-line bg-warn-soft text-warn'
                      : 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)] hover:opacity-90'
                  }`}
                >
                  {busy
                    ? t('projects.sceneBoard.autoStoryboardBusy')
                    : confirming
                      ? t('projects.sceneBoard.confirmAuto')
                      : t('projects.sceneBoard.autoStoryboard')}
                </button>
              </div>

              <div data-testid={`ep-scene-shots-${key}`} className="space-y-1.5">
                {shots.length === 0 ? (
                  <div className="text-xs text-content-3 italic">
                    {hasContent
                      ? t('projects.sceneBoard.noShots')
                      : t('projects.sceneBoard.emptyScene')}
                  </div>
                ) : (
                  shots.map((shot, shotIdx) => (
                    <div
                      key={shot.id}
                      className="flex items-center gap-2 rounded border border-line bg-island-2/40 px-2 py-1.5"
                    >
                      {shot.thumbnail_url ? (
                        <img
                          src={shot.thumbnail_url}
                          alt=""
                          className="h-8 w-12 rounded object-cover shrink-0"
                        />
                      ) : (
                        <div className="h-8 w-12 rounded bg-island-2 shrink-0" />
                      )}
                      <span className="font-mono text-[10px] font-bold text-content-2 shrink-0">
                        {t('projects.sceneBoard.shotBadge', {
                          scene: num,
                          shot: shot.shot_number ?? shotIdx + 1,
                        })}
                      </span>
                      <span className="text-[12px] text-content-3 truncate">
                        {shot.description ?? ''}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        );
      })}
      {scenes.length === 0 && (
        <div className="text-center text-sm text-content-3 py-10">
          {t('projects.sceneBoard.noScenes')}
        </div>
      )}
    </div>
  );
}

export default EpisodeSceneBoard;
