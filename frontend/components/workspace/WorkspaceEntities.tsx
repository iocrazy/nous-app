/**
 * WorkspaceEntities — the Characters/Locations ASSETS "main library" module
 * (spec frame: "Characters / Locations 主库(ASSETS)", decision G13). One
 * component serves both `characters` and `locations` (prop-driven) since
 * they share the same main-library + episode-badge layout, just a
 * different count label and data source field.
 *
 * Entities are derived server-side from script cues/scene headers — this
 * view is read-only (no create/edit; the note line makes that explicit).
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchProjectEntities } from '../../services/projectsService';
import type { EpisodeProgress, ProjectEntities } from '../../types';

interface WorkspaceEntitiesProps {
  kind: 'characters' | 'locations';
  projectId: string;
  episodes: EpisodeProgress[];
}

// Deterministic avatar hue per entity name — mirrors ProjectCard's
// avatarHue so initials chips look consistent across the app without
// storing a color anywhere.
const AVATAR_HUES = [212, 32, 152, 262, 105, 342];
function avatarHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

function initials(name: string): string {
  return (name || '?').trim().slice(0, 2).toUpperCase();
}

export function WorkspaceEntities({ kind, projectId, episodes }: WorkspaceEntitiesProps) {
  const { t } = useTranslation();
  const [entities, setEntities] = useState<ProjectEntities>({ characters: [], locations: [] });
  const [loading, setLoading] = useState(true);
  const [episodeFilter, setEpisodeFilter] = useState<string | 'all'>('all');
  const [filterOpen, setFilterOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchProjectEntities(projectId)
      .then((data) => {
        if (!cancelled) setEntities(data);
      })
      .catch((err) => console.error('[WorkspaceEntities] failed to load entities:', err))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const sortedEpisodes = useMemo(
    () => [...episodes].sort((a, b) => a.sort_order - b.sort_order),
    [episodes],
  );
  const epLabel = (episodeId: string): string => {
    const idx = sortedEpisodes.findIndex((e) => e.episode_id === episodeId);
    return t('projects.workspace.entities.epChip', { n: idx >= 0 ? idx + 1 : '?' });
  };

  const rows = kind === 'characters' ? entities.characters : entities.locations;
  const filtered =
    episodeFilter === 'all'
      ? rows
      : rows.filter((r) => r.episode_ids.includes(episodeFilter));

  return (
    <div data-testid={`ws-entities-${kind}`} className="py-3">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-semibold text-ink-100 inline-flex items-center gap-2">
            {t(`projects.workspace.modules.${kind}`)}
            <span className="text-[11px] font-normal text-ink-500">
              {t('projects.workspace.entities.derivedNote')}
            </span>
          </div>
        </div>
        <div className="relative">
          <button
            data-testid="ws-entities-filter"
            onClick={() => setFilterOpen((v) => !v)}
            className="text-[12px] border border-ink-700 rounded-full px-3 py-1 text-ink-300 hover:border-ink-500 transition-colors"
          >
            {episodeFilter === 'all'
              ? t('projects.workspace.entities.filterAll')
              : epLabel(episodeFilter)}
            {' ▾'}
          </button>
          {filterOpen && (
            <div className="absolute right-0 top-full mt-1 z-50 w-40 rounded-lg border border-ink-700 bg-ink-900 shadow-2xl py-1 max-h-56 overflow-y-auto">
              <button
                data-testid="ws-entities-filter-all"
                onClick={() => {
                  setEpisodeFilter('all');
                  setFilterOpen(false);
                }}
                className="w-full text-left px-3 py-1.5 text-[12px] text-ink-300 hover:text-ink-100 hover:bg-ink-800"
              >
                {t('projects.workspace.entities.filterAll')}
              </button>
              {sortedEpisodes.map((ep) => (
                <button
                  key={ep.episode_id}
                  data-testid={`ws-entities-filter-${ep.episode_id}`}
                  onClick={() => {
                    setEpisodeFilter(ep.episode_id);
                    setFilterOpen(false);
                  }}
                  className="w-full text-left px-3 py-1.5 text-[12px] text-ink-300 hover:text-ink-100 hover:bg-ink-800"
                >
                  {ep.title}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {!loading && filtered.length === 0 && (
        <div data-testid="ws-entities-empty" className="text-center text-xs text-ink-600 py-10">
          {t(
            kind === 'characters'
              ? 'projects.workspace.entities.emptyCharacters'
              : 'projects.workspace.entities.emptyLocations',
          )}
        </div>
      )}

      {filtered.length > 0 && (
        <div className="rounded-xl border border-ink-800 overflow-hidden">
          {filtered.map((row, i) => (
            <div
              key={row.name}
              data-testid={`ws-entities-row-${i}`}
              className={`flex items-center gap-3 px-3 py-2.5 text-[12.5px] ${
                i > 0 ? 'border-t border-ink-800/60' : ''
              }`}
            >
              <span
                className="w-6 h-6 rounded-full grid place-items-center text-[10px] font-bold text-white shrink-0"
                style={{ backgroundColor: `hsl(${avatarHue(row.name)} 45% 45%)` }}
              >
                {initials(row.name)}
              </span>
              <div className="min-w-0 w-40 shrink-0">
                <div className="font-medium text-ink-100 truncate">{row.name}</div>
                <div
                  data-testid={`ws-entities-count-${i}`}
                  className="font-mono text-[10.5px] text-ink-500"
                >
                  {kind === 'characters'
                    ? t('projects.workspace.entities.cueCount', {
                        count: (row as { cue_count: number }).cue_count,
                      })
                    : t('projects.workspace.entities.sceneCount', {
                        count: (row as { scene_count: number }).scene_count,
                      })}
                </div>
              </div>
              <div className="flex items-center gap-1 flex-wrap flex-1 min-w-0">
                {row.episode_ids.map((epId) => (
                  <span
                    key={epId}
                    data-testid={`ws-entities-badge-${i}-${epId}`}
                    className="text-[10px] text-indigo-300 bg-indigo-500/10 rounded-full px-2 py-0.5 font-medium whitespace-nowrap"
                  >
                    {epLabel(epId)}
                  </span>
                ))}
              </div>
              {i === 0 && (
                <span className="text-[10.5px] text-ink-500 shrink-0 whitespace-nowrap">
                  {t('projects.workspace.entities.reusableNote')}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default WorkspaceEntities;
