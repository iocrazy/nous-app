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

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
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
import { hasShotFocusListener, requestShotFocus } from '../agentActivity/shotFocusBus';
// Kept eager: ProjectsListView statically imports it too, so it lives in the
// ProjectsPage chunk regardless — a dynamic import here buys nothing and just
// trips Rollup's "dynamically + statically imported" warning.
import { ProjectSettingsPanel } from '../ProjectSettingsPanel';
import type { RailView } from '../../editor/components/RailModules';
import type { SceneDoc } from '../../editor/types';
import { WorkspaceSidebar, type WorkView } from './WorkspaceSidebar';
import { resolveSurface } from './nodeSurface';
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
const EpisodeStoryboardPage = lazy(() =>
  import('./EpisodeStoryboardPage').then((m) => ({ default: m.EpisodeStoryboardPage })),
);

/** Minimal scene shape lifted from the embedded editor for the SCENES sidebar. */
interface SceneLift {
  id: string;
  heading_int_ext: string | null;
  location_text: string | null;
}

/**
 * URL 统一寻址 (IA redesign Task 1, spec `2026-08-10-workspace-ia-redesign`):
 * reads the `ep/node/view/scene/shot` param family as plain strings (never
 * `Number()` — Snowflake BIGINT exceeds JS's safe integer range). Missing or
 * empty params come back `null`, never `''`/`NaN`. Exported so Task 2
 * (storyboard `view/scene/shot`) and Task 4 (accordion `node`) consume the
 * same contract instead of each re-deriving it from `URLSearchParams`.
 */
export function readWorkspaceParams(sp: URLSearchParams) {
  const get = (k: string) => {
    const v = sp.get(k);
    return v && v.length > 0 ? v : null;
  };
  return { ep: get('ep'), node: get('node'), view: get('view'), scene: get('scene'), shot: get('shot') };
}

// Shot-focus request timing (Task 3 修复轮2, 2026-08-10 用户拍板): `shotFocusBus`'s
// ONLY subscriber is EditorShell (editor/components/EditorShell.tsx ~L534,
// `useEffect(() => onShotFocus(...), [selectRailView])`), which registers on
// mount. Getting from "shot card clicked" to "EditorShell mounted and
// subscribed" crosses TWO unbounded async hops — resolving/provisioning the
// episode's script (network) and loading EditorShell's own lazy chunk
// (`lazy(() => import('../../editor/components/EditorShell'))` above) — so a
// single fixed delay (the 300ms this codebase used for the OLD page-local
// canvas jump, when the target was already mounted) can't be trusted here.
// `hasShotFocusListener()` (already exported by the bus for exactly this
// "is anyone listening" question) turns the guess into a check: try once
// after a generous first delay, and if nobody's listening yet, retry once
// more after a longer one before giving up silently — matching the bus's own
// fire-and-forget philosophy ("a chip that does nothing beats yanking the
// writer somewhere unexpected", per shotFocusBus.ts's file doc comment) over
// an unbounded poll loop.
const SHOT_FOCUS_FIRST_DELAY_MS = 400;
const SHOT_FOCUS_RETRY_DELAY_MS = 900;

function requestShotFocusWhenEditorReady(shotId: string): void {
  window.setTimeout(() => {
    if (hasShotFocusListener()) {
      requestShotFocus(shotId);
      return;
    }
    window.setTimeout(() => {
      if (hasShotFocusListener()) requestShotFocus(shotId);
    }, SHOT_FOCUS_RETRY_DELAY_MS);
  }, SHOT_FOCUS_FIRST_DELAY_MS);
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
      'locations', 'props', 'files', 'trash', 'settings', 'stage', 'storyboard',
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
        if (activeModule === 'stage') {
          if (stageNodeId) next.set('node', stageNodeId);
          else next.delete('node');
        }
        // Non-stage modules leave an existing `node` param untouched — it's
        // the accordion's selected node (Task 4, IA redesign), which this
        // effect doesn't own and must not clobber on every module switch.
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
  // Overview accordion (IA redesign Task 4): whether the writer collapsed the
  // open row WITHOUT switching episodes (EpisodeSummaryRow's chevron click on
  // an already-open row). Deliberately NOT part of the URL — Task 1's `ep=`
  // stays the "which episode" truth; this is purely the accordion's own
  // open/closed UI state, reset back to expanded on every real episode switch
  // (see `handleEpisodeChange` below) so collapsing one episode's row never
  // silently hides the NEXT one you switch to.
  const [overviewCollapsed, setOverviewCollapsed] = useState(false);

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
        // URL `ep` is the first source of truth (Task 1, IA redesign): a
        // deep link (e.g. shared/back-navigated) should win over whatever
        // was last selected on this browser. Falls through the same
        // validation as the stored value — an invalid/stale `ep` degrades to
        // localStorage, then the first episode, exactly like before.
        const fromUrl = readWorkspaceParams(searchParams).ep;
        const valid =
          fromUrl && rows.some((r) => r.episode_id === fromUrl)
            ? fromUrl
            : stored && rows.some((r) => r.episode_id === stored)
              ? stored
              : fallback;
        setCurrentEpisodeId(valid);
      })
      .catch((err) => console.error('[ProjectWorkspace] failed to load episodes progress:', err));
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  const currentEpisode = episodes.find((e) => e.episode_id === currentEpisodeId) ?? null;

  // ── Workflow instance (strip + node card + advance gate) ───────────────
  // `loading` (Task 4 修复轮1): threaded to WorkspaceOverview so it can gate
  // the accordion's strip/card-slot render — see that prop's doc comment for
  // why `workflow` alone isn't a safe signal of "this is the expanded
  // episode's data" during an episode-switch fetch.
  const { workflow, loading: workflowLoading, reload: reloadWorkflow } = useProjectWorkflow(
    project.id,
    currentEpisodeId,
  );
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
      // Gate on currentEpisodeId (B6 PR-2 task 10): fetchAdvancePreview now
      // REQUIRES episode_id (server 422s otherwise). In practice the advance
      // affordances only render once `workflow` is loaded, which itself
      // requires a resolved episodeId (see useProjectWorkflow) — this is a
      // defensive no-op for the brief window before that resolves.
      if (!currentEpisodeId) return;
      setAdvance({ direction, preview: null, confirming: false });
      fetchAdvancePreview(project.id, direction, currentEpisodeId)
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
    // Same episodeId gate as requestAdvance above — executeAdvance also
    // requires episode_id.
    if (!currentEpisodeId) return;
    setAdvance((cur) => (cur ? { ...cur, confirming: true } : cur));
    const direction = advance?.direction ?? 'forward';
    executeAdvance(project.id, direction, currentEpisodeId)
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

  // Overview accordion node selection (IA redesign Task 4) — writes URL
  // `node=` only; it does NOT itself navigate/route anywhere. Task 5's
  // `EpisodeNodeCard` (consumed via `WorkspaceOverview`'s `renderNodeCard`
  // slot) owns what "a node is selected" actually does.
  const handleSelectNodeId = useCallback(
    (nodeId: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('node', nodeId);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Top-bar node stepper (H3) — jump back to Overview with that node selected
  // (URL `node=`, same as clicking it in the accordion strip) instead of the
  // old free-standing `focusNodeId`/scroll-into-view mechanism, which had no
  // surviving consumer once the accordion replaced the always-mounted
  // project-wide WorkflowSection strip (Task 4). `overviewCollapsed` reset
  // ensures a jump while the accordion is collapsed actually shows the row.
  const handleJumpToNode = useCallback(
    (nodeId: string) => {
      setActiveModule('overview');
      setOverviewCollapsed(false);
      handleSelectNodeId(nodeId);
    },
    [handleSelectNodeId],
  );

  // Sidebar Stages block (M2 PR-F F2) — opens the dedicated Stage Board module
  // instead of scrolling to the node's Overview card (handleJumpToNode above).
  const handleOpenStage = useCallback((nodeId: string) => {
    setStageNodeId(nodeId);
    setActiveModule('stage');
  }, []);

  const handleEpisodeChange = useCallback(
    (episodeId: string) => {
      setCurrentEpisodeId(episodeId);
      // A real episode switch always re-expands the accordion (Task 4) — see
      // `overviewCollapsed`'s doc comment: collapsing one episode's row must
      // never carry over and silently hide the row you just switched to.
      setOverviewCollapsed(false);
      // localStorage stays as the cross-session/no-URL-yet default; the URL
      // `ep` (Task 1) is now the first source of truth on load, so keep both
      // in sync on every explicit switch.
      try {
        localStorage.setItem(episodeStorageKey(project.id), episodeId);
      } catch (err) {
        console.error('[ProjectWorkspace] failed to persist episode selection:', err);
      }
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('ep', episodeId);
          // Task 2 review carry-over: `view`/`scene`/`shot` are scoped to
          // WHICHEVER episode was current when they were set (e.g. a
          // `?shot=` deep-link into that episode's canvas) — an episode
          // switch must drop them in this SAME setSearchParams call, or a
          // shot/scene id from the episode just left behind would survive
          // into the newly-selected one's storyboard page.
          next.delete('view');
          next.delete('scene');
          next.delete('shot');
          return next;
        },
        { replace: true },
      );
    },
    [project.id, setSearchParams],
  );

  // Overview accordion row toggle (IA redesign Task 4, ambiguity #3): a
  // non-null id opens/switches to that episode's row by routing through
  // `handleEpisodeChange` (writes URL `ep=` AND resets the collapsed flag);
  // `null` means "collapse the currently-open row" — the writer clicked the
  // chevron on an already-open row, which must NOT change which episode is
  // current (URL `ep=` stays exactly as-is).
  const handleExpandEpisode = useCallback(
    (episodeId: string | null) => {
      if (episodeId != null) {
        handleEpisodeChange(episodeId);
      } else {
        setOverviewCollapsed(true);
      }
    },
    [handleEpisodeChange],
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
  // Scene-card deep link (Task 8): set alongside studioView by
  // handleOpenWorkView when a scene card's Open passes a sceneId, cleared on
  // the bare-storyboard early return so a stale target doesn't leak into a
  // later plain script/beats open.
  const [studioFocusSceneId, setStudioFocusSceneId] = useState<string | null>(null);
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

  // Read-only half of the resolve chain: looks for the episode's most
  // recently updated script WITHOUT creating one. Shared by
  // `resolveOrProvisionScript` below (which falls through to create) and the
  // storyboard surface panel's probe effect (which must NOT create — see the
  // Task 3 review fix: a passive Overview landing on a storyboard-surface
  // node must never silently provision an empty script just because the
  // panel rendered).
  const findExistingScript = useCallback(
    async (episode: EpisodeProgress): Promise<string | null> => {
      const result = await fetchScriptProjects(project.id);
      const candidates = (result.data ?? []).filter(
        (s) => String(s.episode_id ?? '') === String(episode.episode_id),
      );
      if (candidates.length === 0) return null;
      candidates.sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      return candidates[0].id;
    },
    [project.id],
  );

  const resolveOrProvisionScript = useCallback(
    (episode: EpisodeProgress): Promise<string | null> => {
      const key = String(episode.episode_id);
      const inflight = provisionInFlightRef.current.get(key);
      if (inflight) return inflight;
      const run = (async (): Promise<string | null> => {
        const existing = await findExistingScript(episode);
        if (existing) return existing;
        // Pre-epic projects have episodes with no script (mig353 backfilled
        // Episode 1 but only attached scripts that already existed) — the
        // old silent fall-back to the Episodes pane read as "点了没反应"
        // (prod feedback 2026-07-11). Atomic create+bind: the backend
        // returns the episode's existing active script if one already
        // exists, so a lost race still can't duplicate. Only reached from an
        // explicit user action (Script/Beats work views, the scene card's
        // Open deep-link, or the storyboard panel's "Start Storyboard" CTA)
        // — never from a passive render, see findExistingScript above.
        const created = await createScriptProject({
          project_id: project.id,
          name: episode.title,
          episode_id: episode.episode_id,
        });
        return created.id;
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
    [project.id, findExistingScript],
  );

  // Resolve a given episode's most recently updated script and mount
  // EditorShell inline (no route jump); no script → provision an empty one the
  // same way project-create does, then mount it. `railView` presets the work
  // view (and the sidebar highlight). ───
  // `focusSceneId` (Task 8 fix round 1): this is the ONLY call site that flips
  // activeModule to 'script' (the studioMode condition EditorShell mounts on),
  // so it's the single choke point for studioFocusSceneId too — every caller
  // must declare its scene-focus intent explicitly, default null CLEARS it.
  // Without this, a scene-card deep link's target survived in bare React state
  // past the EditorShell unmount (leaving 'script' drops the mount but not the
  // state) and got silently re-consumed by the next, unrelated remount —
  // "Continue Writing" or an episode row's Open button (neither of which ever
  // meant to focus a scene) — scrolling to a scene the writer never asked for
  // this time.
  const openEpisodeScript = useCallback(
    async (
      episode: EpisodeProgress | null,
      railView: RailView = 'script',
      focusSceneId: string | null = null,
    ) => {
      if (!episode) {
        setActiveModule('episodes');
        return;
      }
      setStudioView(railView);
      setStudioFocusSceneId(focusSceneId);
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

  // Storyboard is now the episode node's PRIMARY face (三视图主工作面, 2026-08-09
  // 拍板) AND its own standalone module (IA redesign Task 2): a bare open
  // ('storyboard', no sceneId) — from the sidebar's 分镜 row, a workflow-strip
  // node (handleSelectNode below), or anywhere else that used to jump
  // straight into the embedded editor — now routes to the dedicated
  // EpisodeStoryboardPage module (EpisodeSceneBoard IS the view, not an entry
  // button into one). Only a scene card's "Open" deep-link (opts.sceneId set)
  // still wants the real editor — studioFocusSceneId carries the target down
  // to EditorShell's initialFocusSceneId, which scrolls the matching
  // storyboard column / script scene into view once it has rendered (Task 8).
  const handleOpenWorkView = useCallback(
    (view: WorkView, opts?: { sceneId?: string }) => {
      if (view === 'storyboard' && !opts?.sceneId) {
        setStudioFocusSceneId(null); // clear any stale target from a prior deep link
        setActiveModule('storyboard');
        return;
      }
      setStudioView(view);
      void openEpisodeScript(
        currentEpisode,
        view,
        view === 'storyboard' && opts?.sceneId ? opts.sceneId : null,
      );
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
  // entry point (the sidebar's Stages list was removed in T-B5.6). Only the
  // CURRENT node routes by creative `surface` (nodeSurface.ts::resolveSurface):
  // script / storyboard open the current episode's studio on that view; renders
  // opens the Renders file filter. Every other node — including a
  // deliverable-only one (surface === null) — falls back to its own dedicated
  // Stage Board (Task 7, see guard below: a non-current node's click must not
  // flash open the CURRENT episode's surface panel with unrelated content).
  //
  // ⚠️ Task 4 (IA redesign accordion rewrite) unwired this from
  // `WorkspaceOverview` — its new `onSelectNode` prop only writes URL
  // `node=` (see `handleSelectNodeId` above), it doesn't take a full
  // `ProjectStageNode` to route by surface. This function (and
  // `handleOpenStage` / the `resolveSurface` import it uses) is INTENTIONALLY
  // KEPT, currently uncalled: Task 5's `EpisodeNodeCard` (the `renderNodeCard`
  // slot) is expected to reuse this exact routing logic for its own actions
  // (e.g. an "Open" affordance on the node card). DO NOT DELETE as dead code
  // without checking Task 5's plan first.
  const handleSelectNode = useCallback(
    (node: ProjectStageNode) => {
      // 拍板（undo 立项附带, Task 7 小尾巴 A, 2026-08-09）：非当前节点点击不再闪当前集的
      // 作业面——surface 路由只对 current 节点成立，其余一律看节点自己的 Stage Board。
      if (node.id !== workflow?.current_node_id) {
        handleOpenStage(node.id);
        return;
      }
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
    [handleOpenWorkView, handleOpenRenders, handleOpenStage, workflow?.current_node_id],
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

  // EpisodeStoryboardPage (IA redesign Task 2) owns the `?view=` param on its
  // own — it holds the active-view state internally and calls back here only
  // to persist a writer-driven tab switch to the URL (deep-linkable, survives
  // back/forward). `replace: true` mirrors the module-sync effect above: a
  // tab switch isn't a new history entry.
  //
  // Review fix round 1 (sticky `?shot=`): `shot` is a ONE-SHOT deep-link
  // trigger, now consumed by THIS file's own URL-shot effect below (moved out
  // of EpisodeStoryboardPage in 修复轮2 — see that effect's comment) — every
  // view change, whether the writer clicking a tab or that effect's own
  // consumption, clears it here. Without this, `shot` lingered in the URL
  // after the writer manually switched tabs; a later remount (refresh, or
  // navigating out and back into the Storyboard module) re-read the stale
  // `shot` and re-fired the editor deep-link, silently overriding whatever
  // the writer was doing.
  const handleStoryboardViewChange = useCallback(
    (view: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('view', view);
          next.delete('shot');
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Shot deep-link into the editor (Task 3 修复轮2, 2026-08-10 用户拍板): the
  // ONLY two entry points — a scene board's shot-card click (via
  // EpisodeStoryboardPage's `onOpenShotInEditor` prop, sceneId always known —
  // the shot's own column has it) and the URL `?shot=` one-shot trigger below
  // (sceneId unknown, `null` — an agent-panel/share link only carries the
  // shot id) — both funnel through here, so there is exactly ONE behavior to
  // reason about. Routes through `openEpisodeScript` directly (NOT
  // `handleOpenWorkView('storyboard', {sceneId})`; that helper's own
  // `!opts?.sceneId` guard would redirect a null-sceneId call back to the
  // standalone Storyboard MODULE page instead of the embedded editor — see
  // its comment above). `openEpisodeScript` already handles `sceneId=null`
  // gracefully (just skips the scroll-to-scene half), so the editor's
  // storyboard rail opens on the current episode's script either way.
  const handleOpenShotInEditor = useCallback(
    (shotId: string, sceneId: string | null) => {
      void openEpisodeScript(currentEpisode, 'storyboard', sceneId);
      requestShotFocusWhenEditorReady(shotId);
    },
    [openEpisodeScript, currentEpisode],
  );

  // URL `?shot=` one-shot deep-link (kept as an entry point per 修复轮2's
  // 拍板: an agent panel or a shared link may carry `shot=<id>` into the
  // Storyboard module page without a `scene`). Fires once `currentEpisode`
  // has resolved (so `openEpisodeScript` above has a real episode to work
  // with, not a premature `null` that would bounce to the Episodes module),
  // then immediately clears `shot` from the URL in the SAME effect — true
  // one-shot, mirroring the `shot`-clearing contract `handleStoryboardViewChange`
  // already enforces on a manual tab switch. Not gated on `activeModule` —
  // the deep-link's destination is the EDITOR, a different module entirely,
  // so it's meant to fire regardless of which module the URL happened to
  // land on first.
  useEffect(() => {
    const shotId = readWorkspaceParams(searchParams).shot;
    if (!shotId || !currentEpisode) return;
    handleOpenShotInEditor(shotId, null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('shot');
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately narrow: re-checks whenever `searchParams` changes (for a fresh `shot` value) or `currentEpisode` first resolves; `handleOpenShotInEditor`'s identity riding on `currentEpisode` would otherwise re-fire this on every episodes refetch even with no `shot` in the URL, but the `!shotId` guard above already makes that a no-op
  }, [searchParams, currentEpisode, setSearchParams]);

  // Film slate read-out (studio only): 1-based episode + active-scene numbers.
  const epIdx = episodes.findIndex((e) => e.episode_id === currentEpisode?.episode_id);
  const epNumber = epIdx >= 0 ? epIdx + 1 : null;
  const sceneNumber =
    (studioActiveSceneId
      ? studioScenes.findIndex((s) => s.id === studioActiveSceneId) + 1
      : 0) || null;

  // Overview accordion addressing (IA redesign Task 4, ambiguity #3): URL
  // `ep=` is the first source of truth (falls back to `currentEpisodeId`
  // exactly like the episode-resolution effect above), UNLESS the writer
  // explicitly collapsed the open row — that's local UI state, not a URL
  // concern, so it wins over both. `node=` has no such override; Task 5's
  // node card decides what an absent selection defaults to.
  const expandedEpisodeId = overviewCollapsed
    ? null
    : (readWorkspaceParams(searchParams).ep ?? currentEpisodeId);
  const selectedNodeId = readWorkspaceParams(searchParams).node;

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
              initialFocusSceneId={studioFocusSceneId ?? undefined}
              embedded
              onScenesChange={handleScenesChange}
              onActiveSceneChange={handleActiveSceneChange}
            />
          </div>
        ) : activeModule === 'storyboard' ? (
          // Storyboard's standalone module (IA redesign Task 2) — replaces
          // the old Overview "surface panel". Full-bleed like the EditorShell
          // branch above: the page owns its own header + scroll region, so
          // no px-6 pb-8 wrapper (that would double the padding/scrollbar).
          <EpisodeStoryboardPage
            projectId={project.id}
            teamId={teamId ?? ''}
            episode={currentEpisode}
            initialView={readWorkspaceParams(searchParams).view}
            onViewChange={handleStoryboardViewChange}
            findExistingScript={findExistingScript}
            provisionScript={resolveOrProvisionScript}
            onOpenScene={(sceneId) => handleOpenWorkView('storyboard', { sceneId })}
            onOpenShotInEditor={handleOpenShotInEditor}
          />
        ) : (
          <div className="flex-1 overflow-y-auto px-6 pb-8">
            {showOverview && (
              <WorkspaceOverview
                project={project}
                episodes={episodes}
                workflow={workflow}
                workflowLoading={workflowLoading}
                expandedEpisodeId={expandedEpisodeId}
                selectedNodeId={selectedNodeId}
                onExpandEpisode={handleExpandEpisode}
                onSelectNode={handleSelectNodeId}
                // Task 5 fills this in with the real EpisodeNodeCard; Task 4
                // only wires the accordion + strip + slot plumbing.
                renderNodeCard={() => null}
                canWrite={canWrite}
                onReloadWorkflow={() => void reloadWorkflow()}
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
