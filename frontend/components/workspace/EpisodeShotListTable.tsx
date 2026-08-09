/**
 * EpisodeShotListTable — view three ("Shot List") of the episode-node
 * three-view primary work surface (SDD 2026-08-09 Task 2). A flat table:
 * one group-header row per scene (colSpan=7, shown even for a zero-shot
 * scene) followed by one row per shot, plus a CSV export that shells out to
 * `buildShotListCsv` + a Blob/object-URL anchor download (zipExport.ts:102
 * pattern). No duration column — Shot carries no duration field
 * (sceneService.ts's Shot type / plan's "盘点权威事实").
 *
 * `hideExport` + the forwarded `exportCsv` handle (Task 3, 主工作面接线): the
 * table is mounted inside a segmented-control content pane whose Export
 * trigger lives in the tabs' own `actions` slot, not inside the pane. Rather
 * than re-fetching scenes/shots a second time at the parent to drive a
 * standalone export hook, the parent holds a ref and calls straight into
 * this component's own (already-fetched) `handleExport` — zero duplicate
 * network round-trip. The plain `<EpisodeShotListTable scriptId />` (no ref,
 * no hideExport) keeps working exactly as Task 2 shipped it.
 */
import { Fragment, forwardRef, useCallback, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import type { SceneDoc } from '../../editor/types';
import { buildShotListCsv, type ShotListCsvGroup } from './shotListCsv';
import { useSceneShots } from './useSceneShots';

export interface EpisodeShotListTableProps {
  scriptId: string;
  /** Suppress the built-in top-right Export button — the parent renders its
   * own trigger elsewhere (e.g. EpisodeViewTabs' actions slot) and drives
   * export via the forwarded ref's `exportCsv()` instead. Default false. */
  hideExport?: boolean;
}

export interface EpisodeShotListTableHandle {
  /** Same CSV-blob-and-download the built-in button fires; a no-op call site
   * can safely invoke it even before scenes/shots have finished loading (it
   * exports whatever's in state — an empty CSV — matching the built-in
   * button's own behavior mid-load, since it renders as soon as the table
   * mounts). */
  exportCsv: () => void;
}

/** Slate scene-number read-out: the real value, or a 1-based fallback (mirrors EpisodeSceneBoard). */
function sceneNumberLabel(scene: SceneDoc, idx: number): string {
  return scene.scene_number ?? String(idx + 1);
}

export const EpisodeShotListTable = forwardRef<EpisodeShotListTableHandle, EpisodeShotListTableProps>(
  function EpisodeShotListTable({ scriptId, hideExport = false }, ref) {
    const { t } = useTranslation();
    const { scenes, shotsByScene } = useSceneShots(scriptId);

    const handleExport = useCallback(() => {
      const groups: ShotListCsvGroup[] = scenes.map((scene, idx) => ({
        sceneLabel: sceneNumberLabel(scene, idx),
        shots: shotsByScene[String(scene.id)] ?? [],
      }));
      const csv = buildShotListCsv(groups);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `shotlist-${scriptId}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }, [scenes, shotsByScene, scriptId]);

    useImperativeHandle(ref, () => ({ exportCsv: handleExport }), [handleExport]);

    return (
      <div data-testid="ep-shotlist-table">
        {!hideExport && (
          <div className="flex justify-end mb-2">
            <button
              type="button"
              data-testid="ep-shotlist-export"
              onClick={handleExport}
              className="rounded-md border border-line px-3 py-1.5 text-[13px] font-medium text-content hover:bg-island-2"
            >
              {t('projects.shotList.export')}
            </button>
          </div>
        )}
        <table className="w-full text-sm border border-line rounded-lg overflow-hidden">
          <thead>
            <tr className="text-left text-content-3 text-xs border-b border-line bg-island-2">
              <th className="px-3 py-2 font-medium">{t('projects.shotList.frame')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.shot')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.type')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.angle')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.move')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.lens')}</th>
              <th className="px-3 py-2 font-medium">{t('projects.shotList.description')}</th>
            </tr>
          </thead>
          <tbody>
            {scenes.map((sceneDoc, idx) => {
              const key = String(sceneDoc.id);
              const shots = shotsByScene[key] ?? [];
              const num = sceneNumberLabel(sceneDoc, idx);

              return (
                <Fragment key={key}>
                  <tr data-testid={`ep-shotlist-group-${key}`} className="bg-island-2 font-medium">
                    <td colSpan={7} className="px-3 py-2 text-content">
                      {num} — {sceneDoc.heading_int_ext ?? '—'}. {sceneDoc.location_text ?? '—'} ·{' '}
                      {sceneDoc.time_of_day ?? '—'}　({shots.length})
                    </td>
                  </tr>
                  {shots.map((shot, shotIdx) => (
                    <tr
                      key={shot.id}
                      data-testid={`ep-shotlist-row-${shot.id}`}
                      className="border-b border-line/50"
                    >
                      <td className="px-3 py-2">
                        {shot.thumbnail_url || shot.image_url ? (
                          <img
                            src={shot.thumbnail_url || shot.image_url || ''}
                            alt=""
                            className="h-[34px] w-[56px] rounded object-cover"
                          />
                        ) : (
                          <div className="h-[34px] w-[56px] rounded bg-island-2" />
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-content-2">
                        {num}-{shot.shot_number ?? shotIdx + 1}
                      </td>
                      <td className="px-3 py-2 text-content-2">{shot.shot_type ?? '—'}</td>
                      <td className="px-3 py-2 text-content-2">{shot.camera_angle ?? '—'}</td>
                      <td className="px-3 py-2 text-content-2">{shot.camera_movement ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-content-2">{shot.focal_length ?? '—'}</td>
                      <td className="px-3 py-2 text-content-3 truncate max-w-xs">
                        {shot.description ?? '—'}
                      </td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  },
);

export default EpisodeShotListTable;
