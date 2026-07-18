/**
 * WorkspaceEpisodes — the Episodes management module (spec frame: "Episodes
 * 模块 —— 剧集管理面", decision G4). Full list of every episode with its
 * derived progress line, ⋯ menu (Rename / Move up / Move down / Delete),
 * and an Open/Start CTA that hands off to the shell (sets the current
 * episode + deep-links into its script editor).
 *
 * v1 simplification: no real drag-and-drop reordering — Move up/Move down
 * swap `sort_order` with the neighboring row via two PATCH calls. Real drag
 * is a fit-and-finish follow-up (timeboxed per PR-10b Wave 2 scope).
 */

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MoreHorizontal } from 'lucide-react';
import {
  createEpisode,
  deleteEpisode,
  updateEpisode,
} from '../../services/projectsService';
import { ApiError } from '../../services/apiClient';
import { useToast } from '../Toast';
import type { EpisodeProgress } from '../../types';

interface WorkspaceEpisodesProps {
  projectId: string;
  episodes: EpisodeProgress[];
  /** Refetch the episodes/progress feed after a create/rename/reorder/delete. */
  onEpisodesChanged: () => void;
  /** Set this episode as current + deep-link into its script editor. */
  onOpenEpisode: (episodeId: string) => void;
}

function isEpisodeEmpty(ep: EpisodeProgress): boolean {
  return ep.scene_count === 0 && ep.shots_total === 0 && ep.renders_count === 0;
}

export function WorkspaceEpisodes({
  projectId,
  episodes,
  onEpisodesChanged,
  onOpenEpisode,
}: WorkspaceEpisodesProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [creating, setCreating] = useState(false);
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const sorted = [...episodes].sort((a, b) => a.sort_order - b.sort_order);

  const handleCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      // Sequential starting title so a fresh episode doesn't collide with
      // the server default ("Ep 1") when "Episode 1" already exists —
      // derived from the progress feed already in scope, not a uniqueness
      // guarantee (a rename elsewhere can still create a duplicate).
      //
      // sort_order is assigned server-side (max(sort_order)+1), NOT sent from
      // here, so two near-simultaneous creates (two tabs / two members) can
      // land on the same sort_order. That tie is benign: the list renders
      // through `[...episodes].sort((a,b) => a.sort_order - b.sort_order)`
      // (stable, so tied rows keep fetch order) and the ▲▼ reorder swaps the
      // two rows' sort_order values, which resolves any duplicate on first
      // move. Not worth a transaction/unique-constraint for a low-frequency,
      // self-healing collision.
      const title = `Episode ${episodes.length + 1}`;
      await createEpisode(projectId, title);
      onEpisodesChanged();
    } catch (err) {
      console.error('[WorkspaceEpisodes] create failed:', err);
      addToast(t('common.error'), 'error');
    } finally {
      setCreating(false);
    }
  }, [creating, projectId, episodes.length, onEpisodesChanged, addToast, t]);

  const startRename = useCallback((ep: EpisodeProgress) => {
    setRenamingId(ep.episode_id);
    setRenameValue(ep.title);
    setMenuOpenFor(null);
  }, []);

  const commitRename = useCallback(
    async (episodeId: string) => {
      const title = renameValue.trim();
      setRenamingId(null);
      const original = episodes.find((e) => e.episode_id === episodeId);
      if (!title || title === original?.title) return;
      setBusyId(episodeId);
      try {
        await updateEpisode(episodeId, { title });
        onEpisodesChanged();
      } catch (err) {
        console.error('[WorkspaceEpisodes] rename failed:', err);
        addToast(t('common.error'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [renameValue, episodes, onEpisodesChanged, addToast, t],
  );

  const move = useCallback(
    async (episodeId: string, direction: 'up' | 'down') => {
      setMenuOpenFor(null);
      const idx = sorted.findIndex((e) => e.episode_id === episodeId);
      const otherIdx = direction === 'up' ? idx - 1 : idx + 1;
      if (idx < 0 || otherIdx < 0 || otherIdx >= sorted.length) return;
      const current = sorted[idx];
      const other = sorted[otherIdx];
      setBusyId(episodeId);
      try {
        await Promise.all([
          updateEpisode(current.episode_id, { sort_order: other.sort_order }),
          updateEpisode(other.episode_id, { sort_order: current.sort_order }),
        ]);
        onEpisodesChanged();
      } catch (err) {
        console.error('[WorkspaceEpisodes] reorder failed:', err);
        addToast(t('common.error'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [sorted, onEpisodesChanged, addToast, t],
  );

  const confirmDelete = useCallback(
    async (episodeId: string) => {
      setDeleteConfirmId(null);
      setBusyId(episodeId);
      try {
        await deleteEpisode(episodeId);
        onEpisodesChanged();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          addToast(t('projects.workspace.episodes.notEmpty'), 'error');
        } else {
          console.error('[WorkspaceEpisodes] delete failed:', err);
          addToast(t('common.error'), 'error');
        }
      } finally {
        setBusyId(null);
      }
    },
    [onEpisodesChanged, addToast, t],
  );

  return (
    <div data-testid="ws-episodes" className="py-3">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-semibold text-ink-100">
          {t('projects.workspace.episodes.title')}
        </div>
        <button
          data-testid="ws-episodes-new-btn"
          onClick={handleCreate}
          disabled={creating}
          className="rounded-lg bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-ink-950 font-semibold text-[12.5px] px-3 py-1.5 transition-colors"
        >
          {t('projects.workspace.episodes.newEpisode')}
        </button>
      </div>

      <div className="rounded-xl border border-ink-800 overflow-hidden">
        {sorted.map((ep, i) => {
          const empty = isEpisodeEmpty(ep);
          const isBusy = busyId === ep.episode_id;
          return (
            <div
              key={ep.episode_id}
              data-testid={`ws-episode-row-${ep.episode_id}`}
              className={`flex items-center gap-3 px-3 py-2.5 text-[12.5px] ${
                i > 0 ? 'border-t border-ink-800/60' : ''
              } ${isBusy ? 'opacity-50' : ''}`}
            >
              <span className="font-mono text-[11px] text-ink-500 w-10 shrink-0">
                {t('projects.workspace.episodes.epPrefix', { n: i + 1 })}
              </span>

              <div className="min-w-0 flex-1">
                {renamingId === ep.episode_id ? (
                  <input
                    data-testid={`ws-episode-rename-input-${ep.episode_id}`}
                    autoFocus
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => commitRename(ep.episode_id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitRename(ep.episode_id);
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    className="w-full bg-ink-800 border border-ink-600 rounded px-1.5 py-0.5 text-ink-100"
                  />
                ) : (
                  <div
                    data-testid={`ws-episode-title-${ep.episode_id}`}
                    onDoubleClick={() => startRename(ep)}
                    className="font-medium text-ink-100 truncate cursor-text"
                    title={ep.title}
                  >
                    {ep.title}
                  </div>
                )}
              </div>

              <span
                data-testid={`ws-episode-status-${ep.episode_id}`}
                className="text-[11px] text-indigo-300 bg-indigo-500/10 rounded-full px-2 py-0.5 font-medium whitespace-nowrap shrink-0"
              >
                {t(`projects.workspace.episodeStatus.${ep.status}`, ep.status)}
              </span>

              <span
                data-testid={`ws-episode-progress-${ep.episode_id}`}
                className={`font-mono text-[11px] flex-1 min-w-0 truncate ${
                  empty ? 'text-ink-600' : 'text-ink-400'
                }`}
              >
                {empty
                  ? t('projects.workspace.episodes.empty')
                  : `${t('projects.workspace.episodes.scriptScenes', { count: ep.scene_count })} · ${t(
                      'projects.workspace.episodes.shotsProgress',
                      { done: ep.shots_done, total: ep.shots_total },
                    )} · ${t('projects.workspace.episodes.rendersCount', { count: ep.renders_count })}`}
              </span>

              <button
                data-testid={`ws-episode-open-${ep.episode_id}`}
                onClick={() => onOpenEpisode(ep.episode_id)}
                className="shrink-0 rounded-md border border-ink-700 hover:border-ink-500 text-ink-300 font-medium text-[11.5px] px-2.5 py-1 transition-colors"
              >
                {empty ? t('projects.workspace.episodes.start') : t('projects.workspace.episodes.open')}
              </button>

              <div className="relative shrink-0">
                <button
                  data-testid={`ws-episode-menu-${ep.episode_id}`}
                  onClick={() =>
                    setMenuOpenFor((cur) => (cur === ep.episode_id ? null : ep.episode_id))
                  }
                  className="p-1 rounded text-ink-500 hover:text-ink-200 hover:bg-ink-800 transition-colors"
                >
                  <MoreHorizontal size={14} />
                </button>
                {menuOpenFor === ep.episode_id && (
                  <div className="absolute right-0 top-full mt-1 z-50 w-36 rounded-lg border border-ink-700 bg-ink-900 shadow-2xl py-1">
                    <button
                      data-testid={`ws-episode-menu-rename-${ep.episode_id}`}
                      onClick={() => startRename(ep)}
                      className="w-full text-left px-3 py-1.5 text-[12px] text-ink-300 hover:text-ink-100 hover:bg-ink-800"
                    >
                      {t('projects.workspace.episodes.rename')}
                    </button>
                    <button
                      data-testid={`ws-episode-menu-moveup-${ep.episode_id}`}
                      onClick={() => move(ep.episode_id, 'up')}
                      disabled={i === 0}
                      className="w-full text-left px-3 py-1.5 text-[12px] text-ink-300 hover:text-ink-100 hover:bg-ink-800 disabled:opacity-40 disabled:cursor-default"
                    >
                      {t('projects.workspace.episodes.moveUp')}
                    </button>
                    <button
                      data-testid={`ws-episode-menu-movedown-${ep.episode_id}`}
                      onClick={() => move(ep.episode_id, 'down')}
                      disabled={i === sorted.length - 1}
                      className="w-full text-left px-3 py-1.5 text-[12px] text-ink-300 hover:text-ink-100 hover:bg-ink-800 disabled:opacity-40 disabled:cursor-default"
                    >
                      {t('projects.workspace.episodes.moveDown')}
                    </button>
                    <button
                      data-testid={`ws-episode-menu-delete-${ep.episode_id}`}
                      onClick={() => {
                        setMenuOpenFor(null);
                        setDeleteConfirmId(ep.episode_id);
                      }}
                      className="w-full text-left px-3 py-1.5 text-[12px] text-red-400 hover:text-red-300 hover:bg-ink-800"
                    >
                      {t('projects.workspace.episodes.delete')}
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {deleteConfirmId && (
        <div
          data-testid={`ws-episode-delete-confirm-${deleteConfirmId}`}
          className="mt-3 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-300"
        >
          <span className="flex-1">{t('projects.workspace.episodes.deleteConfirm')}</span>
          <button
            data-testid={`ws-episode-delete-confirm-yes-${deleteConfirmId}`}
            onClick={() => confirmDelete(deleteConfirmId)}
            className="rounded-md bg-red-500 hover:bg-red-400 text-ink-950 font-semibold text-[11.5px] px-2.5 py-1 transition-colors"
          >
            {t('projects.workspace.episodes.deleteConfirmYes')}
          </button>
          <button
            data-testid={`ws-episode-delete-confirm-cancel-${deleteConfirmId}`}
            onClick={() => setDeleteConfirmId(null)}
            className="rounded-md border border-ink-700 text-ink-300 font-medium text-[11.5px] px-2.5 py-1 transition-colors"
          >
            {t('projects.workspace.episodes.deleteConfirmCancel')}
          </button>
        </div>
      )}
    </div>
  );
}

export default WorkspaceEpisodes;
