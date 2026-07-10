/**
 * ProjectWorkspace — the PR-10b/PR-11 workspace shell (spec
 * `2026-07-10-projects-workspace-final.html`, decisions G1/G2/G4/G6/G12/G13).
 * Flag-gated by VITE_FEATURE_PROJECT_WORKSPACE_V2 at the ProjectsPage call
 * site; this component owns everything inside the detail pane once the
 * flag is on: the top project bar, the scoped sidebar, and the module
 * content area. Script/Storyboard mount `EditorShell` INLINE (PR-11) — no
 * more deep-link route jump — with the editor's own left rail acting as the
 * script-level nav underneath this project-level sidebar; every other
 * sidebar item without real content yet renders `WorkspacePlaceholder`.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  fetchCurrentStage,
  fetchEpisodesProgress,
  fetchStageCatalog,
  fetchStageSuggestion,
  generateMissingFrames,
  setCurrentStage,
} from '../../services/projectsService';
import { fetchScriptProjects } from '../../services/scriptService';
import { useToast } from '../Toast';
import { useAuth } from '../../contexts/AuthContext';
import { ProjectTrashView } from '../ProjectTrashView';
import { ProjectSettingsPanel } from '../ProjectSettingsPanel';
import { EditorShell } from '../../editor/components/EditorShell';
import type { RailView } from '../../editor/components/RailModules';
import { WorkspaceSidebar } from './WorkspaceSidebar';
import { WorkspaceTopBar } from './WorkspaceTopBar';
import { WorkspaceOverview } from './WorkspaceOverview';
import { WorkspaceEpisodes } from './WorkspaceEpisodes';
import { WorkspaceEntities } from './WorkspaceEntities';
import { WorkspaceFiles, type FilesChip } from './WorkspaceFiles';
import { WorkspacePlaceholder } from './WorkspacePlaceholder';
import { episodeStorageKey, type WorkspaceModule } from './workspaceModules';
import type {
  EpisodeProgress,
  Project,
  ProjectStage,
  ProjectTab,
  StageSuggestion as StageSuggestionData,
} from '../../types';

interface ProjectWorkspaceProps {
  project: Project;
  teamId?: string;
  onBack: () => void;
  canWrite?: boolean;
  /** Settings module saved a project field — bubble the fresh row up so the caller can update its own state. */
  onProjectUpdated?: (project: Project) => void;
}

// A suggestion action's `tab` targets the legacy ProjectTab set — the
// workspace shell has no 1:1 equivalent for every one of those (scripts /
// storyboard live inside the current episode's editor now; output/shares
// fold into the Files module for Wave 1). Map each to its workspace
// counterpart so StageSuggestion's ghost CTA still does something sane.
function suggestionTabToModule(tab: ProjectTab | null | undefined): WorkspaceModule {
  if (tab === 'trash') return 'trash';
  return 'files';
}

export function ProjectWorkspace({
  project,
  teamId,
  onBack,
  canWrite = true,
  onProjectUpdated,
}: ProjectWorkspaceProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  // Same identity source as the standalone editor route (pages/ScriptEditor)
  // — the inline-mounted EditorShell needs it for collaboration presence.
  const { currentUserId, userProfile } = useAuth();

  const [activeModule, setActiveModule] = useState<WorkspaceModule>('overview');

  // ── SOP stage (catalog + current + advance/jump) ──────────────────────
  const [catalog, setCatalog] = useState<ProjectStage[]>([]);
  const [currentStage, setCurrentStageState] = useState<ProjectStage | null>(null);
  const [advancing, setAdvancing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchStageCatalog()
      .then((stages) => {
        if (!cancelled) setCatalog(stages);
      })
      .catch((err) => console.error('[ProjectWorkspace] failed to load stage catalog:', err));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCurrentStage(project.id)
      .then((stage) => {
        if (!cancelled) setCurrentStageState(stage);
      })
      .catch((err) => console.error('[ProjectWorkspace] failed to load current stage:', err));
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  const currentIndex = catalog.findIndex((s) => s.id === currentStage?.id);
  const nextStage =
    currentIndex >= 0 && currentIndex < catalog.length - 1 ? catalog[currentIndex + 1] : null;

  const jumpToStage = useCallback(
    async (stage: ProjectStage) => {
      if (advancing) return;
      setAdvancing(true);
      try {
        const updated = await setCurrentStage(project.id, stage.id);
        if (updated) setCurrentStageState(updated);
      } catch (err) {
        console.error('[ProjectWorkspace] failed to change stage:', err);
      } finally {
        setAdvancing(false);
      }
    },
    [advancing, project.id],
  );

  const handleAdvance = useCallback(() => {
    if (!nextStage) return;
    return jumpToStage(nextStage);
  }, [nextStage, jumpToStage]);

  // ── Stage suggestion (top-bar inline CTA) ──────────────────────────────
  const [suggestion, setSuggestion] = useState<StageSuggestionData | null>(null);
  const [suggestionBusy, setSuggestionBusy] = useState(false);

  const loadSuggestion = useCallback(() => {
    fetchStageSuggestion(project.id)
      .then(setSuggestion)
      .catch((err) => {
        console.error('[ProjectWorkspace] failed to load stage suggestion:', err);
        setSuggestion(null);
      });
  }, [project.id]);

  useEffect(() => {
    loadSuggestion();
  }, [loadSuggestion, currentStage?.slug]);

  const handleSuggestionGenerate = useCallback(async () => {
    if (suggestionBusy) return;
    setSuggestionBusy(true);
    try {
      const res = await generateMissingFrames(project.id);
      addToast(t('projects.suggest.generating', { count: res.dispatched_count }), 'success');
      loadSuggestion();
    } catch (err) {
      console.error('[ProjectWorkspace] generate-missing failed:', err);
      addToast(t('common.error'), 'error');
    } finally {
      setSuggestionBusy(false);
    }
  }, [suggestionBusy, project.id, addToast, t, loadSuggestion]);

  // ── Episodes (sidebar current-episode block + switcher) ────────────────
  const [episodes, setEpisodes] = useState<EpisodeProgress[]>([]);
  const [currentEpisodeId, setCurrentEpisodeId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEpisodes([]);
    setCurrentEpisodeId(null);
    fetchEpisodesProgress(project.id)
      .then((rows) => {
        if (cancelled) return;
        setEpisodes(rows);
        const sorted = [...rows].sort((a, b) => a.sort_order - b.sort_order);
        const fallback = sorted[0]?.episode_id ?? null;
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(episodeStorageKey(project.id));
        } catch (err) {
          console.error('[ProjectWorkspace] failed to read stored episode:', err);
        }
        const valid = stored && rows.some((r) => r.episode_id === stored) ? stored : fallback;
        setCurrentEpisodeId(valid);
      })
      .catch((err) => console.error('[ProjectWorkspace] failed to load episodes progress:', err));
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  const currentEpisode = episodes.find((e) => e.episode_id === currentEpisodeId) ?? null;

  const handleEpisodeChange = useCallback(
    (episodeId: string) => {
      setCurrentEpisodeId(episodeId);
      try {
        localStorage.setItem(episodeStorageKey(project.id), episodeId);
      } catch (err) {
        console.error('[ProjectWorkspace] failed to persist episode selection:', err);
      }
    },
    [project.id],
  );

  // Re-fetch the progress feed after a create/rename/reorder/delete in the
  // Episodes management module. Keeps the current selection when it still
  // exists; otherwise falls back to the lowest sort_order episode (mirrors
  // the initial-load fallback below, without touching that pinned effect).
  const refetchEpisodes = useCallback(() => {
    return fetchEpisodesProgress(project.id)
      .then((rows) => {
        setEpisodes(rows);
        setCurrentEpisodeId((cur) => {
          if (cur && rows.some((r) => r.episode_id === cur)) return cur;
          const sorted = [...rows].sort((a, b) => a.sort_order - b.sort_order);
          return sorted[0]?.episode_id ?? null;
        });
      })
      .catch((err) => console.error('[ProjectWorkspace] failed to refresh episodes progress:', err));
  }, [project.id]);

  // ── Script/Storyboard inline mount (PR-11): resolve a given episode's most
  // recently updated script and mount EditorShell directly in the content
  // area (no more route jump); no script → fall back to the Episodes
  // management module so the user can start one. Parameterized (rather than
  // always reading `currentEpisode`) so the Episodes module's Open/Start CTA
  // can switch episode + open its script in one action. `railView` presets
  // which centre-pane view the shell opens on — 'storyboard' for the
  // sidebar's Storyboard child, undefined (script sheet / stored pref) for
  // Script. ───
  const [resolvedScriptId, setResolvedScriptId] = useState<string | null>(null);
  const [scriptInitialRailView, setScriptInitialRailView] = useState<RailView | undefined>(
    undefined,
  );

  const openEpisodeScript = useCallback(
    async (episode: EpisodeProgress | null, railView?: RailView) => {
      if (!episode) {
        setActiveModule('episodes');
        return;
      }
      try {
        const result = await fetchScriptProjects(project.id);
        const candidates = (result.data ?? []).filter(
          (s) => String(s.episode_id ?? '') === String(episode.episode_id),
        );
        if (candidates.length === 0) {
          setActiveModule('episodes');
          return;
        }
        candidates.sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        );
        setResolvedScriptId(candidates[0].id);
        setScriptInitialRailView(railView);
        setActiveModule('script');
      } catch (err) {
        console.error('[ProjectWorkspace] failed to resolve current episode script:', err);
        setActiveModule('episodes');
      }
    },
    [project.id],
  );

  const openCurrentEpisodeScript = useCallback(
    () => openEpisodeScript(currentEpisode),
    [openEpisodeScript, currentEpisode],
  );

  const openCurrentEpisodeStoryboard = useCallback(
    () => openEpisodeScript(currentEpisode, 'storyboard'),
    [openEpisodeScript, currentEpisode],
  );

  const handleOpenEpisode = useCallback(
    (episodeId: string) => {
      handleEpisodeChange(episodeId);
      const ep = episodes.find((e) => e.episode_id === episodeId) ?? null;
      void openEpisodeScript(ep);
    },
    [handleEpisodeChange, episodes, openEpisodeScript],
  );

  // If the writer switches episodes (⇄ switcher) while the script module is
  // open, re-resolve for the newly-selected episode rather than leaving a
  // stale script mounted under the new episode's sidebar context.
  const prevEpisodeIdRef = useRef(currentEpisodeId);
  useEffect(() => {
    const changed = prevEpisodeIdRef.current !== currentEpisodeId;
    prevEpisodeIdRef.current = currentEpisodeId;
    if (changed && activeModule === 'script') {
      void openEpisodeScript(currentEpisode, scriptInitialRailView);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-resolve on an episode-id change, not on every render of the other deps
  }, [currentEpisodeId]);

  const handleSuggestionNavigate = useCallback(
    (tab: ProjectTab) => {
      if (tab === 'scripts') {
        void openCurrentEpisodeScript();
        return;
      }
      if (tab === 'storyboard') {
        void openCurrentEpisodeStoryboard();
        return;
      }
      setActiveModule(suggestionTabToModule(tab));
    },
    [openCurrentEpisodeScript, openCurrentEpisodeStoryboard],
  );

  // ── Files module chip/episode-filter handoff (renders sidebar child) ──
  const [filesInitialChip, setFilesInitialChip] = useState<FilesChip>('all');
  const [filesEpFilterOn, setFilesEpFilterOn] = useState(false);
  // Bumped on every entry into Files so `key`-ing WorkspaceFiles on it
  // forces a remount (and re-applies the initial chip/filter props) even
  // when the user re-clicks "Renders" while already in the Files module.
  const [filesEntryToken, setFilesEntryToken] = useState(0);

  const handleModuleChange = useCallback((module: WorkspaceModule) => {
    if (module === 'files') {
      setFilesInitialChip('all');
      setFilesEpFilterOn(false);
      setFilesEntryToken((n) => n + 1);
    }
    setActiveModule(module);
  }, []);

  const handleOpenRenders = useCallback(() => {
    setFilesInitialChip('renders');
    setFilesEpFilterOn(true);
    setFilesEntryToken((n) => n + 1);
    setActiveModule('files');
  }, []);

  return (
    <div data-testid="project-workspace" className="flex h-full min-h-0">
      <WorkspaceSidebar
        activeModule={activeModule}
        onModuleChange={handleModuleChange}
        episodes={episodes}
        currentEpisode={currentEpisode}
        onEpisodeChange={handleEpisodeChange}
        onOpenScript={() => void openCurrentEpisodeScript()}
        onOpenStoryboard={() => void openCurrentEpisodeStoryboard()}
        onOpenRenders={handleOpenRenders}
      />
      <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
        <WorkspaceTopBar
          projectName={project.name}
          onBack={onBack}
          catalog={catalog}
          currentStage={currentStage}
          currentIndex={currentIndex}
          canWrite={canWrite}
          advancing={advancing}
          onJumpStage={jumpToStage}
          nextStage={nextStage}
          onAdvance={handleAdvance}
          suggestion={suggestion}
          suggestionBusy={suggestionBusy}
          onSuggestionGenerate={handleSuggestionGenerate}
          onSuggestionNavigate={handleSuggestionNavigate}
        />
        {activeModule === 'script' && resolvedScriptId ? (
          // Full-bleed: EditorShell manages its own internal layout/scroll
          // (`.mh-editor-shell { position:absolute; inset:0 }`), so this
          // wrapper only needs to be a sized, positioned box — no padding,
          // no `overflow-y-auto` (that would create a second scrollbar on
          // top of the editor's own scene-sheet scroll).
          <div data-testid="ws-script-editor" className="flex-1 min-h-0 relative overflow-hidden">
            <EditorShell
              scriptId={resolvedScriptId}
              currentUserId={currentUserId}
              currentUserName={userProfile.name}
              initialRailView={scriptInitialRailView}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-6 pb-8">
            {activeModule === 'overview' && (
              <WorkspaceOverview
                project={project}
                projectId={project.id}
                currentStage={currentStage}
                episodes={episodes}
                currentEpisode={currentEpisode}
                onOpenScript={() => void openCurrentEpisodeScript()}
                onSuggestionNavigate={handleSuggestionNavigate}
              />
            )}
            {activeModule === 'episodes' && (
              <WorkspaceEpisodes
                projectId={project.id}
                episodes={episodes}
                onEpisodesChanged={() => void refetchEpisodes()}
                onOpenEpisode={handleOpenEpisode}
              />
            )}
            {(activeModule === 'characters' || activeModule === 'locations') && (
              <WorkspaceEntities kind={activeModule} projectId={project.id} episodes={episodes} />
            )}
            {activeModule === 'files' && (
              <WorkspaceFiles
                key={filesEntryToken}
                projectId={project.id}
                currentEpisode={currentEpisode}
                initialChip={filesInitialChip}
                initialEpisodeFilterOn={filesEpFilterOn}
              />
            )}
            {activeModule === 'trash' && <ProjectTrashView projectId={project.id} />}
            {activeModule === 'settings' && (
              <ProjectSettingsPanel
                project={project}
                isOpen
                onClose={() => setActiveModule('overview')}
                onUpdated={(updated) => {
                  onProjectUpdated?.(updated);
                  setActiveModule('overview');
                }}
                onDeleted={onBack}
              />
            )}
            {activeModule === 'canvas' && <WorkspacePlaceholder module="canvas" />}
          </div>
        )}
      </div>
    </div>
  );
}

export default ProjectWorkspace;
