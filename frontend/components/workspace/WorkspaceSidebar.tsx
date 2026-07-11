/**
 * WorkspaceSidebar — the scoped project sidebar for the PR-10b workspace
 * shell (spec frames: "内部" section, decision G4). Renders Overview /
 * Canvas / Episodes at the top, a bordered current-episode card with its
 * Script/Storyboard/Renders children indented underneath, then the ASSETS
 * and MANAGE groups. Only the current episode's children are rendered —
 * the ⇄ switcher opens a compact popover instead of listing every episode
 * inline, so the sidebar's height never grows with the episode count.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeftRight, ChevronDown, FileText, Clapperboard, Film, ExternalLink } from 'lucide-react';
import type { EpisodeProgress } from '../../types';
import { TOP_MODULES, ASSET_MODULES, MANAGE_MODULES, type WorkspaceModule } from './workspaceModules';

interface WorkspaceSidebarProps {
  activeModule: WorkspaceModule;
  onModuleChange: (module: WorkspaceModule) => void;
  episodes: EpisodeProgress[];
  currentEpisode: EpisodeProgress | null;
  onEpisodeChange: (episodeId: string) => void;
  /** Script child clicked — resolve + open the episode's script editor (script view). */
  onOpenScript: () => void;
  /** Storyboard child clicked — resolve + open the episode's script editor, preset to the storyboard view. */
  onOpenStoryboard: () => void;
  /** Renders child clicked — switch to Files with the Renders chip + current-episode filter preset. */
  onOpenRenders: () => void;
}

function sideItemClass(active: boolean): string {
  return `flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] transition-colors w-full text-left ${
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
  onOpenScript,
  onOpenStoryboard,
  onOpenRenders,
}: WorkspaceSidebarProps) {
  const { t } = useTranslation();
  const [switcherOpen, setSwitcherOpen] = useState(false);
  // Popover position in viewport coordinates. The popover is `fixed` (anchored
  // to the ep-card's rect) instead of `absolute`, for two reasons: the old
  // `top-full` resolved against the whole episode block (card + child rows),
  // dropping the menu below 发布 instead of below the card; and the sidebar is
  // an overflow-y-auto scroller, which would clip a 224px-wide absolute child.
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number } | null>(null);
  const switcherRef = useRef<HTMLDivElement>(null);
  const epCardRef = useRef<HTMLButtonElement>(null);

  const closeSwitcher = useCallback(() => setSwitcherOpen(false), []);

  const toggleSwitcher = useCallback(() => {
    setSwitcherOpen((open) => {
      if (open) return false;
      const rect = epCardRef.current?.getBoundingClientRect();
      if (!rect) return false;
      setPopoverPos({ top: rect.bottom + 4, left: rect.left });
      return true;
    });
  }, []);

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

  return (
    <div
      data-testid="workspace-sidebar"
      className="w-[168px] shrink-0 border-r border-line py-2 px-2 flex flex-col gap-0.5 overflow-y-auto"
    >
      {TOP_MODULES.map((mod) => {
        const Icon = mod.icon;
        const count =
          mod.key === 'episodes'
            ? episodes.length
            : mod.key === 'canvas'
              ? null // spec: Canvas has no derivable count yet ("—")
              : undefined;
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
            {mod.key === 'canvas' ? (
              <span className="text-[10px] text-ink-600 font-mono">—</span>
            ) : (
              count != null && (
                <span className="text-[10px] text-ink-500 font-mono">{count}</span>
              )
            )}
          </button>
        );
      })}

      {currentEpisode && (
        <div className="mt-2" ref={switcherRef}>
          <button
            type="button"
            ref={epCardRef}
            data-testid="ws-ep-card"
            onClick={toggleSwitcher}
            className="flex items-center justify-between gap-2 w-full rounded-lg border border-line-strong px-2.5 py-1.5 text-[12.5px] font-medium text-ink-100 hover:border-ink-500 transition-colors"
          >
            <span className="flex items-center gap-1.5 min-w-0">
              <ChevronDown size={12} className="shrink-0 text-ink-500" />
              <span className="truncate">{currentEpisode.title}</span>
            </span>
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

          <div className="mt-0.5 flex flex-col gap-0.5">
            <button
              data-testid="ws-ep-script"
              onClick={onOpenScript}
              className="flex items-center gap-2 rounded-md pl-6 pr-2.5 py-1.5 text-[12.5px] text-ink-300 hover:text-ink-100 hover:bg-ink-800/50 transition-colors text-left"
            >
              <FileText size={13} className="shrink-0" />
              <span className="truncate">{t('projects.workspace.modules.script')}</span>
            </button>
            <button
              data-testid="ws-ep-storyboard"
              onClick={onOpenStoryboard}
              className="flex items-center justify-between gap-2 rounded-md pl-6 pr-2.5 py-1.5 text-[12.5px] text-ink-300 hover:text-ink-100 hover:bg-ink-800/50 transition-colors text-left"
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
              data-testid="ws-ep-renders"
              onClick={onOpenRenders}
              className="flex items-center justify-between gap-2 rounded-md pl-6 pr-2.5 py-1.5 text-[12.5px] text-ink-300 hover:text-ink-100 hover:bg-ink-800/50 transition-colors text-left"
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
          </div>
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
