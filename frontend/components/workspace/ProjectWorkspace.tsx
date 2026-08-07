/**
 * ProjectWorkspace — the unified workspace shell (合一终稿, 2026-07-11; spec
 * `2026-07-10-projects-workspace-final.html`, decisions G1/G2/G4/G6/G12/G13).
 * This is the only project detail implementation (the legacy nav sidebar +
 * stage strip + tab surface, and its VITE_FEATURE_PROJECT_WORKSPACE_V2 flag,
 * were retired in PR-18). It owns everything inside the detail pane: the top
 * project bar, the single left-tree sidebar, and the module content area.
 *
 * The sidebar stays mounted at ALL times — including while Script/Storyboard
 * mount `EditorShell` INLINE. The embedded editor drops its own left rail
 * (EditorShell `embedded`), so this tree is the single side navigation: the
 * episode's work views (剧本/节拍/分镜/场景) hang off the 剧集 node, and the
 * editor's scene list is lifted up into a SCENES sub-section. A workflow
 * project's node stepper is a read-only read-out in the top bar (G3 dropped
 * the legacy SOP `current_stage` read-out for No-workflow projects); advancing
 * stages happens elsewhere.
 */

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loading } from '../common/Loading';
import { fetchEpisodesProgress } from '../../services/projectsService';
import {
  createScriptProject,
  fetchScriptProjects,
} from '../../services/scriptService';
import { useToast } from '../Toast';
import { useAuth } from '../../contexts/AuthContext';
// Kept eager: ProjectsListView statically imports it too, so it lives in the
// ProjectsPage chunk regardless — a dynamic import here buys nothing and just
// trips Rollup's "dynamically + statically imported" warning.
import { ProjectSettingsPanel } from '../ProjectSettingsPanel';
import type { RailView } from '../../editor/components/RailModules';
import type { SceneDoc } from '../../editor/types';
import { WorkspaceSidebar, type WorkView } from './WorkspaceSidebar';
import { resolveSurface, viewsForNode } from './nodeSurface';
import { EpisodeViewTabs } from './EpisodeViewTabs';
import { WorkspaceTopBar } from './WorkspaceTopBar';
import { WorkspaceOverview } from './WorkspaceOverview';
import { AdvanceConfirmDialog } from '../workflow/AdvanceConfirmDialog';
import { useProjectWorkflow } from '../../hooks/useProjectWorkflow';
import { executeAdvance, fetchAdvancePreview } from '../../services/workflowService';
import { episodeStorageKey, type WorkspaceModule } from './workspaceModules';
import type { FilesChip } from './WorkspaceFiles';
import type { AdvancePreview, EpisodeProgress, Project, ProjectStageNode } from '../../types';

// Code-split the heavier / non-default modules out of the ProjectsPage chunk
// (PR-19). Overview is the landing module so it stays eager, as do the
// always-mounted sidebar + top bar. Everything below only mounts when its
// module (or the inline studio editor) is opened, so it loads on demand under
// the Suspense boundary in the content area — the editor in particular drags
// in the tiptap bundle, which no longer weighs on first paint of the shell.
const EditorShell = lazy(() =>
  import('../../editor/components/EditorShell').then((m) => ({ default: m.EditorShell })),
);
const WorkspaceEpisodes = lazy(() =>
  import('./WorkspaceEpisodes').then((m) => ({ default: m.WorkspaceEpisodes })),
);
const WorkspaceEntities = lazy(() =>
  import('./WorkspaceEntities').then((m) => ({ default: m.WorkspaceEntities })),
);
const WorkspaceFiles = lazy(() =>
  import('./WorkspaceFiles').then((m) => ({ default: m.WorkspaceFiles })),
);
const WorkspaceCanvas = lazy(() =>
  import('./WorkspaceCanvas').then((m) => ({ default: m.WorkspaceCanvas })),
);
const WorkspaceTasks = lazy(() =>
  import('./WorkspaceTasks').then((m) => ({ default: m.WorkspaceTasks })),
);
const WorkspaceStageBoard = lazy(() =>
  import('./WorkspaceStageBoard').then((m) => ({ default: m.WorkspaceStageBoard })),
);
const ProjectTrashView = lazy(() =>
  import('../ProjectTrashView').then((m) => ({ default: m.ProjectTrashView })),
);

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

  // Module state lives in the URL (?module=canvas): entering the canvas
  // editor and coming BACK (history/back-pill) must land on the same
  // module — a plain useState reset to Overview on every return.
  const [searchParams, setSearchParams] = useSearchParams();
  const initialModule = ((): WorkspaceModule => {
    const q = searchParams.get('module');
    const valid: WorkspaceModule[] = [
      'overview', 'canvas', 'episodes', 'tasks', 'script', 'characters',
      'locations', 'props', 'files', 'trash', 'settings', 'stage',
    ];
    return valid.includes(q as WorkspaceModule) ? (q as WorkspaceModule) : 'overview';
  })();
  const [activeModule, setActiveModule] = useState<WorkspaceModule>(initialModule);
  // The node whose Stage Board is open (`?module=stage&node={id}`, M2 PR-F F2).
  // Falls back to null (→ back to Overview) if a stage URL somehow lacks `node`.
  const [stageNodeId, setStageNodeId] = useState<string | null>(
    initialModule === 'stage' ? searchParams.get('node') : null,
  );
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (activeModule === 'overview') next.delete('module');
        else next.set('module', activeModule);
        if (activeModule === 'stage' && stageNodeId) next.set('node', stageNodeId);
        else next.delete('node');
        return next;
      },
      { replace: true },
    );
  }, [activeModule, stageNodeId, setSearchParams]);

  // ── Episodes (sidebar current-episode block + switcher) ────────────────
  // Declared BEFORE the workflow instance / advance callbacks below: they all
  // consume `currentEpisodeId` (B2 #1712 — the workflow read + advance chain are
  // scoped to the current episode), so its state must exist first.
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

  // ── Workflow instance (strip + node card + advance gate) ───────────────
  const { workflow, reload: reloadWorkflow } = useProjectWorkflow(project.id, currentEpisodeId);
  // A node the sidebar / top-bar asked to focus on the Overview.
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  // The advance/back confirm gate — one instance, shared by the node card's
  // Complete/Back buttons and the top-bar stepper. Holds the server preview so
  // the dialog only ever renders what the same predicate ruled (#1400).
  const [advance, setAdvance] = useState<{
    direction: 'forward' | 'back';
    preview: AdvancePreview | null;
    confirming: boolean;
  } | null>(null);

  const requestAdvance = useCallback(
    (direction: 'forward' | 'back') => {
      setAdvance({ direction, preview: null, confirming: false });
      fetchAdvancePreview(project.id, direction, currentEpisodeId ?? undefined)
        .then((preview) =>
          setAdvance((cur) => (cur && cur.direction === direction ? { ...cur, preview } : cur)),
        )
        .catch((err) => {
          console.error('[ProjectWorkspace] advance preview failed:', err);
          setAdvance(null);
          addToast(t('common.error'), 'error');
        });
    },
    [project.id, currentEpisodeId, addToast, t],
  );

  const confirmAdvance = useCallback(() => {
    setAdvance((cur) => (cur ? { ...cur, confirming: true } : cur));
    const direction = advance?.direction ?? 'forward';
    executeAdvance(project.id, direction, currentEpisodeId ?? undefined)
      .then(() => {
        setAdvance(null);
        void reloadWorkflow();
      })
      .catch((err) => {
        console.error('[ProjectWorkspace] advance failed:', err);
        addToast(t('common.error'), 'error');
        setAdvance((cur) => (cur ? { ...cur, confirming: false } : cur));
      });
  }, [advance?.direction, project.id, currentEpisodeId, reloadWorkflow, addToast, t]);

  const handleJumpToNode = useCallback((nodeId: string) => {
    setActiveModule('overview');
    setFocusNodeId(nodeId);
  }, []);

  // Sidebar Stages block (M2 PR-F F2) — opens the dedicated Stage Board module
  // instead of scrolling to the node's Overview card (handleJumpToNode above).
  const handleOpenStage = useCallback((nodeId: string) => {
    setStageNodeId(nodeId);
    setActiveModule('stage');
  }, []);

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
  // centre-pane view the shell opens on. studioScenes/activeSceneId are still
  // lifted out of the embedded editor — not for a sidebar list (the editor owns
  // its own scene rail now), but so the workspace top-bar slate can read the
  // active scene number.
  const [resolvedScriptId, setResolvedScriptId] = useState<string | null>(null);
  const [studioView, setStudioView] = useState<RailView>('script');
  const [studioScenes, setStudioScenes] = useState<SceneLift[]>([]);
  const [studioActiveSceneId, setStudioActiveSceneId] = useState<string | null>(null);

  // Concurrent triggers for the SAME episode share one resolve/provision.
  // Without this, a double-fire — double-clicking a work view, or the
  // episode-change effect firing alongside an explicit open — runs the
  // check-then-create twice and provisions two scripts (prod #1432: two active
  // scripts ~1.3s apart, the first empty). Callers share ONE promise so at most
  // one createScriptProject is issued; the backend get-or-create covers the
  // cross-tab / lost-race case as defense in depth.
  const provisionInFlightRef = useRef<Map<string, Promise<string | null>>>(new Map());

  const resolveOrProvisionScript = useCallback(
    (episode: EpisodeProgress): Promise<string | null> => {
      const key = String(episode.episode_id);
      const inflight = provisionInFlightRef.current.get(key);
      if (inflight) return inflight;
      const run = (async (): Promise<string | null> => {
        const result = await fetchScriptProjects(project.id);
        const candidates = (result.data ?? []).filter(
          (s) => String(s.episode_id ?? '') === String(episode.episode_id),
        );
        if (candidates.length === 0) {
          // Pre-epic projects have episodes with no script (mig353 backfilled
          // Episode 1 but only attached scripts that already existed) — the
          // old silent fall-back to the Episodes pane read as "点了没反应"
          // (prod feedback 2026-07-11). Atomic create+bind: the backend
          // returns the episode's existing active script if one already
          // exists, so a lost race still can't duplicate.
          const created = await createScriptProject({
            project_id: project.id,
            name: episode.title,
            episode_id: episode.episode_id,
          });
          return created.id;
        }
        candidates.sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        );
        return candidates[0].id;
      })();
      provisionInFlightRef.current.set(key, run);
      // Housekeeping branch: clear the in-flight entry once settled. The
      // `.catch` swallows rejection on THIS branch only — the real error still
      // propagates through the `run` promise returned to (and awaited by)
      // callers; without it a failed provision would surface as an unhandled
      // rejection.
      void run
        .finally(() => {
          // Clear only if a later distinct run hasn't already replaced this one.
          if (provisionInFlightRef.current.get(key) === run) {
            provisionInFlightRef.current.delete(key);
          }
        })
        .catch(() => {});
      return run;
    },
    [project.id],
  );

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
        const scriptId = await resolveOrProvisionScript(episode);
        if (!scriptId) {
          setActiveModule('episodes');
          return;
        }
        setResolvedScriptId(scriptId);
        setActiveModule('script');
      } catch (err) {
        console.error('[ProjectWorkspace] failed to resolve current episode script:', err);
        // Loud failure (was silent): the click otherwise appears to do nothing.
        addToast(t('common.error'), 'error');
        setActiveModule('episodes');
      }
    },
    [resolveOrProvisionScript, addToast, t],
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

  // Workflow-strip node click (B5 T-B5.3) — the strip is now the sole node
  // entry point (the sidebar's Stages list was removed in T-B5.6). Route by the
  // node's creative `surface` (nodeSurface.ts::resolveSurface): script /
  // storyboard open the current episode's studio on that view; renders opens the
  // Renders file filter; a deliverable-only node (surface === null) falls back
  // to its dedicated Stage Board.
  const handleSelectNode = useCallback(
    (node: ProjectStageNode) => {
      switch (resolveSurface(node)) {
        case 'script':
          handleOpenWorkView('script');
          break;
        case 'storyboard':
          handleOpenWorkView('storyboard');
          break;
        case 'renders':
          handleOpenRenders();
          break;
        default:
          handleOpenStage(node.id);
      }
    },
    [handleOpenWorkView, handleOpenRenders, handleOpenStage],
  );

  const studioMode = activeModule === 'script' && resolvedScriptId != null;

  // `?module=stage` without a `node` param has nothing to render (stageNodeId
  // fell back to null, see the useState initializer above) — the module
  // comment promised a fallback to Overview, but the render switch below only
  // ever matched a plain 'overview' module, leaving the content area blank.
  // Route THIS content render to Overview without touching `activeModule`
  // itself (URL/module state stays 'stage' so a later `onOpenStage` re-entry
  // still behaves as expected).
  const showOverview = activeModule === 'overview' || (activeModule === 'stage' && !stageNodeId);

  // ── Episode surface view set (B5 T-B5.4) ──────────────────────────────
  // The current workflow node's creative `surface` (nodeSurface.ts) drives a
  // top segmented control (EpisodeViewTabs). Deliverable-only nodes yield an
  // empty view set → no control (they still reach their Stage Board via the
  // node click). The panel is rendered on the workspace landing (Overview),
  // above the overview content, so it is strictly additive and easy to retune
  // once the design mock lands — see the render block + report for assumptions.
  const currentNode = useMemo(
    () => workflow?.nodes.find((n) => n.id === workflow.current_node_id) ?? null,
    [workflow],
  );
  const surfaceViews = useMemo(() => viewsForNode(currentNode), [currentNode]);
  const [episodeView, setEpisodeView] = useState<string | null>(null);
  // Keep the active view key valid for the current node's set: default to the
  // first view; reset when the set no longer contains the active key (e.g. the
  // writer advanced to a node with a different surface).
  useEffect(() => {
    if (surfaceViews.length === 0) {
      if (episodeView !== null) setEpisodeView(null);
      return;
    }
    if (!surfaceViews.some((v) => v.key === episodeView)) {
      setEpisodeView(surfaceViews[0].key);
    }
  }, [surfaceViews, episodeView]);
  const activeEpisodeView = episodeView ?? surfaceViews[0]?.key ?? null;
  const showSurfacePanel = showOverview && surfaceViews.length > 0;

  // Film slate read-out (studio only): 1-based episode + active-scene numbers.
  const epIdx = episodes.findIndex((e) => e.episode_id === currentEpisode?.episode_id);
  const epNumber = epIdx >= 0 ? epIdx + 1 : null;
  const sceneNumber =
    (studioActiveSceneId
      ? studioScenes.findIndex((s) => s.id === studioActiveSceneId) + 1
      : 0) || null;

  return (
    <div data-testid="project-workspace" className="flex flex-col h-full min-h-0">
      {/* Full-width project bar on top, spanning over the sidebar. */}
      <WorkspaceTopBar
        projectName={project.name}
        onBack={onBack}
        canWrite={canWrite}
        slate={activeModule === 'script' && epNumber ? { ep: epNumber, scene: sceneNumber } : null}
        workflow={workflow}
        onRequestAdvance={requestAdvance}
        onJumpToNode={handleJumpToNode}
        projectId={project.id}
        autopilotEnabled={project.autopilot_enabled ?? true}
        onAutopilotChange={(enabled) => onProjectUpdated?.({ ...project, autopilot_enabled: enabled })}
      />
      <div className="flex-1 min-h-0 flex overflow-hidden">
      <WorkspaceSidebar
        activeModule={activeModule}
        onModuleChange={handleModuleChange}
        episodes={episodes}
        currentEpisode={currentEpisode}
        onEpisodeChange={handleEpisodeChange}
        activeWorkView={activeModule === 'script' ? studioView : null}
        onOpenWorkView={handleOpenWorkView}
        onOpenRenders={handleOpenRenders}
      />
      <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
        <Suspense
          fallback={
            <div className="flex-1 grid place-items-center text-ink-500">
              <Loading center />
            </div>
          }
        >
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
            />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-6 pb-8">
            {showSurfacePanel && activeEpisodeView && (
              // B5 T-B5.4: surface view set for the current node. Rendered as a
              // strip above the Overview content — additive, not a replacement.
              // Content wiring is intentionally shallow (see per-view notes):
              // canvas reuses the real WorkspaceCanvas inline; editor-backed
              // views (script/beats/storyboard) and renders route to their
              // existing surface via an entry button; shotlist is a disabled
              // placeholder (the table does not exist yet).
              <div data-testid="episode-surface-panel" className="pt-4 pb-2">
                <EpisodeViewTabs
                  views={surfaceViews}
                  active={activeEpisodeView}
                  onChange={setEpisodeView}
                />
                <div className="mt-4">
                  {activeEpisodeView === 'canvas' ? (
                    // Reuse the SAME canvas module component (not a fork); its
                    // sidebar module registration is untouched.
                    <div className="min-h-[24rem]">
                      <WorkspaceCanvas projectId={project.id} teamId={teamId} />
                    </div>
                  ) : activeEpisodeView === 'shotlist' ? (
                    <div
                      data-testid="episode-view-shotlist"
                      className="rounded-lg border border-dashed border-line bg-island-2/40 px-6 py-10 text-center"
                    >
                      <p className="text-sm font-medium text-content-2">
                        {t('projects.episodeSurface.shotlistComingSoon')}
                      </p>
                      <p className="mt-1 text-xs text-content-3">
                        {t('projects.episodeSurface.shotlistHint')}
                      </p>
                    </div>
                  ) : (
                    // Editor-backed / renders views: entry button into the
                    // existing surface. Deep inline embedding of the studio
                    // editor / storyboard is deferred (needs the editor's
                    // lifted scenes + scriptId) — see report.
                    <div
                      data-testid="episode-view-entry"
                      className="rounded-lg border border-line bg-island-2/40 px-6 py-10 text-center"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          if (activeEpisodeView === 'renders') handleOpenRenders();
                          else if (activeEpisodeView === 'storyboard')
                            handleOpenWorkView('storyboard');
                          else if (activeEpisodeView === 'beats') handleOpenWorkView('beats');
                          else handleOpenWorkView('script');
                        }}
                        className="rounded-md bg-[var(--accent-soft)] px-4 py-2 text-sm font-medium text-[var(--accent-text)] hover:opacity-90"
                      >
                        {activeEpisodeView === 'renders'
                          ? t('projects.episodeSurface.openRenders')
                          : activeEpisodeView === 'storyboard'
                            ? t('projects.episodeSurface.openStoryboard')
                            : activeEpisodeView === 'beats'
                              ? t('projects.episodeSurface.openBeats')
                              : t('projects.episodeSurface.openScript')}
                      </button>
                      {activeEpisodeView !== 'renders' && (
                        <p className="mt-2 text-xs text-content-3">
                          {t('projects.episodeSurface.editorEntryHint')}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
            {showOverview && (
              <WorkspaceOverview
                project={project}
                episodes={episodes}
                currentEpisode={currentEpisode}
                episodeId={currentEpisodeId}
                epNumber={epNumber}
                onOpenScript={() => void openCurrentEpisodeScript()}
                workflow={workflow}
                canWrite={canWrite}
                onReloadWorkflow={() => void reloadWorkflow()}
                onRequestAdvance={requestAdvance}
                onOpenTodolist={() => setActiveModule('tasks')}
                onOpenStage={handleOpenStage}
                onSelectNode={handleSelectNode}
                focusNodeId={focusNodeId}
                onSelectEpisode={handleEpisodeChange}
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
            {(activeModule === 'characters' || activeModule === 'locations' || activeModule === 'props') && (
              <WorkspaceEntities kind={activeModule} projectId={project.id} />
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
            {activeModule === 'tasks' && (
              <WorkspaceTasks
                projectId={project.id}
                projectName={project.name}
                teamId={teamId}
                currentEpisodeId={currentEpisodeId}
              />
            )}
            {activeModule === 'stage' && stageNodeId && (
              <WorkspaceStageBoard
                projectId={project.id}
                projectName={project.name}
                nodeId={stageNodeId}
                episodeId={currentEpisodeId}
                workflow={workflow}
                canWrite={canWrite}
                onRequestAdvance={requestAdvance}
                onOpenTodolist={() => setActiveModule('tasks')}
              />
            )}
          </div>
        )}
        </Suspense>
      </div>
      </div>

      {advance && (
        <AdvanceConfirmDialog
          preview={advance.preview}
          direction={advance.direction}
          confirming={advance.confirming}
          onConfirm={confirmAdvance}
          onClose={() => setAdvance(null)}
        />
      )}
    </div>
  );
}

export default ProjectWorkspace;
