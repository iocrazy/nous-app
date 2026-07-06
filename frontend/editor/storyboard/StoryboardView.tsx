/**
 * StoryboardView — the shot-board projection surface (Phase B P3).
 *
 * Horizontal scene columns: each column heads with a scene-number badge + a
 * typographic INT/EXT · Location · TIME heading + an inline-confirm Auto
 * Storyboard button, then stacks that scene's shot cards vertically with a
 * "+ Add Shot" footer. Shots load per scene in parallel on mount and re-sync
 * when the scene set changes. Auto Storyboard dispatches the flat task_id
 * endpoint and polls `listShots` (via the generic usePoll) until the scene's
 * shot count grows, then reloads just that column.
 *
 * Reorder is WITHIN a column only (P3 scope — no cross-scene shot move): the
 * column owns the HTML5 drag state and hands each card its slice through the
 * ShotCard `reorder` prop.
 *
 * #1006: scene/shot ids are strings end-to-end but arrive as native numbers at
 * runtime, so every id comparison coerces both sides with String().
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  autoStoryboard,
  createShot,
  deleteShot,
  generateShot,
  getShot,
  listShots,
  moveShot,
  ShotGenerateDisabledError,
  updateShot,
  type Shot,
} from '../sceneService';
import { usePoll } from '../usePoll';
import { ShotCard } from './ShotCard';
import type { SceneDoc } from '../types';

/** How long an armed Auto Storyboard "Confirm?" stays live before auto-disarming. */
const CONFIRM_WINDOW_MS = 3000;

/**
 * Session-level memory: once the generate endpoint 404s (feature flag off), every
 * Generate button degrades for the rest of the session. Module-scoped so it
 * survives StoryboardView remounts (switching rail views unmounts the board).
 */
let generateFeatureOff = false;

export interface StoryboardViewProps {
  scenes: SceneDoc[];
  scriptId: string;
}

type ShotsByScene = Record<string, Shot[]>;
type DropTarget = { shotId: string; edge: 'before' | 'after' };

/** `INT · Location · TIME`, empty parts dropped (mirrors OutlineView's head). */
function headingLine(scene: SceneDoc): string {
  return [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
}

export function StoryboardView({ scenes, scriptId }: StoryboardViewProps) {
  const { t } = useTranslation();
  const { startPoll } = usePoll();

  const [shotsByScene, setShotsByScene] = useState<ShotsByScene>({});
  const [autoBusy, setAutoBusy] = useState<Record<string, boolean>>({});
  const [confirmingAuto, setConfirmingAuto] = useState<string | null>(null);
  const [generateDisabled, setGenerateDisabled] = useState(generateFeatureOff);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Drag reorder is column-scoped: a shot may only drop within its own scene.
  const [dragging, setDragging] = useState<{ shotId: string; sceneId: string } | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const draggingRef = useRef<{ shotId: string; sceneId: string } | null>(null);

  // Load every scene's shots in parallel on mount / when the scene set changes.
  useEffect(() => {
    let cancelled = false;
    Promise.all(
      scenes.map(async (scene) => {
        const shots = await listShots(scene.id).catch((err) => {
          console.error('[StoryboardView] failed to load shots', err);
          return [] as Shot[];
        });
        return [String(scene.id), shots] as const;
      }),
    ).then((pairs) => {
      if (cancelled) return;
      setShotsByScene(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [scenes]);

  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const refreshScene = useCallback(async (sceneId: string) => {
    try {
      const shots = await listShots(sceneId);
      setShotsByScene((prev) => ({ ...prev, [String(sceneId)]: shots }));
    } catch (err) {
      console.error('[StoryboardView] failed to reload scene shots', err);
    }
  }, []);

  // Merge a partial into one shot in-place (optimistic status flips, poll results).
  const patchShotLocal = useCallback(
    (sceneId: string, shotId: string, patch: Partial<Shot>) => {
      setShotsByScene((prev) => ({
        ...prev,
        [String(sceneId)]: (prev[String(sceneId)] ?? []).map((s) =>
          String(s.id) === String(shotId) ? { ...s, ...patch } : s,
        ),
      }));
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
        console.error('[StoryboardView] autoStoryboard dispatch failed', err);
        setAutoBusy((prev) => ({ ...prev, [key]: false }));
        return;
      }
      startPoll(
        async () => {
          const shots = await listShots(sceneId);
          if (shots.length > baseline) {
            setShotsByScene((prev) => ({ ...prev, [key]: shots }));
            return true;
          }
          return false;
        },
        () => setAutoBusy((prev) => ({ ...prev, [key]: false })),
      );
    },
    [shotsByScene, startPoll],
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

  const handleAddShot = useCallback(
    async (sceneId: string) => {
      try {
        await createShot(sceneId, {});
        await refreshScene(sceneId);
      } catch (err) {
        console.error('[StoryboardView] createShot failed', err);
      }
    },
    [refreshScene],
  );

  const handleUpdateShot = useCallback(
    async (sceneId: string, shotId: string, data: Partial<Shot>) => {
      try {
        const updated = await updateShot(shotId, data);
        setShotsByScene((prev) => ({
          ...prev,
          [String(sceneId)]: (prev[String(sceneId)] ?? []).map((s) =>
            String(s.id) === String(shotId) ? { ...s, ...updated } : s,
          ),
        }));
      } catch (err) {
        console.error('[StoryboardView] updateShot failed', err);
      }
    },
    [],
  );

  const handleDeleteShot = useCallback(
    async (sceneId: string, shotId: string) => {
      try {
        await deleteShot(shotId);
        await refreshScene(sceneId);
      } catch (err) {
        console.error('[StoryboardView] deleteShot failed', err);
      }
    },
    [refreshScene],
  );

  // Generate a single shot's image: optimistic 'generating', dispatch, then poll
  // getShot until the workflow lands 'done'/'failed'. A flag-off 404 degrades
  // every Generate control for the session; a dispatch failure or poll timeout
  // (attempt cap) shows the failed state with its Retry affordance.
  const handleGenerate = useCallback(
    async (sceneId: string, shot: Shot) => {
      const prevStatus = shot.status;
      patchShotLocal(sceneId, shot.id, { status: 'generating' });
      try {
        await generateShot(shot.id);
      } catch (err) {
        if (err instanceof ShotGenerateDisabledError) {
          generateFeatureOff = true;
          setGenerateDisabled(true);
          patchShotLocal(sceneId, shot.id, { status: prevStatus });
          return;
        }
        console.error('[StoryboardView] generateShot dispatch failed', err);
        patchShotLocal(sceneId, shot.id, { status: 'failed' });
        return;
      }
      startPoll(
        async () => {
          const fresh = await getShot(shot.id);
          if (fresh.status === 'done' || fresh.status === 'failed') {
            patchShotLocal(sceneId, shot.id, fresh);
            return true;
          }
          return false;
        },
        (settled) => {
          if (!settled) patchShotLocal(sceneId, shot.id, { status: 'failed' });
        },
      );
    },
    [patchShotLocal, startPoll],
  );

  const beginDrag = useCallback((shotId: string, sceneId: string) => {
    draggingRef.current = { shotId, sceneId: String(sceneId) };
    setDragging({ shotId, sceneId: String(sceneId) });
  }, []);

  const endDrag = useCallback(() => {
    draggingRef.current = null;
    setDragging(null);
    setDropTarget(null);
  }, []);

  const handleDrop = useCallback(
    async (sceneId: string, target: Shot, edge: 'before' | 'after') => {
      const drag = draggingRef.current;
      endDrag();
      if (!drag || drag.shotId === target.id) return;
      // Within-column only: ignore a drop whose dragged shot is a different scene.
      if (String(drag.sceneId) !== String(sceneId)) return;
      const anchor =
        edge === 'before' ? { before_shot_id: target.id } : { after_shot_id: target.id };
      try {
        await moveShot(drag.shotId, anchor);
        await refreshScene(sceneId);
      } catch (err) {
        console.error('[StoryboardView] moveShot failed', err);
      }
    },
    [endDrag, refreshScene],
  );

  return (
    <div className="mh-storyboard" data-testid="storyboard-view" aria-label={t('editor.storyboardLabel')}>
      {scenes.map((scene, sceneIdx) => {
        const key = String(scene.id);
        const shots = shotsByScene[key] ?? [];
        const heading = headingLine(scene);
        const busy = !!autoBusy[key];
        const dragInScene = dragging?.sceneId === key;
        return (
          <section key={key} className="mh-sb-column" data-testid="storyboard-column" data-scene-id={key}>
            <header className="mh-sb-col-head">
              <span className="mh-scene-num-badge">{sceneIdx + 1}</span>
              <span className="mh-sb-col-heading">{heading || t('editor.untitledScene')}</span>
              <button
                type="button"
                className={`mh-sb-auto-btn${confirmingAuto === key ? ' confirming' : ''}`}
                disabled={busy}
                aria-busy={busy || undefined}
                onClick={() => handleAutoClick(scene.id)}
              >
                {busy
                  ? t('editor.storyboardAutoBusy')
                  : confirmingAuto === key
                    ? t('editor.nodesConfirm')
                    : t('editor.storyboardAuto')}
              </button>
            </header>

            <div className="mh-sb-shots">
              {shots.length === 0 && (
                <div className="mh-sb-empty">{t('editor.storyboardEmptyColumn')}</div>
              )}
              {shots.map((shot, shotIdx) => {
                const dropEdge =
                  dragInScene && dropTarget && String(dropTarget.shotId) === String(shot.id)
                    ? dropTarget.edge
                    : null;
                return (
                  <ShotCard
                    key={key + ':' + String(shot.id)}
                    shot={shot}
                    index={shotIdx + 1}
                    onUpdate={(shotId, data) => handleUpdateShot(scene.id, shotId, data)}
                    onDelete={(shotId) => handleDeleteShot(scene.id, shotId)}
                    onGenerate={() => handleGenerate(scene.id, shot)}
                    generateDisabled={generateDisabled}
                    reorder={{
                      isDragging: dragging?.shotId === shot.id,
                      dropEdge,
                      onDragStart: () => beginDrag(shot.id, scene.id),
                      onDragEnd: endDrag,
                      onDragOver: (edge) => {
                        if (!dragInScene || dragging?.shotId === shot.id) return;
                        setDropTarget((prev) =>
                          prev && String(prev.shotId) === String(shot.id) && prev.edge === edge
                            ? prev
                            : { shotId: shot.id, edge },
                        );
                      },
                      onDrop: (edge) => void handleDrop(scene.id, shot, edge),
                    }}
                  />
                );
              })}
            </div>

            <button
              type="button"
              className="mh-sb-add"
              onClick={() => handleAddShot(scene.id)}
            >
              {t('editor.storyboardAddShot')}
            </button>
          </section>
        );
      })}
      {scenes.length === 0 && (
        <div className="mh-sb-empty-board">{t('editor.storyboardNoScenes')}</div>
      )}
    </div>
  );
}
