/**
 * WorkspaceSidebar — the unified project sidebar (合一终稿, 2026-07-11). One
 * left menu tree: Overview / Canvas at the top, then an EXPANDABLE 剧集
 * (Episodes) item whose work views — 剧本 / 节拍 / 分镜 / 场景 / 成片 / 发布 —
 * live indented underneath it, plus a live SCENES sub-section (lifted out of
 * the embedded editor) when a script is open. ASSETS and MANAGE groups follow.
 *
 * The whole thing stays mounted even while the studio editor is embedded — the
 * editor drops its own left rail in embedded mode (EditorShell), so this tree
 * is the single side navigation. The current-episode ⇄ card opens a compact
 * fixed popover (never grows the sidebar height with episode count).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeftRight,
  ChevronDown,
  ChevronRight,
  LayoutDashboard,
  Frame,
  ListVideo,
  FileText,
  ListMusic,
  Clapperboard,
  LayoutGrid,
  Film,
  ExternalLink,
} from 'lucide-react';
import type { EpisodeProgress } from '../../types';
import { ASSET_MODULES, MANAGE_MODULES, type WorkspaceModule } from './workspaceModules';

/** The episode-scoped work views the tree can open (maps to editor RailView). */
export type WorkView = 'script' | 'beats' | 'storyboard' | 'nodes';

export interface WorkspaceSidebarScene {
  scene_id: string;
  int_ext: string | null;
  label: string;
}

interface WorkspaceSidebarProps {
  activeModule: WorkspaceModule;
  onModuleChange: (module: WorkspaceModule) => void;
  episodes: EpisodeProgress[];
  currentEpisode: EpisodeProgress | null;
  onEpisodeChange: (episodeId: string) => void;
  /** The active work view while `activeModule === 'script'`; null otherwise. */
  activeWorkView: WorkView | null;
  /** Open one of the episode's work views (剧本 / 节拍 / 分镜 / 场景). */
  onOpenWorkView: (view: WorkView) => void;
  onOpenRenders: () => void;
  /** Lifted scene list (from the embedded editor); null → the section hides. */
  scenes: WorkspaceSidebarScene[] | null;
  activeSceneId: string | null;
  onSelectScene: (sceneId: string) => void;
}

function sideItemClass(active: boolean): string {
  return `flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] transition-colors w-full text-left ${
    active
      ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] font-medium'
      : 'text-ink-300 hover:text-ink-100 hover:bg-ink-800/50'
  }`;
}

/** Shared class for the six indented work-view child rows. */
function childRowClass(active: boolean): string {
  return `flex items-center justify-between gap-2 rounded-md pl-6 pr-2.5 py-1.5 text-[12.5px] transition-colors text-left w-full ${
    active
      ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] font-medium'
      : 'text-ink-300 hover:text-ink-100 hover:bg-ink-800/50'
  }`;
}

export function WorkspaceSidebar({
  activeModule,
  onModuleChange,
  episodes,
  currentEpisode,
  onEpisodeChange,
  activeWorkView,
  onOpenWorkView,
  onOpenRenders,
  scenes,
  activeSceneId,
  onSelectScene,
}: WorkspaceSidebarProps) {
  const { t } = useTranslation();
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [epExpanded, setEpExpanded] = useState(
    activeModule === 'episodes' || activeModule === 'script',
  );
  // Popover position in viewport coordinates. The popover is `fixed` (anchored
  // to the ep-card's rect) instead of `absolute`, for two reasons: the old
  // `top-full` resolved against the whole episode block (card + child rows),
  // dropping the menu below 发布 instead of below the card; and the sidebar is
  // an overflow-y-auto scroller, which would clip a 224px-wide absolute child.
  //
  // It flies out to the RIGHT of the card, not below it: dropping down covers
  // the work-view rows, and because outside-mousedown closes the menu without
  // delivering the click, every control under it needed two clicks — in
  // practice "点不动" (prod feedback 2026-07-11).
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number } | null>(null);
  const switcherRef = useRef<HTMLDivElement>(null);
  const epCardRef = useRef<HTMLButtonElement>(null);

  // Auto-reveal the tree once the writer is inside an episode/studio context
  // (e.g. the Overview "Open Studio" button jumps straight to the script view);
  // a manual collapse otherwise sticks.
  useEffect(() => {
    if (activeModule === 'script' || activeModule === 'episodes') setEpExpanded(true);
  }, [activeModule]);

  const closeSwitcher = useCallback(() => setSwitcherOpen(false), []);

  const toggleSwitcher = useCallback(() => {
    setSwitcherOpen((open) => {
      if (open) return false;
      const rect = epCardRef.current?.getBoundingClientRect();
      if (!rect) return false;
      setPopoverPos({ top: rect.top, left: rect.right + 6 });
      return true;
    });
  }, []);

  const handleEpisodesRowClick = useCallback(() => {
    if (epExpanded && activeModule === 'episodes') {
      setEpExpanded(false);
      return;
    }
    setEpExpanded(true);
    onModuleChange('episodes');
  }, [epExpanded, activeModule, onModuleChange]);

  useEffect(() => {
    if (!switcherOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (switcherRef.current && !switcherRef.current.contains(e.target as Node)) {
        closeSwitcher();
      }
    };
    // A fixed-position popover detaches from its anchor the moment anything
    // scrolls or the window resizes — close instead of chasing the rect.
    const handleScroll = () => closeSwitcher();
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeSwitcher();
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    window.addEventListener('scroll', handleScroll, true);
    window.addEventListener('resize', handleScroll);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
      window.removeEventListener('scroll', handleScroll, true);
      window.removeEventListener('resize', handleScroll);
    };
  }, [switcherOpen, closeSwitcher]);

  const isScript = activeModule === 'script';

  return (
    <div
      data-testid="workspace-sidebar"
      className="w-[168px] shrink-0 border-r border-line py-2 px-2 flex flex-col gap-0.5 overflow-y-auto"
    >
      <button
        data-testid="ws-module-overview"
        onClick={() => onModuleChange('overview')}
        className={sideItemClass(activeModule === 'overview')}
      >
        <span className="flex items-center gap-2 min-w-0">
          <LayoutDashboard size={14} className="shrink-0" />
          <span className="truncate">{t('projects.workspace.modules.overview')}</span>
        </span>
      </button>
      <button
        data-testid="ws-module-canvas"
        onClick={() => onModuleChange('canvas')}
        className={sideItemClass(activeModule === 'canvas')}
      >
        <span className="flex items-center gap-2 min-w-0">
          <Frame size={14} className="shrink-0" />
          <span className="truncate">{t('projects.workspace.modules.canvas')}</span>
        </span>
        <span className="text-[10px] text-ink-600 font-mono">—</span>
      </button>

      {/* ── 剧集 (Episodes) — expandable tree node ── */}
      <button
        data-testid="ws-module-episodes"
        onClick={handleEpisodesRowClick}
        aria-expanded={epExpanded}
        className={sideItemClass(activeModule === 'episodes')}
      >
        <span className="flex items-center gap-2 min-w-0">
          <ListVideo size={14} className="shrink-0" />
          <span className="truncate">{t('projects.workspace.modules.episodes')}</span>
        </span>
        {/* The expand chevron sits at the row's RIGHT end (beside the count),
            so the leading ListVideo icon stays in the same left column as every
            other nav row instead of being pushed right by the chevron. */}
        <span className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-ink-500 font-mono">{episodes.length}</span>
          {epExpanded ? (
            <ChevronDown size={12} className="shrink-0 text-ink-500" />
          ) : (
            <ChevronRight size={12} className="shrink-0 text-ink-500" />
          )}
        </span>
      </button>

      {epExpanded && currentEpisode && (
        <div className="mt-0.5 flex flex-col gap-0.5" ref={switcherRef}>
          <button
            type="button"
            ref={epCardRef}
            data-testid="ws-ep-card"
            onClick={toggleSwitcher}
            className="ml-2 flex items-center justify-between gap-2 rounded-lg border border-line-strong px-2.5 py-1.5 text-[12.5px] font-medium text-ink-100 hover:border-ink-500 transition-colors"
          >
            <span className="truncate">{currentEpisode.title}</span>
            <ArrowLeftRight
              data-testid="ws-ep-switch"
              size={12}
              className="shrink-0 text-ink-500"
            />
          </button>

          {switcherOpen && popoverPos && (
            <div
              data-testid="ws-ep-popover"
              style={{ top: popoverPos.top, left: popoverPos.left }}
              className="fixed z-50 w-56 rounded-lg border border-line-strong bg-island shadow-2xl py-1 max-h-64 overflow-y-auto"
            >
              {episodes.map((ep) => (
                <button
                  key={ep.episode_id}
                  data-testid={`ws-ep-option-${ep.episode_id}`}
                  onClick={() => {
                    onEpisodeChange(ep.episode_id);
                    closeSwitcher();
                  }}
                  className={`flex items-center justify-between gap-2 w-full px-3 py-1.5 text-[12.5px] text-left transition-colors ${
                    ep.episode_id === currentEpisode.episode_id
                      ? 'text-[var(--accent-text)] bg-[var(--accent-soft)]'
                      : 'text-ink-300 hover:text-ink-100 hover:bg-ink-800'
                  }`}
                >
                  <span className="truncate">{ep.title}</span>
                  <span className="shrink-0 text-[10px] rounded-full bg-ink-800 text-ink-400 px-1.5 py-0.5">
                    {t(`projects.workspace.episodeStatus.${ep.status}`, ep.status)}
                  </span>
                </button>
              ))}
            </div>
          )}

          <button
            data-testid="ws-ep-script"
            onClick={() => onOpenWorkView('script')}
            className={childRowClass(isScript && activeWorkView === 'script')}
          >
            <span className="flex items-center gap-2 min-w-0">
              <FileText size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.script')}</span>
            </span>
          </button>
          <button
            data-testid="ws-ep-beats"
            onClick={() => onOpenWorkView('beats')}
            className={childRowClass(isScript && activeWorkView === 'beats')}
          >
            <span className="flex items-center gap-2 min-w-0">
              <ListMusic size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.beats')}</span>
            </span>
          </button>
          <button
            data-testid="ws-ep-storyboard"
            onClick={() => onOpenWorkView('storyboard')}
            className={childRowClass(isScript && activeWorkView === 'storyboard')}
          >
            <span className="flex items-center gap-2 min-w-0">
              <Clapperboard size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.storyboard')}</span>
            </span>
            <span className="text-[10px] text-ink-500 font-mono shrink-0">
              {currentEpisode.shots_done}/{currentEpisode.shots_total}
            </span>
          </button>
          <button
            data-testid="ws-ep-scenes"
            onClick={() => onOpenWorkView('nodes')}
            className={childRowClass(isScript && activeWorkView === 'nodes')}
          >
            <span className="flex items-center gap-2 min-w-0">
              <LayoutGrid size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.scenes')}</span>
            </span>
            <span className="text-[10px] text-ink-500 font-mono shrink-0">
              {currentEpisode.scene_count}
            </span>
          </button>
          <button
            data-testid="ws-ep-renders"
            onClick={onOpenRenders}
            className={childRowClass(false)}
          >
            <span className="flex items-center gap-2 min-w-0">
              <Film size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.renders')}</span>
            </span>
            <span className="text-[10px] text-ink-500 font-mono shrink-0">
              {currentEpisode.renders_count}
            </span>
          </button>
          {/* Publish placeholder (PR-11, G12) — arrives with Distribution D2;
              disabled here so the product surface is legible ahead of time. */}
          <button
            type="button"
            data-testid="ws-ep-publish"
            disabled
            title={t('projects.workspace.publish.comingSoon')}
            className="flex items-center gap-2 rounded-md pl-6 pr-2.5 py-1.5 text-[12.5px] text-ink-600 cursor-not-allowed text-left"
          >
            <ExternalLink size={13} className="shrink-0" />
            <span className="truncate">{t('projects.workspace.modules.publish')}</span>
          </button>

          {/* ── SCENES sub-section (lifted from the embedded editor) ── */}
          {scenes && scenes.length > 0 && (
            <div className="mt-1 pt-1.5 border-t border-line">
              <div className="pl-6 pb-1 font-mono text-[8.5px] tracking-widest text-ink-500">
                SCENES · {scenes.length}
              </div>
              <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto">
                {scenes.map((sc, i) => {
                  const active = activeSceneId != null && sc.scene_id === activeSceneId;
                  return (
                    <button
                      key={sc.scene_id}
                      data-testid={`ws-scene-${sc.scene_id}`}
                      onClick={() => onSelectScene(sc.scene_id)}
                      className={`flex items-center gap-1.5 pr-2 py-1 text-[12px] text-left transition-colors ${
                        active
                          ? 'bg-[var(--accent-soft)] rounded-r-md border-l-2 border-[var(--accent-text)] pl-[22px]'
                          : 'pl-6 hover:bg-ink-800/50'
                      }`}
                    >
                      <span
                        className={`font-mono font-bold shrink-0 ${
                          active ? 'text-[var(--accent-text)]' : 'text-ink-300'
                        }`}
                      >
                        S{i + 1}
                      </span>
                      {sc.int_ext && (
                        <span className="shrink-0 font-mono text-[8.5px] rounded px-1 py-0.5 text-violet-400 bg-violet-500/10">
                          {sc.int_ext}
                        </span>
                      )}
                      <span className="truncate text-ink-300">{sc.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 px-2.5 text-[10px] uppercase tracking-wider text-ink-600">
        {t('projects.workspace.assets')}
      </div>
      {ASSET_MODULES.map((mod) => {
        const Icon = mod.icon;
        return (
          <button
            key={mod.key}
            data-testid={`ws-module-${mod.key}`}
            onClick={() => onModuleChange(mod.key)}
            className={sideItemClass(activeModule === mod.key)}
          >
            <span className="flex items-center gap-2 min-w-0">
              <Icon size={14} className="shrink-0" />
              <span className="truncate">{t(mod.labelKey)}</span>
            </span>
          </button>
        );
      })}

      <div className="mt-3 px-2.5 text-[10px] uppercase tracking-wider text-ink-600">
        {t('projects.workspace.manage')}
      </div>
      {MANAGE_MODULES.map((mod) => {
        const Icon = mod.icon;
        return (
          <button
            key={mod.key}
            data-testid={`ws-module-${mod.key}`}
            onClick={() => onModuleChange(mod.key)}
            className={sideItemClass(activeModule === mod.key)}
          >
            <span className="flex items-center gap-2 min-w-0">
              <Icon size={14} className="shrink-0" />
              <span className="truncate">{t(mod.labelKey)}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default WorkspaceSidebar;
