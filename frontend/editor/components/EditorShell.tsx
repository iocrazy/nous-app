/**
 * EditorShell — the R2-A Final three-zone shell for the v2 script editor
 * (spec v3 §3.1; visual baseline ui-r2a-final.html).
 *
 * Layout: a left navigation rail (episode label + scene list), a centre paper
 * column (indigo-island chrome, cream/deep-ink sheet with feTurbulence grain),
 * and a right "Writing" panel. Both side zones collapse to a narrow strip that
 * KEEPS its landmark and an expand button — navigation never disappears (island
 * rule). The theme is a set of CSS variables scoped to the shell root via
 * `data-theme`; nothing here touches global document styles.
 *
 * Scope for Task 4: real scene fetch + the chrome. The live scene editor
 * (SceneRail / SceneBlock / Hollywood layout / Statistics) lands in Tasks 5-6;
 * here the rail and paper column render a minimal-but-real view of the loaded
 * scenes so the shell is exercised end to end.
 */
import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from 'react';
import { useTranslation } from 'react-i18next';
import {
  applyOps,
  convertToScenes,
  createScene,
  deleteScene,
  listEpisodes,
  listScenes,
  moveScene,
  newElementId,
  type Episode,
  type ScriptCommit,
} from '../sceneService';
import { fetchScriptProject } from '../../services/scriptService';
import { planInsertScene } from '../insertScenePlan';
import { fetchProjectEntities } from '../../services/projectsService';
import { extractLibEntitiesFromScript } from '../../services/libEntitiesService';
import { useToast } from '../../components/Toast';
import { mergeProjectMentionCandidates } from '../mentionCandidates';
import type { CursorState, ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';
import { useEditorState, type EditorFormat, type EditorMode } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { persistFormat, readStoredFormat } from '../formatStorage';
import { persistPagination, readStoredPagination, type PaginationMode } from '../paginationStorage';
import { persistZoom, readStoredZoom, DEFAULT_ZOOM } from '../zoomStorage';
import { computePageLayout, type MeasuredRow, type RowKind } from '../paginate';
import { PageSeam } from './PageSeam';
import { persistRailView, readStoredRailView } from '../railViewStorage';
import { useConvertPoll } from '../useConvertPoll';
import {
  WINDOW_THRESHOLD,
  computeSceneWindow,
  estimateSceneHeight,
  type SceneWindow,
} from '../windowing';
import { EDITOR_SHELL_STYLES } from './editorShellStyles';
import { sceneBlockBases } from './blockNumbering';
import {
  SceneBlock,
  type TypeCommand,
  type SceneSyncStatus,
  type SceneReorderApi,
} from './SceneBlock';
import { SceneToc } from './SceneToc';
import { RailModules, type RailView } from './RailModules';
import { BeatsView } from '../beats/BeatsView';
import {
  persistBeatsSubview,
  readStoredBeatsSubview,
  type BeatsSubview,
} from '../beats/beatsViewStorage';
import { NodesView } from '../nodes/NodesView';
import { StoryboardView } from '../storyboard/StoryboardView';
import { VersionDiff } from '../versions/VersionDiff';
import { OutlineView } from './OutlineView';
import { EpisodePanel } from './EpisodePanel';
import { RailEntities } from './RailEntities';
import { deriveRailCharacters, deriveRailLocations } from '../railDerive';
import { ElementToolbar } from './ElementToolbar';
import { WritingPanel, deriveStatistics, CAST_COLORS } from './WritingPanel';
import { SaveIndicator, aggregateSaveState } from './SaveIndicator';
import { ConflictBar } from './ConflictBar';
import { ColdStart } from './EmptyStates';
import { ImportScriptModal } from './ImportScriptModal';
import { useScriptPresence } from '../collab/useScriptPresence';
import { useScriptOpsRealtime } from '../collab/useScriptOpsRealtime';
import { PresenceAvatars } from '../collab/PresenceAvatars';
import { ScenePresenceContext, type ScenePresenceMap } from '../collab/scenePresenceContext';
import type { RemoteOpRow } from '../useSceneSync';
import { reportError } from '../../services/errorReporter';
import { SelectionAiChatButton } from '../selection/SelectionAiChatButton';
import { Loading } from '../../components/common/Loading';
import { onShotFocus } from '../../components/agentActivity/shotFocusBus';

type LoadState = 'loading' | 'ready' | 'error';

// Realtime collaboration is flag-dark: off → the presence hook receives null and
// nothing subscribes (zero mount / zero channel), matching the P5 constraint.
const COLLAB_ENABLED = import.meta.env.VITE_FEATURE_COLLAB === 'true';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

export function EditorShell({
  scriptId,
  currentUserId = null,
  currentUserName,
  projectId: workspaceProjectId,
  initialRailView,
  embedded = false,
  onScenesChange,
  onActiveSceneChange,
  selectSceneRef,
}: {
  scriptId: string;
  /** Local user identity for collaboration presence (supplied by the route). */
  currentUserId?: string | null;
  currentUserName?: string;
  /**
   * Owning project id, known up front by callers that already have it (the
   * workspace shell's current project / the standalone route's `:projectId`
   * param) — PR-11, G13. Aliased on destructure to `workspaceProjectId`
   * because the component already has an internal `projectId` state (below)
   * resolved asynchronously from `fetchScriptProject` for the Episode panel
   * / import modal; this prop exists purely so the @-mention project-entity
   * fetch doesn't have to wait on that resolution. Optional + best-effort:
   * omitted or failing leaves @-mention candidates at script-only (Phase 1
   * behavior), never blocking or erroring the editor.
   */
  projectId?: string;
  /**
   * Preset which centre-pane view (RailModules) this mount opens on — e.g.
   * the workspace's "Storyboard" sidebar child opens straight onto the
   * storyboard view instead of the script sheet (PR-11). Re-applied whenever
   * the prop changes (so re-clicking a different sidebar child while the
   * shell is already mounted for the same script still switches the view).
   * Omitted preserves the existing per-script localStorage preference.
   */
  initialRailView?: RailView;
  /**
   * Studio mode: the shell is mounted inside the project workspace (合一终稿,
   * 2026-07-11). The workspace tree is the single side navigation, so embedded
   * drops the editor's OWN left rail entirely — scene navigation is lifted up
   * to the workspace sidebar via the callbacks below. Standalone (fullscreen
   * route) keeps its full three-zone rail unchanged.
   */
  embedded?: boolean;
  /** Studio lift: report the scene list up to the workspace sidebar (embedded). */
  onScenesChange?: (scenes: SceneDoc[]) => void;
  /** Studio lift: report the currently highlighted scene up to the sidebar. */
  onActiveSceneChange?: (sceneId: string | null) => void;
  /** Studio lift: the workspace writes the internal scene-select fn here so a
   * sidebar scene click can scroll the matching block into view. */
  selectSceneRef?: MutableRefObject<((sceneId: string) => void) | null>;
}) {
  const { t } = useTranslation();
  // Restore the per-script layout engine synchronously so the first paint uses
  // it (no engine flash on remount).
  const initialFormat = useMemo(() => readStoredFormat(scriptId) ?? undefined, [scriptId]);
  // Pagination mode (laper's Paged vs Continuous), persisted per script like
  // the format engine. Paged draws dashed page rules + page numbers as an
  // overlay computed from measured block boxes (see the layout effect below).
  const [paginationMode, setPaginationMode] = useState<PaginationMode>(
    () => readStoredPagination(scriptId) ?? 'continuous',
  );
  const handlePaginationChange = useCallback(
    (mode: PaginationMode) => {
      setPaginationMode(mode);
      persistPagination(scriptId, mode);
    },
    [scriptId],
  );
  // Display zoom (laper-style), persisted per script. PURELY VISUAL: a CSS
  // `zoom` on the sheet reflows the paper (scroll size stays correct) while the
  // stored document, export/print semantics and pagination break points are
  // untouched — the seam measurement below divides its readings back to logical
  // (100%) coordinates, so breaks land on identical rows at every zoom level.
  const [sheetZoom, setSheetZoom] = useState<number>(
    () => readStoredZoom(scriptId) ?? DEFAULT_ZOOM,
  );
  const handleZoomChange = useCallback(
    (zoom: number) => {
      setSheetZoom(zoom);
      persistZoom(scriptId, zoom);
    },
    [scriptId],
  );
  const { state, setMode, setFormat, setTheme, setActiveScene, setCursor, setNextInsertType } =
    useEditorState({ initialFormat });
  const { addToast } = useToast();
  // Follow the app theme while mounted: ThemeContext stamps <html data-theme>,
  // so a MutationObserver keeps the shell in sync without context plumbing
  // (the shell renders in unit tests without a ThemeProvider). A later ☾/☼
  // click still overrides for the session; the next app-theme change re-syncs.
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, [setTheme]);
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [chapters, setChapters] = useState<ScriptChapter[]>([]);
  // Episode dimension (Task 5): the owning project, this script's episode, and
  // the project's episode list power the rail Ep selector + management panel.
  const [projectId, setProjectId] = useState<string | null>(null);
  const [currentEpisodeId, setCurrentEpisodeId] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [episodePanelOpen, setEpisodePanelOpen] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  // Live (optimistic) elements lifted from each SceneBlock so Statistics + rail
  // entities reflect in-flight edits, not just the last loaded snapshot (Task 6 ⑥).
  const [liveElements, setLiveElements] = useState<Record<string, ScriptElement[]>>({});
  const [converting, setConverting] = useState<Record<string, boolean>>({});
  const [loadState, setLoadState] = useState<LoadState>('loading');
  // Central-column view: the script sheet or the scene-node projection. The top
  // Script/Outline/Cover tabs are a separate axis and stay put (spec §3.1).
  // Restored per-script from localStorage so a reload/remount lands the writer
  // back on the view they left (F2); an unknown/invalid stored value → 'script'.
  const [railView, setRailView] = useState<RailView>(
    () => readStoredRailView(scriptId) ?? 'script',
  );
  // Version diff (Phase B P4): when set, the centre pane swaps to the diff view
  // (comparing this commit against the live 'current' state), taking precedence
  // over the rail views. A shell-level state — the simplest surface consistent
  // with how railView already gates the centre pane.
  const [diffCommit, setDiffCommit] = useState<ScriptCommit | null>(null);
  const [pendingOpenSceneId, setPendingOpenSceneId] = useState<string | null>(null);
  const [pendingFocusShotId, setPendingFocusShotId] = useState<string | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [typeCommand, setTypeCommand] = useState<TypeCommand | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, SceneSyncStatus>>({});
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [copilotSceneId, setCopilotSceneId] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ sceneId: string; edge: 'before' | 'after' } | null>(
    null,
  );
  const [sceneWindow, setSceneWindow] = useState<SceneWindow>({ start: 0, end: 10 });

  const shellRef = useRef<HTMLDivElement | null>(null);
  const sheetScrollRef = useRef<HTMLDivElement | null>(null);
  const cursorRef = useRef<CursorState | null>(null);
  const nonceRef = useRef(0);
  // Guards the toolbar Scene button against rapid re-clicks minting a blank
  // scene per click (see handleInsertScene).
  const insertingSceneRef = useRef(false);
  const scenesRef = useRef<SceneDoc[]>(scenes);
  cursorRef.current = state.cursor;
  scenesRef.current = scenes;

  const handleSyncStateChange = useCallback((sceneId: string, status: SceneSyncStatus) => {
    setSyncStates((prev) => ({ ...prev, [sceneId]: status }));
  }, []);

  // Keep the summoned copilot card to a single scene at a time (Task 11).
  const handleCopilotActivate = useCallback((sceneId: string | null) => {
    setCopilotSceneId(sceneId);
  }, []);

  const reload = useCallback(async () => {
    try {
      const rows = await listScenes(scriptId);
      setScenes([...rows].sort((a, b) => a.sort_order - b.sort_order));
      setLoadState('ready');
    } catch (err) {
      console.error('[EditorShell] failed to load scenes', err);
      setLoadState('error');
    }
  }, [scriptId]);

  // Chapters power the legacy prose fallback (Task 10); the same fetch carries
  // the owning project id + this script's episode (Task 5). Failure is
  // non-fatal: the scene editor still works, we just lose the Convert cards and
  // the episode selector falls back to its "Ep 1" label.
  const loadChapters = useCallback(async () => {
    try {
      const project = await fetchScriptProject(scriptId);
      setChapters(project.chapters ?? []);
      setProjectId(project.project_id ?? null);
      setCurrentEpisodeId(project.episode_id ?? null);
    } catch (err) {
      console.error('[EditorShell] failed to load chapters', err);
    }
  }, [scriptId]);

  // The project's episode list feeds the rail Ep selector + management panel.
  const loadEpisodes = useCallback(async (pid: string) => {
    try {
      setEpisodes(await listEpisodes(pid));
    } catch (err) {
      console.error('[EditorShell] failed to load episodes', err);
    }
  }, []);

  // After an episode mutation (rename / create / delete / reassign) re-fetch the
  // project (its episode_id may have changed) and the episode list.
  const reloadEpisodes = useCallback(async () => {
    const project = await fetchScriptProject(scriptId).catch((err) => {
      console.error('[EditorShell] failed to reload project', err);
      return null;
    });
    if (project) {
      setProjectId(project.project_id ?? null);
      setCurrentEpisodeId(project.episode_id ?? null);
      if (project.project_id) await loadEpisodes(project.project_id);
    }
  }, [scriptId, loadEpisodes]);

  const reloadAll = useCallback(async () => {
    await Promise.all([reload(), loadChapters()]);
  }, [reload, loadChapters]);

  // ── Version history (Phase B P4) ───────────────────────────────────────────
  // Compare opens the centre-pane diff for a commit; selecting any rail view or
  // doc tab closes it (the diff is a modal-ish takeover of the centre pane).
  const handleCompareCommit = useCallback((commit: ScriptCommit) => {
    setDiffCommit(commit);
  }, []);

  // Click-to-jump from the diff rail: scroll the live sheet to the changed
  // element (or its scene when the element is gone) and pulse a highlight so the
  // eye lands on it. Best-effort — a missing target (deleted since) is a no-op.
  const handleJumpToDiff = useCallback(
    (sceneId: string, elementId: string | null) => {
      const root = shellRef.current;
      if (!root) return;
      const block = root.querySelector<HTMLElement>(`[data-scene-id="${sceneId}"]`);
      if (!block) return;
      const target =
        (elementId &&
          block.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`)) ||
        block;
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('mh-diff-jump-flash');
      window.setTimeout(() => target.classList.remove('mh-diff-jump-flash'), 1400);
    },
    [],
  );

  // A rollback wrote new ops server-side — drop the stale optimistic overlay and
  // re-fetch the scenes so the sheet reflects the rolled-back content.
  // rollbackNonce keys the scene subtree: useSceneSync only reseeds when
  // scene.id CHANGES (its stale-scene guard), so a reload returning the SAME
  // scene ids with different elements would leave every editor showing the
  // pre-rollback text (seen live: toast fired, sheet unchanged). Bumping the
  // key remounts the blocks and they seed from the fresh fetch — rollback is
  // rare, so a full remount (caret reset) is the right trade.
  const [rollbackNonce, setRollbackNonce] = useState(0);
  const handleRolledBack = useCallback(() => {
    setLiveElements({});
    // Bump AFTER the re-fetch lands — remounting first would seed the fresh
    // blocks from the still-stale scenes prop and change nothing.
    void reload().finally(() => setRollbackNonce((n) => n + 1));
  }, [reload]);

  const selectRailView = useCallback((view: RailView) => {
    setDiffCommit(null);
    setRailView(view);
  }, []);

  // Beats sub-view (Arrangement / List) is lifted HERE so it renders in the very
  // same centre-topbar slot — and reuses the same `mh-doc-tabs` classes — as the
  // Script/Outline/Cover document tabs, instead of BeatsView drawing its own
  // segmented one row lower with its own CSS (user verdict 2026-07-20: same-class
  // blocks share one slot + one stylesheet). BeatsView is driven controlled via
  // the `subview`/`onSelectSubview` props below.
  const [beatsSubview, setBeatsSubview] = useState<BeatsSubview>(() =>
    readStoredBeatsSubview(scriptId),
  );
  const selectBeatsSubview = useCallback(
    (next: BeatsSubview) => {
      setBeatsSubview(next);
      persistBeatsSubview(scriptId, next);
    },
    [scriptId],
  );

  // Persist the rail view per script whenever it changes (F2). An effect covers
  // every path uniformly — the rail toggle AND the scene-node jump-back that
  // sets it to 'script' — so a reload always restores what the writer last saw.
  useEffect(() => {
    persistRailView(scriptId, railView);
  }, [scriptId, railView]);

  // PR-11: the caller's preset view (e.g. the workspace's Storyboard sidebar
  // child) wins over the stored preference. A one-way input — it only reacts
  // to the PROP changing, never to the writer's own RailModules clicks, so
  // switching views inside an already-open shell isn't fought back to the
  // preset on every render.
  useEffect(() => {
    if (initialRailView) setRailView(initialRailView);
  }, [initialRailView]);

  const selectMode = useCallback(
    (m: EditorMode) => {
      setDiffCommit(null);
      setMode(m);
    },
    [setMode],
  );

  // Shared "dispatch a slow chapter workflow, then poll for its output" driver —
  // used by the legacy Convert card here and by the node view's chapter actions.
  const { startPoll } = useConvertPoll(scriptId);

  useEffect(() => {
    let cancelled = false;
    setLoadState('loading');
    listScenes(scriptId)
      .then((rows) => {
        if (cancelled) return;
        setScenes([...rows].sort((a, b) => a.sort_order - b.sort_order));
        setLoadState('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[EditorShell] failed to load scenes', err);
        setLoadState('error');
      });
    loadChapters();
    return () => {
      cancelled = true;
    };
  }, [scriptId, loadChapters]);

  // Once the owning project is known, load its episode list for the selector.
  useEffect(() => {
    if (projectId) void loadEpisodes(projectId);
  }, [projectId, loadEpisodes]);

  // Viewport auto-highlight: mark the most-visible SceneBlock active. jsdom has
  // no IntersectionObserver, so feature-detect and no-op there.
  useEffect(() => {
    if (state.mode !== 'script' || typeof IntersectionObserver === 'undefined') return;
    const root = shellRef.current;
    if (!root) return;
    const blocks = root.querySelectorAll('[data-scene-id]');
    if (blocks.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        const id = top && (top.target as HTMLElement).dataset.sceneId;
        if (id) setActiveScene(id);
      },
      { threshold: [0.4] },
    );
    blocks.forEach((b) => io.observe(b));
    return () => io.disconnect();
  }, [state.mode, scenes, setActiveScene]);

  const handleSelectScene = useCallback(
    (sceneId: string) => {
      setActiveScene(sceneId);
      shellRef.current
        ?.querySelector<HTMLElement>(`[data-scene-id="${sceneId}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    [setActiveScene],
  );

  // ── Studio lift (embedded, 合一终稿 2026-07-11) ─────────────────────────────
  // Report the scene list + active scene up to the workspace sidebar, and
  // publish handleSelectScene so a sidebar scene click can reach back in. The
  // callbacks are undefined for the standalone route (no-op).
  useEffect(() => {
    onScenesChange?.(scenes);
  }, [scenes, onScenesChange]);
  useEffect(() => {
    onActiveSceneChange?.(state.activeSceneId);
  }, [state.activeSceneId, onActiveSceneChange]);
  useEffect(() => {
    if (!selectSceneRef) return;
    selectSceneRef.current = handleSelectScene;
    return () => {
      selectSceneRef.current = null;
    };
  }, [selectSceneRef, handleSelectScene]);

  // Jump from a scene node (double-click) or an outline row (click) back to the
  // script sheet, landing on that scene: switch BOTH axes to Script (the
  // railView node/script axis AND the top Script/Outline/Cover tab), mark it
  // active, and defer the scroll+focus until the sheet has re-rendered (the node
  // canvas / outline unmounts on the same tick).
  const handleOpenScene = useCallback(
    (sceneId: string) => {
      setRailView('script');
      setMode('script');
      setActiveScene(sceneId);
      setPendingOpenSceneId(sceneId);
    },
    [setActiveScene, setMode],
  );

  useEffect(() => {
    if (!pendingOpenSceneId || railView !== 'script') return;
    const block = shellRef.current?.querySelector<HTMLElement>(
      `[data-scene-id="${pendingOpenSceneId}"]`,
    );
    if (block) {
      block.scrollIntoView({ behavior: 'smooth', block: 'start' });
      block.querySelector<HTMLElement>('[data-el-id]')?.focus();
    }
    setPendingOpenSceneId(null);
  }, [pendingOpenSceneId, railView, scenes]);

  // A7: the chat panel's "the agent wrote these shot cards" summary lives in a
  // sibling component tree (FloatingChatWidget), so it reaches us through a
  // module-level bus rather than a prop. Same two-step shape as
  // handleOpenScene above — switch the rail first, scroll once the storyboard
  // has actually rendered.
  useEffect(
    () =>
      onShotFocus((shotId) => {
        selectRailView('storyboard');
        setPendingFocusShotId(shotId);
      }),
    [selectRailView],
  );

  useEffect(() => {
    if (!pendingFocusShotId || railView !== 'storyboard') return;
    // ShotCard already emits data-shot-id (editor/storyboard/ShotCard.tsx).
    const card = shellRef.current?.querySelector<HTMLElement>(
      `[data-shot-id="${pendingFocusShotId}"]`,
    );
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('mh-diff-jump-flash');
      window.setTimeout(() => card.classList.remove('mh-diff-jump-flash'), 1400);
    }
    setPendingFocusShotId(null);
  }, [pendingFocusShotId, railView, scenes]);

  // Focusing an element line raises the editing-state flag, which the shell
  // exposes as data-editing="true" — a pure CSS hook that lights up the element
  // toolbar (see editorShellStyles). It does NOT change focus or a11y behaviour.
  const handleFocusElement = useCallback(
    (cursor: CursorState) => {
      setCursor(cursor);
      setEditing(true);
    },
    [setCursor],
  );

  // Esc left the element line: clear the editing-state flag so data-editing
  // flips back to "false" and the toolbar emphasis relaxes. Native focus order
  // is unaffected — this is only a styling marker.
  const handleExitEditing = useCallback(() => setEditing(false), []);

  // ── Scene reorder (Task 10): moveScene({before|after}) then reload ─────────
  const moveSceneRelative = useCallback(
    async (sceneId: string, edge: 'before' | 'after', anchorSceneId: string) => {
      if (sceneId === anchorSceneId) return;
      try {
        await moveScene(
          sceneId,
          edge === 'before'
            ? { before_scene_id: anchorSceneId }
            : { after_scene_id: anchorSceneId },
        );
        await reload();
      } catch (err) {
        console.error('[EditorShell] moveScene failed', err);
      }
    },
    [reload],
  );

  const handleReorderDrop = useCallback(
    (targetSceneId: string, edge: 'before' | 'after') => {
      const draggedId = dragging;
      setDragging(null);
      setDropTarget(null);
      if (draggedId) void moveSceneRelative(draggedId, edge, targetSceneId);
    },
    [dragging, moveSceneRelative],
  );

  // Delete a whole scene (heading + all its elements) from the context menu.
  const handleDeleteScene = useCallback(
    async (sceneId: string) => {
      try {
        await deleteScene(sceneId);
        await reload();
      } catch (err) {
        console.error('[EditorShell] deleteScene failed', err);
      }
    },
    [reload],
  );

  // Keyboard reorder: Alt+Arrow moves the scene past its neighbour (spec §3.5).
  const handleKeyboardMove = useCallback(
    (sceneId: string, direction: 'up' | 'down') => {
      const ordered = scenesRef.current;
      const idx = ordered.findIndex((s) => s.id === sceneId);
      if (idx === -1) return;
      if (direction === 'up' && idx > 0) {
        void moveSceneRelative(sceneId, 'before', ordered[idx - 1].id);
      } else if (direction === 'down' && idx < ordered.length - 1) {
        void moveSceneRelative(sceneId, 'after', ordered[idx + 1].id);
      }
    },
    [moveSceneRelative],
  );

  const reorder: SceneReorderApi = useMemo(
    () => ({
      draggingId: dragging,
      dropTarget,
      onDragStart: (sceneId) => setDragging(sceneId),
      onDragEnd: () => {
        setDragging(null);
        setDropTarget(null);
      },
      onDragOver: (sceneId, edge) =>
        setDropTarget((prev) =>
          prev && prev.sceneId === sceneId && prev.edge === edge ? prev : { sceneId, edge },
        ),
      onDrop: handleReorderDrop,
      onKeyboardMove: handleKeyboardMove,
      onDeleteScene: handleDeleteScene,
    }),
    [dragging, dropTarget, handleReorderDrop, handleKeyboardMove, handleDeleteScene],
  );

  // ── Legacy chapter → scenes conversion (Task 10) ──────────────────────────
  // Dispatch the backend convert workflow, then poll (via useConvertPoll) until
  // scenes for this chapter materialise. Polling — rather than the TaskManager
  // realtime path — is chosen deliberately: it observes the exact end state we
  // care about (scenes with this chapter_id) instead of a task row.
  const handleConvertChapter = useCallback(
    async (chapterId: string) => {
      setConverting((prev) => ({ ...prev, [chapterId]: true }));
      try {
        await convertToScenes(scriptId, chapterId);
        addToast(t('editor.convertStarted'), 'info');
      } catch (err) {
        console.error('[EditorShell] convertToScenes failed', err);
        addToast(t('editor.convertFailed'), 'error');
        setConverting((prev) => ({ ...prev, [chapterId]: false }));
        return;
      }
      startPoll(
        (data) => data.scenes.some((s) => s.chapter_id === chapterId),
        (settled, data) => {
          setScenes([...data.scenes].sort((a, b) => a.sort_order - b.sort_order));
          setChapters(data.chapters);
          setConverting((prev) => ({ ...prev, [chapterId]: false }));
          if (settled) addToast(t('editor.convertDone'), 'success');
        },
      );
    },
    [scriptId, addToast, t, startPoll],
  );

  // An EMPTY legacy chapter has no prose for the AI convert to split — that
  // path calls the LLM and then `persist_scenes` skips element-less scenes, so
  // the poll never settles and the button spins forever. Instead turn the
  // chapter straight into a plain, typeable scene (no model call): create a
  // scene linked to the chapter, seed one empty action row, and drop the caret
  // in — the same seed handleCreateStory uses. The orphan card then disappears
  // (a scene now points at the chapter) and the writer is editing blocks.
  const handleStartChapter = useCallback(
    async (chapterId: string) => {
      setConverting((prev) => ({ ...prev, [chapterId]: true }));
      try {
        const created = await createScene(scriptId, {
          chapter_id: chapterId,
          sort_order: scenes.length,
        });
        const elementId = newElementId();
        const op: ElementOp = {
          op: 'insert',
          element_id: elementId,
          after_id: null,
          payload: { type: 'action', text: '' },
        };
        await applyOps(created.id, [op], created.content_version);
        await reload();
        setActiveScene(created.id);
        setPendingFocusId(elementId);
      } catch (err) {
        console.error('[EditorShell] start chapter failed', err);
        addToast(t('editor.convertFailed'), 'error');
      } finally {
        setConverting((prev) => ({ ...prev, [chapterId]: false }));
      }
    },
    [scriptId, scenes.length, reload, addToast, t],
  );

  const handleSelectType = useCallback(
    (type: ElementType) => {
      setNextInsertType(type);
      const cursor = cursorRef.current;
      if (cursor?.elementId) {
        nonceRef.current += 1;
        setTypeCommand({
          sceneId: cursor.sceneId,
          elementId: cursor.elementId,
          type,
          nonce: nonceRef.current,
        });
      }
    },
    [setNextInsertType],
  );

  const handleInsertScene = useCallback(async () => {
    // Single-flight: the create pipeline takes ~1-2s and the button gives no
    // feedback, so an impatient writer clicks again — every extra click minted
    // another empty scene (prod evidence 2026-07-15: 13 blank scenes in 19s,
    // 4 within one second). Ignore clicks while one create is in flight.
    if (insertingSceneRef.current) return;
    insertingSceneRef.current = true;
    try {
      // Anchor to the scene the CURSOR is in — state.cursor.sceneId tracks the
      // focused element and survives the toolbar button's transient focus steal
      // (blur only relaxes the editing flag, it never clears the cursor).
      // activeSceneId only follows scroll / rail-click, so it is often stale or
      // null when the writer clicks the Scene button (the v0.25.341 bug).
      // laper semantics (user 2026-07-21): the new heading lands AT the caret —
      // see planInsertScene for the split/above/after decision table.
      const plan = planInsertScene(scenes, state.cursor, state.activeSceneId);

      // createScene only positions by sort_order (appends) — create at the
      // tail, then place it. Placement and the split run concurrently: they
      // touch different resources (scene order vs element content).
      const created = await createScene(scriptId, { sort_order: scenes.length });
      const placement = plan.anchorSceneId
        ? moveScene(
            created.id,
            plan.placeBefore
              ? { before_scene_id: plan.anchorSceneId }
              : { after_scene_id: plan.anchorSceneId },
          )
        : Promise.resolve(null);

      const firstMovedId = plan.tail[0]?.id ?? null;
      const split = (async () => {
        if (plan.tail.length === 0 || !plan.anchorSceneId) return;
        const tail = plan.tail;
        // Insert into the NEW scene first, delete from the source after — the
        // cross-scene drag's ordering, so a mid-flight failure leaves
        // recoverable duplicates, never data loss.
        const inserts: ElementOp[] = tail.map((el, i) => ({
          op: 'insert',
          element_id: el.id,
          after_id: i === 0 ? null : tail[i - 1].id,
          payload: {
            type: el.type,
            text: el.text,
            ...(el.character_id ? { character_id: el.character_id } : {}),
          },
        }));
        await applyOps(created.id, inserts, created.content_version);
        // The writer was just TYPING in the source scene, so the version in
        // `scenes` state is stale (ops bump it server-side without flowing
        // back here) — a state-version If-Match would 409 nearly every time.
        // Fetch the current version right before the delete batch.
        const fresh = await listScenes(scriptId);
        const src = fresh.find((s) => s.id === plan.anchorSceneId);
        if (!src) return;
        const deletes: ElementOp[] = tail.map((el) => ({ op: 'delete', element_id: el.id }));
        await applyOps(plan.anchorSceneId, deletes, src.content_version);
      })();

      await Promise.all([placement, split]);
      await reload();
      setActiveScene(created.id);
      if (firstMovedId) setPendingFocusId(firstMovedId);
    } catch (err) {
      console.error('[EditorShell] createScene failed', err);
    } finally {
      insertingSceneRef.current = false;
    }
  }, [scriptId, scenes, state.cursor, state.activeSceneId, reload, setActiveScene]);

  // Cold start: create the first scene AND seed an empty action row (via the
  // documented ops endpoint — createScene does not accept initial elements), so
  // the writer lands on a real, focusable, Tab-ready line.
  const handleCreateStory = useCallback(async () => {
    // Same single-flight guard as handleInsertScene — a double-click on the
    // empty-state button minted two first scenes.
    if (insertingSceneRef.current) return;
    insertingSceneRef.current = true;
    try {
      const created = await createScene(scriptId, { sort_order: 0 });
      const elementId = newElementId();
      const op: ElementOp = {
        op: 'insert',
        element_id: elementId,
        after_id: null,
        payload: { type: 'action', text: '' },
      };
      await applyOps(created.id, [op], created.content_version);
      await reload();
      setActiveScene(created.id);
      setPendingFocusId(elementId);
    } catch (err) {
      console.error('[EditorShell] create story failed', err);
    } finally {
      insertingSceneRef.current = false;
    }
  }, [scriptId, reload, setActiveScene]);

  const handleFormatChange = useCallback(
    (format: EditorFormat) => {
      setFormat(format);
      persistFormat(scriptId, format);
    },
    [setFormat, scriptId],
  );

  // Current episode's display title for the rail selector (#1006: compare ids
  // as strings). Falls back to "Ep 1" when the script has no episode yet.
  const currentEpisodeTitle = useMemo(() => {
    const ep = episodes.find(
      (e) => currentEpisodeId != null && String(e.id) === String(currentEpisodeId),
    );
    return ep?.title ?? t('editor.episodeFallback');
  }, [episodes, currentEpisodeId, t]);

  const handleElementsChange = useCallback((sceneId: string, elements: ScriptElement[]) => {
    setLiveElements((prev) => ({ ...prev, [sceneId]: elements }));
  }, []);

  // Scenes with each block's live optimistic elements overlaid (Task 6 ⑥) — the
  // single source every derivation reads so Statistics, the CAST list and the
  // rail entity sections update as the writer types (debounced 1s upstream).
  // Stale ids (a since-removed scene) drop out because we map over `scenes`.
  const statsScenes = useMemo(
    () => scenes.map((s) => (liveElements[s.id] ? { ...s, elements: liveElements[s.id] } : s)),
    [scenes, liveElements],
  );

  // Active toolbar pill follows the cursor. Three cases (generalised after the
  // user caught it lagging piecemeal):
  //  - a HEADING field focused (slug button / int-ext / location / time) →
  //    the Scene pill;
  //  - an element focused → its LIVE (optimistic) type — the loaded snapshot
  //    misses blocks created/retyped this session, and SceneBlock reports
  //    structural changes immediately (text-only edits stay debounced);
  //  - otherwise → the pending next-insert type.
  const activeType: ElementType | 'scene' | null = (() => {
    const cursor = state.cursor;
    if (cursor && cursor.field !== 'element') return 'scene';
    if (cursor?.elementId) {
      for (const s of statsScenes) {
        const el = s.elements.find((e) => e.id === cursor.elementId);
        if (el) return el.type;
      }
    }
    return state.nextInsertType;
  })();

  // Project-level @-mention extras (PR-11, G13): the owning project's other
  // episodes' character names, fetched once per project id. Best-effort —
  // a failed fetch just leaves this empty and @-mention falls back to
  // script-only candidates (Phase 1 behavior), never blocking the editor.
  const [projectEntityCharacters, setProjectEntityCharacters] = useState<string[]>([]);
  useEffect(() => {
    if (!workspaceProjectId) {
      setProjectEntityCharacters([]);
      return;
    }
    let cancelled = false;
    fetchProjectEntities(workspaceProjectId)
      .then((data) => {
        if (!cancelled) setProjectEntityCharacters(data.characters.map((c) => c.name));
      })
      .catch((err) => {
        console.error('[EditorShell] failed to load project entities for @-mention:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceProjectId]);

  // Script-wide CAST names feed the @-mention / character-cue picker, unioned
  // with the project-level extras above (script names always take priority —
  // see mergeProjectMentionCandidates).
  const mentionCandidates = useMemo(
    () => mergeProjectMentionCandidates(deriveStatistics(statsScenes).cast, projectEntityCharacters),
    [statsScenes, projectEntityCharacters],
  );

  // Rail entity sections (laper info architecture) — Characters + Locations.
  const railCharacters = useMemo(() => deriveRailCharacters(statsScenes), [statsScenes]);
  const railLocations = useMemo(() => deriveRailLocations(statsScenes), [statsScenes]);
  // Distinct location names for the heading's search-or-create location picker.
  const locationCandidates = useMemo(() => railLocations.map((l) => l.name), [railLocations]);
  // Character-cue colour map (UPPERCASE name → rail colour) so each cue in the
  // sheet can prefix its name with the SAME colour dot the left rail's
  // Characters section paints (RailEntities uses CAST_COLORS[i % len] over this
  // exact first-seen order — see railDerive.deriveRailCharacters). Passed down
  // one hop to SceneBlock → TipTapSceneEditor, where the NodeView sets it as the
  // per-cue `--cue-dot` custom property.
  const castColors = useMemo(() => {
    const map: Record<string, string> = {};
    railCharacters.forEach((c, i) => {
      map[c.name.toUpperCase()] = CAST_COLORS[i % CAST_COLORS.length];
    });
    return map;
  }, [railCharacters]);

  // Auto-sync locations to the project Scene Library (场景库): whenever the set
  // of distinct location names changes, re-run the idempotent extract so a
  // location created in a scene heading shows up in the library WITHOUT the
  // manual "Extract from script" step (user request). Debounced so it fires
  // once the meta write (updateSceneMeta, 600ms) has persisted the header the
  // extract reads server-side; best-effort (a background sync, no toast).
  const locationSyncKey = useMemo(
    () => [...locationCandidates].map((n) => n.toUpperCase()).sort().join('|'),
    [locationCandidates],
  );
  const lastLocationSyncRef = useRef<string>('');
  useEffect(() => {
    if (!workspaceProjectId) return;
    if (locationSyncKey === lastLocationSyncRef.current) return;
    const id = setTimeout(() => {
      lastLocationSyncRef.current = locationSyncKey;
      extractLibEntitiesFromScript(workspaceProjectId, 'location').catch((err) =>
        console.error('[EditorShell] auto-sync locations to Scene Library failed', err),
      );
    }, 1500);
    return () => clearTimeout(id);
  }, [locationSyncKey, workspaceProjectId]);

  // Legacy chapters with no scene pointing at them → read-only prose fallbacks.
  const orphanChapters = useMemo(() => {
    const claimed = new Set(scenes.map((s) => s.chapter_id).filter(Boolean));
    return chapters.filter((ch) => !claimed.has(ch.id));
  }, [scenes, chapters]);

  // Windowing: above the threshold, only mount scenes near the viewport. Heights
  // are estimated per scene (element count) and cached across renders.
  const windowed = scenes.length > WINDOW_THRESHOLD;
  const sceneHeights = useMemo(
    () => scenes.map((s) => estimateSceneHeight(s.elements.length)),
    [scenes],
  );

  // Continuous per-block numbering (A1): each scene's cumulative offset into
  // the document-order block count, so heading + element numbers never reset
  // at a scene boundary (laper.ai-style).
  const blockBases = useMemo(() => sceneBlockBases(scenes), [scenes]);
  // laper-parity: the kbd hint rows show inside the sheet top ONLY while the
  // script has no typed text yet — a real script page stays hint-free.
  const scriptUntouched = useMemo(
    () => scenes.every((s) => s.elements.every((e) => !e.text.trim())),
    [scenes],
  );

  // ── Paged-mode page seams (v2 — real page look) ───────────────────────────
  // Measure the sheet's rows after layout, subtract any already-rendered seam
  // heights to get stable CONTENT coordinates (one-pass fixed point: inserting
  // seams never changes content coordinates), and compute the seam list (pure
  // math + keep-together rules in paginate.ts). A ResizeObserver re-runs the
  // measurement as typing reflows the sheet; the deep-equality guard below
  // stops the observe→setState→observe loop once the layout converges.
  const pageSheetRef = useRef<HTMLDivElement | null>(null);
  const [pageSeams, setPageSeams] = useState<Map<string, { page: number; filler: number }>>(
    () => new Map(),
  );
  useLayoutEffect(() => {
    if (paginationMode !== 'paged' || state.mode !== 'script') {
      setPageSeams((prev) => (prev.size === 0 ? prev : new Map()));
      return;
    }
    const sheet = pageSheetRef.current;
    if (!sheet) return;
    let frame = 0;
    const compute = () => {
      // getBoundingClientRect returns VISUAL px — the sheet's CSS `zoom` scales
      // every reading by k. Divide back to logical (100%) coordinates so the
      // break math (and thus the seam beforeKey sequence) is identical at every
      // zoom level; the filler we store is logical too, and renders k× larger
      // inside the zoomed sheet, keeping page bottoms aligned. k is read from
      // the known state value (zoom lives on this very sheet).
      const k = sheetZoom / 100;
      const sheetTop = sheet.getBoundingClientRect().top;
      const seamBoxes = Array.from(sheet.querySelectorAll<HTMLElement>('.mh-page-seam')).map(
        (el) => {
          const r = el.getBoundingClientRect();
          return { top: (r.top - sheetTop) / k, height: r.height / k };
        },
      );
      const seamHeightAbove = (top: number) =>
        seamBoxes.reduce((acc, s) => acc + (s.top < top ? s.height : 0), 0);

      const rows: MeasuredRow[] = [];
      const rowEls = Array.from(
        sheet.querySelectorAll<HTMLElement>(
          '.mh-scene-headrow, .mh-el-row, .mh-scene-placeholder',
        ),
      );
      for (const el of rowEls) {
        const r = el.getBoundingClientRect();
        const top = (r.top - sheetTop) / k;
        const bottom = (r.bottom - sheetTop) / k;
        const sub = seamHeightAbove(top);
        let key = '';
        let kind: RowKind = 'action';
        if (el.classList.contains('mh-scene-headrow')) {
          const sid = (el.closest('[data-scene-id]') as HTMLElement | null)?.dataset.sceneId;
          if (!sid) continue;
          key = `scene:${sid}`;
          kind = 'heading';
        } else if (el.classList.contains('mh-scene-placeholder')) {
          const sid = el.dataset.sceneId;
          if (!sid) continue;
          key = `scene:${sid}`;
          kind = 'placeholder';
        } else {
          const editable = el.querySelector<HTMLElement>('[data-el-id]');
          if (!editable) continue;
          key = editable.dataset.elId ?? '';
          kind = (editable.dataset.elType as RowKind) || 'action';
        }
        if (!key) continue;
        rows.push({ key, kind, top: top - sub, bottom: bottom - sub });
      }

      const layout = computePageLayout(rows);
      const next = new Map(
        layout.seams.map((s) => [s.beforeKey, { page: s.page, filler: s.filler }]),
      );
      setPageSeams((prev) => {
        if (prev.size === next.size) {
          let same = true;
          for (const [k, v] of next) {
            const p = prev.get(k);
            if (!p || p.page !== v.page || Math.abs(p.filler - v.filler) > 1) {
              same = false;
              break;
            }
          }
          if (same) return prev;
        }
        return next;
      });
    };
    compute();
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(compute);
    });
    ro.observe(sheet);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
    };
  }, [paginationMode, state.mode, scenes, sheetZoom]);

  // ── Cross-scene paragraph drag coordination ───────────────────────────────
  // The shell broadcasts which element is mid-drag (and from which scene) so
  // every other SceneBlock can accept the drop; each block registers an op
  // dispatcher here so the target block can ask for the source-scene delete.
  const [elementDrag, setElementDrag] = useState<{
    sceneId: string;
    element: ScriptElement;
  } | null>(null);
  const externalOpsRef = useRef(new Map<string, (ops: ElementOp[]) => void>());
  const handleRegisterExternalOps = useCallback(
    (sceneId: string, fn: ((ops: ElementOp[]) => void) | null) => {
      if (fn) externalOpsRef.current.set(sceneId, fn);
      else externalOpsRef.current.delete(sceneId);
    },
    [],
  );
  const handleElementDragBegin = useCallback(
    (sceneId: string, element: ScriptElement) => setElementDrag({ sceneId, element }),
    [],
  );
  const handleElementDragDone = useCallback(() => setElementDrag(null), []);
  const handleCrossSceneDelete = useCallback((sceneId: string, elementId: string) => {
    externalOpsRef.current.get(sceneId)?.([{ op: 'delete', element_id: elementId }]);
  }, []);

  const recomputeWindow = useCallback(() => {
    if (!windowed) return;
    const el = sheetScrollRef.current;
    const next = computeSceneWindow(
      sceneHeights,
      el?.scrollTop ?? 0,
      el?.clientHeight ?? 0,
    );
    setSceneWindow((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
  }, [windowed, sceneHeights]);

  // Seed the window from the current scroll offset whenever the scene set or
  // windowing toggle changes; rAF-throttled scroll keeps it in step thereafter.
  useEffect(() => {
    if (!windowed) return;
    recomputeWindow();
    const el = sheetScrollRef.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        recomputeWindow();
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [windowed, recomputeWindow]);

  // Aggregate every scene's save state into one headline (worst-wins). Only the
  // currently loaded scenes count, so a deleted scene's stale state drops out.
  const perSceneState = (s: SceneDoc): SaveState => syncStates[s.id]?.saveState ?? 'saved';
  const aggregateState = aggregateSaveState(scenes.map(perSceneState));
  const offlineCount = scenes.filter((s) => perSceneState(s) === 'offline').length;
  const conflictScene = scenes.find((s) => perSceneState(s) === 'conflict') ?? null;

  const resolveConflict = useCallback(
    (choice: 'mine' | 'theirs') => {
      if (conflictScene) syncStates[conflictScene.id]?.resolveConflict(choice);
    },
    [conflictScene, syncStates],
  );

  // ── Collaboration presence (Phase B P5 / C1) ───────────────────────────────
  // Track who else is in this script and which scene they are focused on. The
  // hook receives null when the flag is off, so nothing subscribes. `isDirty`
  // drives our broadcast mode: any scene not fully saved → 'editing' (C3).
  const presenceSelf =
    COLLAB_ENABLED && currentUserId
      ? {
          userId: currentUserId,
          name: currentUserName ?? currentUserId,
          focusedSceneId: state.activeSceneId,
          isDirty: aggregateState !== 'saved',
        }
      : null;
  const { onlineUsers } = useScriptPresence(
    COLLAB_ENABLED ? scriptId : null,
    presenceSelf,
  );

  // Group other participants by the scene they are focused on, for the soft
  // per-scene badges on the sheet and in the node view. Keys are String()'d to
  // match the String(s.id) lookup at the render sites (#1006: scene ids are JSON
  // numbers at runtime despite the string type).
  const presenceByScene = useMemo<ScenePresenceMap>(() => {
    const map: ScenePresenceMap = {};
    for (const u of onlineUsers) {
      if (u.focused_scene_id == null) continue;
      const sceneId = String(u.focused_scene_id);
      (map[sceneId] ??= []).push(u);
    }
    return map;
  }, [onlineUsers]);

  // ── Live op streaming (Phase B P5 / C2) ────────────────────────────────────
  // Each mounted SceneBlock registers its applyRemoteOps here; the realtime hook
  // routes an incoming script_ops row to the matching scene. When a row arrives
  // for a scene that is NOT currently mounted (windowed out above the virtualize
  // threshold), there is no handler to apply it — we record that scene id in
  // `droppedScenes` so the block refetches its (now stale) snapshot the moment it
  // remounts, rather than showing pre-op text until the next reload.
  // Every scene id crossing into these Map/Set keys is String()'d on BOTH sides
  // (#1006: the scenes API returns id as a JSON number, so `scene.id` is a number
  // at runtime and `String(record.scene_id)` from the realtime row is a string —
  // an un-coerced key silently misses and every op is dropped).
  const remoteApplyRef = useRef<Map<string, (row: RemoteOpRow) => void>>(new Map());
  const [droppedScenes, setDroppedScenes] = useState<ReadonlySet<string>>(new Set());
  const registerRemoteApply = useCallback(
    (sceneId: string, apply: ((row: RemoteOpRow) => void) | null) => {
      const key = String(sceneId);
      if (apply) remoteApplyRef.current.set(key, apply);
      else remoteApplyRef.current.delete(key);
    },
    [],
  );
  const getSceneIds = useCallback(() => scenesRef.current.map((s) => String(s.id)), []);
  const dispatchToScene = useCallback((sceneId: string, row: RemoteOpRow) => {
    const key = String(sceneId);
    const handler = remoteApplyRef.current.get(key);
    if (handler) {
      handler(row);
      return;
    }
    // No mounted block for this scene — remember it so the block refetches on
    // remount (M1).
    setDroppedScenes((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  }, []);
  const handleRemoteStaleHandled = useCallback((sceneId: string) => {
    const key = String(sceneId);
    setDroppedScenes((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  }, []);
  useScriptOpsRealtime(COLLAB_ENABLED ? scriptId : null, {
    getSceneIds,
    dispatchToScene,
    onReconcile: reload,
  });

  // ── Divergence beacon (Phase B P5 / C3) ────────────────────────────────────
  // One low-frequency signal each time a scene ENTERS the conflict state so the
  // collaboration divergence rate stays observable: console + the frontend log
  // pipeline (frontend_error_logs; the only persistent client channel — the
  // `collab_divergence` metadata tag makes these greppable/filterable in
  // monitoring since divergence is expected, not a fault).
  const divergedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!COLLAB_ENABLED) return;
    const now = new Set(
      scenes
        .filter((s) => syncStates[s.id]?.saveState === 'conflict')
        .map((s) => String(s.id)),
    );
    for (const sceneId of now) {
      if (divergedRef.current.has(sceneId)) continue;
      console.info('[collab] divergence', { sceneId });
      void reportError(`[collab] divergence scene=${sceneId}`, {
        type: 'runtime',
        component: 'EditorShell',
        metadata: { kind: 'collab_divergence', sceneId, scriptId },
      });
    }
    divergedRef.current = now;
  }, [scenes, syncStates, scriptId]);

  // Cold start ONLY when the script is truly empty. A legacy script with
  // prose chapters but no scenes must land on the chapter fallback cards
  // (with their Convert to Scenes entry) — the full-screen cold start would
  // hide the user's existing work behind a "Create Story" button (go-live
  // gate follow-up #1, 2026-07-06).
  const showColdStart =
    state.mode === 'script' &&
    loadState === 'ready' &&
    scenes.length === 0 &&
    orphanChapters.length === 0;

  // The floating scene TOC belongs only to the script sheet — the node canvas,
  // storyboard, beats and outline views own their own navigation. SceneToc
  // itself renders nothing when scenes.length === 0, so the gate is just the
  // view/mode context.
  const showSceneToc = railView === 'script' && state.mode === 'script' && !showColdStart;

  // Focus the seeded row once the new scene has rendered (cold start). The
  // TipTap editor builds its ProseMirror view in an effect, so the row may not
  // be in the DOM on the same commit the scene lands — poll a few frames until
  // it appears rather than firing once and missing.
  useEffect(() => {
    if (!pendingFocusId) return;
    let raf = 0;
    let tries = 0;
    const tryFocus = () => {
      const node = shellRef.current?.querySelector<HTMLElement>(
        `[data-el-id="${pendingFocusId}"]`,
      );
      if (node) {
        node.focus();
        setPendingFocusId(null);
        return;
      }
      if (tries++ < 30) raf = requestAnimationFrame(tryFocus);
    };
    tryFocus();
    return () => cancelAnimationFrame(raf);
  }, [pendingFocusId, scenes]);

  const tabLabel: Record<EditorMode, string> = {
    script: t('editor.tabScript'),
    outline: t('editor.tabOutline'),
    cover: t('editor.tabCover'),
  };

  return (
    <div
      className={`mh-editor-shell${embedded ? ' mh-embedded' : ''}`}
      data-theme={state.theme}
      // Styling hook consumed by editorShellStyles: "true" while a script line
      // is focused lights up the element toolbar. Not a focus/a11y signal.
      data-editing={editing ? 'true' : 'false'}
      data-editor-shell
      ref={shellRef}
    >
      <style>{EDITOR_SHELL_STYLES}</style>

      {loadState === 'loading' && (
        <div className="mh-shell-state" role="status">
          <Loading label={t('editor.loading')} />
        </div>
      )}
      {loadState === 'error' && (
        <div className="mh-shell-state error" role="alert">
          <div>{t('editor.loadError')}</div>
        </div>
      )}

      {/* ===== LEFT RAIL (standalone only) =====
          Embedded (studio) mode drops the rail entirely — the workspace tree
          is the single side navigation and the scene list is lifted up to it
          (合一终稿, 2026-07-11). The grid is `auto minmax(0,1fr) auto`, so with
          the nav gone the shell reflows to the two remaining columns. */}
      {!embedded && (
        <nav
          className={`mh-island mh-rail${railCollapsed ? ' collapsed' : ''}`}
          aria-label={t('editor.railNav')}
        >
          {railCollapsed ? (
            <div className="mh-rail-collapsed-strip">
              <button
                type="button"
                className="mh-icon-btn"
                aria-label={t('editor.expandLeft')}
                onClick={() => setRailCollapsed(false)}
              >
                ›
              </button>
              <span className="mh-brand-dot" aria-hidden="true" />
            </div>
          ) : (
            <>
              <div className="mh-rail-top">
                <div className="mh-brand-row">
                  <span className="mh-brand-dot" aria-hidden="true" />
                  {t('editor.brand')}
                  <button
                    type="button"
                    className="mh-icon-btn"
                    style={{ marginLeft: 'auto' }}
                    aria-label={t('editor.collapseLeft')}
                    onClick={() => setRailCollapsed(true)}
                  >
                    ‹
                  </button>
                </div>
                <div className="mh-ep-selector-wrap">
                  <button
                    type="button"
                    className="mh-ep-selector"
                    aria-expanded={episodePanelOpen}
                    aria-label={t('editor.manageEpisodes')}
                    onClick={() => setEpisodePanelOpen((v) => !v)}
                  >
                    <div className="mh-ep-name">{currentEpisodeTitle}</div>
                    <div className="mh-ep-sub">
                      {t('editor.sceneCount', { count: scenes.length })}
                    </div>
                  </button>
                  {episodePanelOpen && projectId && (
                    <EpisodePanel
                      scriptId={scriptId}
                      projectId={projectId}
                      episodes={episodes}
                      currentEpisodeId={currentEpisodeId}
                      onChanged={reloadEpisodes}
                      onClose={() => setEpisodePanelOpen(false)}
                    />
                  )}
                </div>
              </div>
              <RailModules activeView={railView} onSelect={selectRailView} />
              {/* The SCENE list moved OUT of this boxed rail into the floating
                  Notion-style SceneToc that overlays the paper's left edge (it
                  no longer reads as "又是一个框"). This rail keeps only the
                  brand / episode selector / Views nav / entity sections; scene
                  navigation is the SceneToc in the centre column below. */}
              <div className="mh-rail-scroll">
                <RailEntities
                  characters={railCharacters}
                  locations={railLocations}
                  onSelect={handleSelectScene}
                />
              </div>
            </>
          )}
        </nav>
      )}

      {/* Embedded (studio) mode has NO boxed left rail: the workspace tree owns
          episode + module navigation, and scene navigation is the floating
          SceneToc overlaying the paper (rendered in the centre column below),
          identical to the standalone route. The shell reflows to two columns
          (paper + Writing) — see .mh-editor-shell.mh-embedded. Scenes still lift
          up via onScenesChange for the workspace top-bar slate. */}

      {/* ===== CENTER PAPER COLUMN ===== */}
      <main className="mh-center-col" aria-label={t('editor.paperColumn')}>
        <div className="mh-center-topbar">
          {/* The Beats workbench renders its Arrangement/List switch in this SAME
              slot with the SAME mh-doc-tabs classes as the Script/Outline/Cover
              document tabs, so the two same-class blocks sit at one position under
              one stylesheet (user verdict 2026-07-20) instead of Beats drawing its
              own segmented one row lower. */}
          {railView === 'beats' ? (
            <div className="mh-doc-tabs" role="tablist" aria-label={t('editor.beatsViewLabel')}>
              <button
                type="button"
                role="tab"
                className="mh-doc-tab"
                aria-selected={beatsSubview === 'arrangement'}
                data-testid="beats-subview-arrangement"
                onClick={() => selectBeatsSubview('arrangement')}
              >
                {t('editor.beatsViewArrangement')}
              </button>
              <button
                type="button"
                role="tab"
                className="mh-doc-tab"
                aria-selected={beatsSubview === 'list'}
                data-testid="beats-subview-list"
                onClick={() => selectBeatsSubview('list')}
              >
                {t('editor.beatsViewList')}
              </button>
            </div>
          ) : (
            <div className="mh-doc-tabs" role="tablist" aria-label={t('editor.docModes')}>
              {DOC_MODES.map((m) => (
                <button
                  type="button"
                  key={m}
                  role="tab"
                  className="mh-doc-tab"
                  aria-selected={state.mode === m}
                  onClick={() => selectMode(m)}
                >
                  {tabLabel[m]}
                </button>
              ))}
            </div>
          )}
          <div className="mh-topbar-right">
            {COLLAB_ENABLED && <PresenceAvatars users={onlineUsers} />}
            <SaveIndicator state={aggregateState} queued={offlineCount} />
            {/* Theme is GLOBAL-ONLY (Settings → Theme). The per-shell ☾/☼
                session override was removed by user verdict 2026-07-20 — the
                shell still follows <html data-theme> via the observer above. */}
          </div>
        </div>

        <div className="mh-page-frame">
          {showSceneToc && (
            <SceneToc
              scenes={scenes}
              activeSceneId={state.activeSceneId}
              onSelect={handleSelectScene}
              scriptId={scriptId}
            />
          )}
          {railView === 'nodes' ? (
            <ScenePresenceContext.Provider value={presenceByScene}>
              <NodesView
                scenes={scenes}
                chapters={chapters}
                onOpenScene={handleOpenScene}
                scriptId={scriptId}
                onReload={reloadAll}
                onBackToScript={() => selectRailView('script')}
              />
            </ScenePresenceContext.Provider>
          ) : railView === 'storyboard' ? (
            <StoryboardView scenes={scenes} scriptId={scriptId} />
          ) : railView === 'beats' ? (
            <BeatsView
              scenes={scenes}
              scriptId={scriptId}
              onOpenScene={handleOpenScene}
              subview={beatsSubview}
            />
          ) : state.mode === 'cover' ? (
            <div className="mh-sheet-scroll">
              <div className="mh-cover-card" data-testid="cover-placeholder">
                {t('editor.coverPlaceholder')}
              </div>
            </div>
          ) : showColdStart ? (
            <div className="mh-sheet-scroll">
              <ColdStart
                onCreateStory={handleCreateStory}
                onImport={projectId ? () => setShowImportModal(true) : undefined}
              />
            </div>
          ) : (
            <div className="mh-sheet-scroll" ref={sheetScrollRef}>
              {conflictScene && (
                <ConflictBar
                  onKeepMine={() => resolveConflict('mine')}
                  onTakeTheirs={() => resolveConflict('theirs')}
                />
              )}
              <ElementToolbar
                mode={state.mode}
                activeType={activeType}
                onSelectType={handleSelectType}
                onInsertScene={handleInsertScene}
              />
              {/* Version COMPARE floats in the empty margin RIGHT OF the sheet
                  (user direction: the diff sits next to the live text so both
                  are visible at once — no centre-pane takeover). Sticky
                  zero-height wrapper keeps the card in view while the paper
                  scrolls; Back closes it. The version LIST lives in the
                  Writing island (WritingPanel). */}
              {state.mode !== 'outline' && diffCommit && (
                <div className="mh-version-rail mh-diff-rail" data-testid="diff-rail">
                  <div className="mh-version-rail-inner">
                    <VersionDiff
                      scriptId={scriptId}
                      commit={diffCommit}
                      scenes={scenes}
                      currentUserId={currentUserId}
                      onBack={() => setDiffCommit(null)}
                      onJumpTo={handleJumpToDiff}
                    />
                  </div>
                </div>
              )}
              <div
                className={`mh-sheet${state.format === 'asian' ? ' asian' : ''}`}
                ref={pageSheetRef}
                style={{ zoom: sheetZoom / 100 }}
                data-zoom={sheetZoom}
              >
                <div className="mh-sheet-inner">
                  {/* `scriptUntouched` is `[].every(...)` === true while scenes
                      are still loading, so the empty-script keyboard hint must
                      NOT show until the load resolves — otherwise every open of
                      a non-empty script flashes the "empty script" affordance
                      before its scenes arrive (the loading overlay paints
                      behind the paper, so it can't hide this). Gate on ready. */}
                  {loadState === 'ready' && state.mode !== 'outline' && scriptUntouched && (
                    <div className="mh-keyboard-hint" aria-hidden="true">
                      <div>
                        <kbd>Tab</kbd> {t('editor.hintTab')}
                      </div>
                      <div>
                        <kbd>Enter</kbd> {t('editor.hintEnter')}
                      </div>
                      <div>
                        <kbd>@</kbd> {t('editor.hintMention')}
                      </div>
                    </div>
                  )}
                  {state.mode === 'outline' ? (
                    <OutlineView
                      scenes={scenes}
                      chapters={chapters}
                      onOpenScene={handleOpenScene}
                      onReload={reloadAll}
                      orphanChapters={orphanChapters}
                      converting={converting}
                      onConvert={handleConvertChapter}
                      onStartWriting={handleStartChapter}
                    />
                  ) : (
                    <>
                      {scenes.map((s, i) => {
                        const mounted =
                          !windowed || (i >= sceneWindow.start && i <= sceneWindow.end);
                        // Paged v2: a seam keyed `scene:<id>` lands BEFORE this
                        // scene (its heading starts the next page).
                        const sceneSeam = pageSeams.get(`scene:${s.id}`);
                        if (!mounted) {
                          // A windowed-out scene is still a valid drop target so a
                          // drag can cross the mounted window: dropping on the
                          // placeholder lands the dragged scene BEFORE it. Keyboard
                          // reorder (Alt+Arrow) covers the a11y path, so the
                          // decorative placeholder stays aria-hidden.
                          return (
                            <Fragment key={s.id}>
                            {sceneSeam && (
                              <PageSeam page={sceneSeam.page} filler={sceneSeam.filler} />
                            )}
                            <div
                              className="mh-scene-placeholder"
                              data-testid="scene-placeholder"
                              data-scene-id={s.id}
                              style={{ height: sceneHeights[i] }}
                              aria-hidden="true"
                              onDragOver={
                                reorder.draggingId
                                  ? (e) => {
                                      e.preventDefault();
                                      reorder.onDragOver(s.id, 'before');
                                    }
                                  : undefined
                              }
                              onDrop={
                                reorder.draggingId
                                  ? (e) => {
                                      e.preventDefault();
                                      reorder.onDrop(s.id, 'before');
                                    }
                                  : undefined
                              }
                            />
                            </Fragment>
                          );
                        }
                        return (
                          <Fragment key={`${s.id}:${rollbackNonce}`}>
                          {sceneSeam && (
                            <PageSeam page={sceneSeam.page} filler={sceneSeam.filler} />
                          )}
                          <SceneBlock
                            scene={s}
                            index={i}
                            blockIndexBase={blockBases[i]}
                            format={state.format}
                            mentionCandidates={mentionCandidates}
                            castColors={castColors}
                            locationCandidates={locationCandidates}
                            onFocusElement={handleFocusElement}
                            onSyncStateChange={handleSyncStateChange}
                            onExitEditing={handleExitEditing}
                            reorder={reorder}
                            copilotActiveSceneId={copilotSceneId}
                            onCopilotActivate={handleCopilotActivate}
                            onElementsChange={handleElementsChange}
                            typeCommand={typeCommand ?? undefined}
                            focusPresence={presenceByScene[String(s.id)]}
                            selfActorId={currentUserId}
                            elementDrag={elementDrag}
                            onElementDragBegin={handleElementDragBegin}
                            onElementDragDone={handleElementDragDone}
                            onCrossSceneDelete={handleCrossSceneDelete}
                            onRegisterExternalOps={handleRegisterExternalOps}
                            pageSeams={pageSeams}
                            onRegisterRemoteApply={
                              COLLAB_ENABLED ? registerRemoteApply : undefined
                            }
                            remoteStale={COLLAB_ENABLED && droppedScenes.has(String(s.id))}
                            onRemoteStaleHandled={
                              COLLAB_ENABLED ? handleRemoteStaleHandled : undefined
                            }
                          />
                          </Fragment>
                        );
                      })}
                    </>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

      </main>

      {/* ===== RIGHT PANEL ===== */}
      {/* Hidden in the Beats workbench — version history / pagination / stats /
          cast are script-document concerns; the timeline gets the width. */}
      {railView !== 'beats' && (
      <aside
        className={`mh-island mh-right-col${panelCollapsed ? ' collapsed' : ''}`}
        aria-label={t('editor.writingPanel')}
      >
        {panelCollapsed ? (
          <div className="mh-panel-collapsed-strip">
            <button
              type="button"
              className="mh-icon-btn"
              aria-label={t('editor.expandRight')}
              onClick={() => setPanelCollapsed(false)}
            >
              ‹
            </button>
          </div>
        ) : (
          <>
            <div className="mh-panel-head">
              <div className="mh-panel-title">{t('editor.writing')}</div>
              <button
                type="button"
                className="mh-icon-btn"
                aria-label={t('editor.collapseRight')}
                onClick={() => setPanelCollapsed(true)}
              >
                ›
              </button>
            </div>
            <WritingPanel
              scenes={statsScenes}
              format={state.format}
              onFormatChange={handleFormatChange}
              pagination={paginationMode}
              onPaginationChange={handlePaginationChange}
              zoom={sheetZoom}
              onZoomChange={handleZoomChange}
              scriptId={scriptId}
              onCompareCommit={handleCompareCommit}
              onRolledBack={handleRolledBack}
              currentUserId={currentUserId}
            />
          </>
        )}
      </aside>
      )}

      {showImportModal && projectId && (
        <ImportScriptModal
          projectId={projectId}
          onClose={() => setShowImportModal(false)}
        />
      )}

      {/* laper-style "select text → AI chat" pill. Mounted here (inside the
          data-theme root so its ink chrome resolves per editor theme); it is
          position:fixed and only paints while script text is selected. Works
          in both the fullscreen route and the embedded studio — the store it
          drives is global, so the quote lands in whichever chat host is up. */}
      <SelectionAiChatButton />
    </div>
  );
}
