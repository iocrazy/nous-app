/**
 * EpisodeSceneBoard — view one ("Storyboard") of the episode-node three-view
 * primary work surface (SDD 2026-08-09 Task 1). A vertical stream of scene
 * cards: each carries the scene's real slate metadata (scene_number / I-E /
 * location / time-of-day), an "Open" affordance that re-centers its own
 * column (used to deep-link into the embedded editor's storyboard rail —
 * retired in Task 6, shot-nodes-on-canvas epic, since this view IS that
 * board now), an "Auto Storyboard" two-click-confirm dispatch, and a
 * simplified read-only shot list.
 *
 * The confirm+poll dispatch was ported from the old editor storyboard
 * rail's StoryboardView (since deleted) — the LOGIC was ported, not
 * imported, per the plan's workspace/editor design-language split (this
 * surface uses the content / island / line / accent token family, never the
 * editor's local --ink or --surface CSS, nor its mh-sb- classes).
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
  /**
   * "Open" click on the scene's own column — handled by the parent
   * (`EpisodeStoryboardPage`) purely locally: scrolls that column into view.
   * Used to deep-link into the embedded editor's storyboard rail at that
   * scene (Task 8); that rail was retired in Task 6 (shot-nodes-on-canvas
   * epic) since this board is now its own destination.
   */
  onOpenScene: (sceneId: string) => void;
  /**
   * Shot card click → switches the storyboard page to its own Canvas tab
   * and focuses this shot's node there (shot-nodes-on-canvas Task 5,
   * 2026-08-11 — supersedes the Task 3 修复轮2 editor deep-link; the Canvas
   * tab now embeds the episode's real storyboard canvas with shot nodes
   * bound to `script_shots`, so a focus request there is meaningful). The
   * shot's own sceneId rides along since the column it's rendered in already
   * has it at hand — same shape as `onOpenScene` above.
   */
  onOpenShot: (shotId: string, sceneId: string) => void;
}

/** Slate scene-number read-out: the real value, or a 1-based fallback. */
function sceneNumberLabel(scene: SceneDoc, idx: number): string {
  return scene.scene_number ?? String(idx + 1);
}

export function EpisodeSceneBoard({ scriptId, onOpenScene, onOpenShot }: EpisodeSceneBoardProps) {
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
      <div className="flex gap-3 overflow-x-auto pb-2" data-testid="scene-columns">
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
              data-testid={`scene-column-${key}`}
              className="w-[236px] flex-none overflow-hidden rounded-lg border border-line bg-island"
            >
              {/* Clap-stripe header — decorative slate marker, theme-aware via --line. */}
              <div
                className="h-5 border-b border-line bg-[repeating-linear-gradient(115deg,var(--line)_0_9px,transparent_9px_18px)]"
                aria-hidden="true"
              />

              <div className="grid grid-cols-[1fr_auto] gap-x-2.5 gap-y-0.5 px-3 py-2 font-mono">
                <div>
                  <div className="text-[9px] uppercase text-ink-500">
                    {t('projects.sceneBoard.sceneLabel')}
                  </div>
                  <div className="text-[12px] font-medium text-content">{num}</div>
                </div>
                <div>
                  <div className="text-[9px] uppercase text-ink-500">
                    {t('projects.sceneBoard.ieLabel')}
                  </div>
                  <div className="text-[12px] font-medium text-content">
                    {sceneDoc.heading_int_ext ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-[9px] uppercase text-ink-500">
                    {t('projects.sceneBoard.locationLabel')}
                  </div>
                  <div className="text-[12px] font-medium text-content truncate">
                    {sceneDoc.location_text ?? '—'}
                  </div>
                </div>
                <div>
                  <div className="text-[9px] uppercase text-ink-500">
                    {t('projects.sceneBoard.dayNightLabel')}
                  </div>
                  <div className="text-[12px] font-medium text-content">
                    {sceneDoc.time_of_day ?? '—'}
                  </div>
                </div>
              </div>

              <div className="flex gap-1.5 px-3 pb-2.5">
                <button
                  type="button"
                  data-testid={`ep-scene-open-${key}`}
                  onClick={() => onOpenScene(sceneDoc.id)}
                  className="rounded-md border border-line px-2.5 py-1 text-[12px] font-medium text-content hover:bg-island-2"
                >
                  {t('projects.sceneBoard.open')}
                </button>
                <button
                  type="button"
                  data-testid={`ep-scene-auto-${key}`}
                  disabled={busy}
                  aria-busy={busy || undefined}
                  onClick={() => handleAutoClick(sceneDoc.id)}
                  className={`rounded-md border px-2.5 py-1 text-[12px] font-medium disabled:opacity-50 ${
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

              <div data-testid={`ep-scene-shots-${key}`} className="flex flex-col gap-2 px-3 pb-3">
                {shots.length === 0 ? (
                  <div className="text-xs text-content-3 italic">
                    {hasContent
                      ? t('projects.sceneBoard.noShots')
                      : t('projects.sceneBoard.emptyScene')}
                  </div>
                ) : (
                  shots.map((shotItem, shotIdx) => (
                    <button
                      key={shotItem.id}
                      type="button"
                      data-testid={`shot-card-${shotItem.id}`}
                      onClick={() => onOpenShot(String(shotItem.id), key)}
                      className="rounded-md border border-dashed border-line bg-island-2 px-2 py-2.5 text-center hover:border-agent-line hover:bg-agent-soft"
                    >
                      <span className="block font-mono text-[11.5px] font-bold text-ink-300">
                        {t('projects.sceneBoard.shotBadge', {
                          scene: num,
                          shot: shotItem.shot_number ?? shotIdx + 1,
                        })}
                      </span>
                      <span className="text-[10.5px] text-ink-500">
                        {t('projects.storyboardPage.shotHint', 'Open in editor')}
                      </span>
                    </button>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
      {scenes.length === 0 && (
        <div className="text-center text-sm text-content-3 py-10">
          {t('projects.sceneBoard.noScenes')}
        </div>
      )}
    </div>
  );
}

export default EpisodeSceneBoard;
