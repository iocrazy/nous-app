/**
 * ProjectWorkspace — the unified workspace shell (合一终稿, 2026-07-11; spec
 * `2026-07-10-projects-workspace-final.html`, decisions G1/G2/G4/G6/G12/G13).
 * Flag-gated by VITE_FEATURE_PROJECT_WORKSPACE_V2 at the ProjectsPage call
 * site; this component owns everything inside the detail pane once the flag is
 * on: the top project bar, the single left-tree sidebar, and the module
 * content area.
 *
 * The sidebar stays mounted at ALL times — including while Script/Storyboard
 * mount `EditorShell` INLINE. The embedded editor drops its own left rail
 * (EditorShell `embedded`), so this tree is the single side navigation: the
 * episode's work views (剧本/节拍/分镜/场景) hang off the 剧集 node, and the
 * editor's scene list is lifted up into a SCENES sub-section. The SOP stage is
 * a read-only read-out in the top bar; advancing stages happens elsewhere.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  fetchCurrentStage,
  fetchEpisodesProgress,
  fetchStageCatalog,
} from '../../services/projectsService';
import {
  createScriptProject,
  fetchScriptProjects,
  updateScriptProject,
} from '../../services/scriptService';
import { useToast } from '../Toast';
import { useAuth } from '../../contexts/AuthContext';
import { ProjectTrashView } from '../ProjectTrashView';
import { ProjectSettingsPanel } from '../ProjectSettingsPanel';
import { EditorShell } from '../../editor/components/EditorShell';
import type { RailView } from '../../editor/components/RailModules';
import type { SceneDoc } from '../../editor/types';
import { WorkspaceSidebar, type WorkView } from './WorkspaceSidebar';
import { WorkspaceTopBar } from './WorkspaceTopBar';
import { WorkspaceOverview } from './WorkspaceOverview';
import { WorkspaceEpisodes } from './WorkspaceEpisodes';
import { WorkspaceEntities } from './WorkspaceEntities';
import { WorkspaceFiles, type FilesChip } from './WorkspaceFiles';
import { WorkspaceCanvas } from './WorkspaceCanvas';
import { episodeStorageKey, type WorkspaceModule } from './workspaceModules';
import type { EpisodeProgress, Project, ProjectStage } from '../../types';

/** Minimal scene shape lifted from the embedded editor for the SCENES sidebar. */
interface SceneLift {
  id: string;
  heading_int_ext: string | null;
  location_text: string | null;
}

interface ProjectWorkspaceProps {
  project: Project;
  teamId?: string;
  onBack: () => void;
  canWrite?: boolean;
  /** Settings module saved a project field — bubble the fresh row up so the caller can update its own state. */
  onProjectUpdated?: (project: Project) => void;
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

  // ── SOP stage (catalog + current, READ-ONLY here) ─────────────────────
  const [catalog, setCatalog] = useState<ProjectStage[]>([]);
  const [currentStage, setCurrentStageState] = useState<ProjectStage | null>(null);

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
  // exists; otherwise falls back to the lowest sort_order episode.
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

  // ── Studio (inline EditorShell) state ──────────────────────────────────
  // studioView drives BOTH which work view the sidebar highlights and which
  // centre-pane view the shell opens on. studioScenes/activeSceneId are lifted
  // out of the embedded editor to feed the sidebar's SCENES sub-section;
  // selectSceneRef lets a sidebar scene click reach back into the editor.
  const [resolvedScriptId, setResolvedScriptId] = useState<string | null>(null);
  const [studioView, setStudioView] = useState<RailView>('script');
  const [studioScenes, setStudioScenes] = useState<SceneLift[]>([]);
  const [studioActiveSceneId, setStudioActiveSceneId] = useState<string | null>(null);
  const selectSceneRef = useRef<((id: string) => void) | null>(null);

  // Resolve a given episode's most recently updated script and mount
  // EditorShell inline (no route jump); no script → provision an empty one the
  // same way project-create does, then mount it. `railView` presets the work
  // view (and the sidebar highlight). ───
  const openEpisodeScript = useCallback(
    async (episode: EpisodeProgress | null, railView: RailView = 'script') => {
      if (!episode) {
        setActiveModule('episodes');
        return;
      }
      setStudioView(railView);
      try {
        const result = await fetchScriptProjects(project.id);
        const candidates = (result.data ?? []).filter(
          (s) => String(s.episode_id ?? '') === String(episode.episode_id),
        );
        if (candidates.length === 0) {
          // Pre-epic projects have episodes with no script (mig353 backfilled
          // Episode 1 but only attached scripts that already existed) — the
          // old silent fall-back to the Episodes pane read as "点了没反应"
          // (prod feedback 2026-07-11). Provision an empty script the same
          // way project-create does, then mount it.
          const created = await createScriptProject({
            project_id: project.id,
            name: episode.title,
          });
          await updateScriptProject(created.id, { episode_id: episode.episode_id });
          setResolvedScriptId(created.id);
          setActiveModule('script');
          return;
        }
        candidates.sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        );
        setResolvedScriptId(candidates[0].id);
        setActiveModule('script');
      } catch (err) {
        console.error('[ProjectWorkspace] failed to resolve current episode script:', err);
        // Loud failure (was silent): the click otherwise appears to do nothing.
        addToast(t('common.error'), 'error');
        setActiveModule('episodes');
      }
    },
    [project.id, addToast, t],
  );

  const openCurrentEpisodeScript = useCallback(
    () => openEpisodeScript(currentEpisode),
    [openEpisodeScript, currentEpisode],
  );

  const handleOpenWorkView = useCallback(
    (view: WorkView) => {
      setStudioView(view);
      void openEpisodeScript(currentEpisode, view);
    },
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

  // If the writer switches episodes (⇄ card) while the script module is open,
  // re-resolve for the newly-selected episode rather than leaving a stale
  // script mounted, preserving the current work view.
  const prevEpisodeIdRef = useRef(currentEpisodeId);
  useEffect(() => {
    const changed = prevEpisodeIdRef.current !== currentEpisodeId;
    prevEpisodeIdRef.current = currentEpisodeId;
    if (changed && activeModule === 'script') {
      void openEpisodeScript(currentEpisode, studioView);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-resolve on an episode-id change, not on every render of the other deps
  }, [currentEpisodeId]);

  // Leaving the studio drops the lifted scene list so a later module render
  // never shows a stale SCENES section under the tree.
  useEffect(() => {
    if (activeModule !== 'script') {
      setStudioScenes([]);
      setStudioActiveSceneId(null);
    }
  }, [activeModule]);

  // Stable adapters (identity must not change per render, or EditorShell's
  // scene-sync effect would re-fire → setState loop).
  const handleScenesChange = useCallback((next: SceneDoc[]) => {
    setStudioScenes(
      next.map((s) => ({
        id: String(s.id),
        heading_int_ext: s.heading_int_ext,
        location_text: s.location_text,
      })),
    );
  }, []);
  const handleActiveSceneChange = useCallback((id: string | null) => {
    setStudioActiveSceneId(id == null ? null : String(id));
  }, []);

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

  const studioMode = activeModule === 'script' && resolvedScriptId != null;

  // Film slate read-out (studio only): 1-based episode + active-scene numbers.
  const epIdx = episodes.findIndex((e) => e.episode_id === currentEpisode?.episode_id);
  const epNumber = epIdx >= 0 ? epIdx + 1 : null;
  const sceneNumber =
    (studioActiveSceneId
      ? studioScenes.findIndex((s) => s.id === studioActiveSceneId) + 1
      : 0) || null;

  const sidebarScenes =
    activeModule === 'script' && studioScenes.length
      ? studioScenes.map((s) => ({
          scene_id: s.id,
          int_ext: s.heading_int_ext,
          label: s.location_text || t('editor.untitledScene'),
        }))
      : null;

  return (
    <div data-testid="project-workspace" className="flex h-full min-h-0">
      <WorkspaceSidebar
        activeModule={activeModule}
        onModuleChange={handleModuleChange}
        episodes={episodes}
        currentEpisode={currentEpisode}
        onEpisodeChange={handleEpisodeChange}
        activeWorkView={activeModule === 'script' ? studioView : null}
        onOpenWorkView={handleOpenWorkView}
        onOpenRenders={handleOpenRenders}
        scenes={sidebarScenes}
        activeSceneId={studioActiveSceneId}
        onSelectScene={(id) => selectSceneRef.current?.(id)}
      />
      <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
        <WorkspaceTopBar
          projectName={project.name}
          onBack={onBack}
          catalog={catalog}
          currentStage={currentStage}
          currentIndex={currentIndex}
          canWrite={canWrite}
          slate={activeModule === 'script' && epNumber ? { ep: epNumber, scene: sceneNumber } : null}
        />
        {studioMode && resolvedScriptId ? (
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
              projectId={project.id}
              initialRailView={studioView}
              embedded
              onScenesChange={handleScenesChange}
              onActiveSceneChange={handleActiveSceneChange}
              selectSceneRef={selectSceneRef}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-6 pb-8">
            {activeModule === 'overview' && (
              <WorkspaceOverview
                project={project}
                episodes={episodes}
                currentEpisode={currentEpisode}
                epNumber={epNumber}
                onOpenScript={() => void openCurrentEpisodeScript()}
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
            {activeModule === 'canvas' && (
              <WorkspaceCanvas projectId={project.id} teamId={teamId} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default ProjectWorkspace;
