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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  applyOps,
  convertToScenes,
  createScene,
  listEpisodes,
  listScenes,
  moveScene,
  newElementId,
  type Episode,
  type ScriptCommit,
} from '../sceneService';
import { fetchScriptProject } from '../../services/scriptService';
import { fetchProjectEntities } from '../../services/projectsService';
import { useToast } from '../../components/Toast';
import { mergeProjectMentionCandidates } from '../mentionCandidates';
import type { CursorState } from '../editorMachine';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';
import { useEditorState, type EditorFormat, type EditorMode } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { persistFormat, readStoredFormat } from '../formatStorage';
import { persistRailView, readStoredRailView } from '../railViewStorage';
import { useConvertPoll } from '../useConvertPoll';
import {
  WINDOW_THRESHOLD,
  computeSceneWindow,
  estimateSceneHeight,
  type SceneWindow,
} from '../windowing';
import { EDITOR_SHELL_STYLES } from './editorShellStyles';
import {
  SceneBlock,
  type TypeCommand,
  type SceneSyncStatus,
  type SceneReorderApi,
} from './SceneBlock';
import { SceneRail } from './SceneRail';
import { RailModules, type RailView } from './RailModules';
import { BeatsView } from '../beats/BeatsView';
import { NodesView } from '../nodes/NodesView';
import { StoryboardView } from '../storyboard/StoryboardView';
import { VersionDiff } from '../versions/VersionDiff';
import { OutlineView } from './OutlineView';
import { EpisodePanel } from './EpisodePanel';
import { RailEntities } from './RailEntities';
import { deriveRailCharacters, deriveRailLocations } from '../railDerive';
import { ElementToolbar } from './ElementToolbar';
import { WritingPanel, deriveStatistics } from './WritingPanel';
import { SaveIndicator, aggregateSaveState } from './SaveIndicator';
import { ConflictBar } from './ConflictBar';
import { ColdStart } from './EmptyStates';
import { ImportScriptModal } from './ImportScriptModal';
import { ChapterFallback } from './ChapterFallback';
import { useScriptPresence } from '../collab/useScriptPresence';
import { useScriptOpsRealtime } from '../collab/useScriptOpsRealtime';
import { PresenceAvatars } from '../collab/PresenceAvatars';
import { ScenePresenceContext, type ScenePresenceMap } from '../collab/scenePresenceContext';
import type { RemoteOpRow } from '../useSceneSync';
import { reportError } from '../../services/errorReporter';

type LoadState = 'loading' | 'ready' | 'error';

// Realtime collaboration is flag-dark: off → the presence hook receives null and
// nothing subscribes (zero mount / zero channel), matching the P5 constraint.
const COLLAB_ENABLED = import.meta.env.VITE_FEATURE_COLLAB === 'true';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

// Episode ids are native numbers at runtime though typed string (#1006) —
// compare through String() like the rest of the editor.
function sameEpisodeId(a: unknown, b: unknown): boolean {
  return a != null && b != null && String(a) === String(b);
}

export function EditorShell({
  scriptId,
  currentUserId = null,
  currentUserName,
  projectId: workspaceProjectId,
  initialRailView,
  embedded = false,
  episodesForSwitcher,
  onSwitchEpisode,
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
   * Studio mode: the shell is mounted inside the project workspace, which
   * hides its own sidebar while the studio is open — the editor rail is then
   * the single side navigation (R2-A single-rail look). Embedded hides the
   * rail's brand row (the workspace top bar carries project identity) and
   * turns the episode selector into a switcher that delegates to
   * `onSwitchEpisode`. Standalone (fullscreen route) stays unchanged.
   */
  embedded?: boolean;
  /** Episode list for the embedded switcher (workspace-owned). */
  episodesForSwitcher?: Array<{ episode_id: string; title: string }>;
  /** Embedded switcher selection — the workspace re-resolves + remounts. */
  onSwitchEpisode?: (episodeId: string) => void;
}) {
  const { t } = useTranslation();
  // Restore the per-script layout engine synchronously so the first paint uses
  // it (no engine flash on remount).
  const initialFormat = useMemo(() => readStoredFormat(scriptId) ?? undefined, [scriptId]);
  const { state, setMode, setFormat, toggleTheme, setTheme, setActiveScene, setCursor, setNextInsertType } =
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
  // Embedded-only: the lightweight episode SWITCH menu (vs. the standalone
  // EpisodePanel, which manages episodes — that job belongs to the
  // workspace's Episodes module when embedded).
  const [epSwitchOpen, setEpSwitchOpen] = useState(false);
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

  // A rollback wrote new ops server-side — drop the stale optimistic overlay and
  // re-fetch the scenes so the sheet reflects the rolled-back content.
  const handleRolledBack = useCallback(() => {
    setLiveElements({});
    void reload();
  }, [reload]);

  const selectRailView = useCallback((view: RailView) => {
    setDiffCommit(null);
    setRailView(view);
  }, []);

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
    }),
    [dragging, dropTarget, handleReorderDrop, handleKeyboardMove],
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

  const handleInsertScene = useCallback(() => {
    createScene(scriptId, { sort_order: scenes.length })
      .then(() => reload())
      .catch((err) => console.error('[EditorShell] createScene failed', err));
  }, [scriptId, scenes.length, reload]);

  // Cold start: create the first scene AND seed an empty action row (via the
  // documented ops endpoint — createScene does not accept initial elements), so
  // the writer lands on a real, focusable, Tab-ready line.
  const handleCreateStory = useCallback(async () => {
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
    }
  }, [scriptId, reload, setActiveScene]);

  const handleFormatChange = useCallback(
    (format: EditorFormat) => {
      setFormat(format);
      persistFormat(scriptId, format);
    },
    [setFormat, scriptId],
  );

  // Active toolbar pill follows the cursor element's type (from the loaded
  // snapshot); falls back to the pending next-insert type when unknown.
  const activeType: ElementType | null = (() => {
    const cursor = state.cursor;
    if (cursor?.elementId) {
      for (const s of scenes) {
        const el = s.elements.find((e) => e.id === cursor.elementId);
        if (el) return el.type;
      }
    }
    return state.nextInsertType;
  })();

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

  // Focus the seeded row once the new scene has rendered (cold start).
  useEffect(() => {
    if (!pendingFocusId) return;
    const node = shellRef.current?.querySelector<HTMLElement>(`[data-el-id="${pendingFocusId}"]`);
    if (node) {
      node.focus();
      setPendingFocusId(null);
    }
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
          {t('editor.loading')}
        </div>
      )}
      {loadState === 'error' && (
        <div className="mh-shell-state error" role="alert">
          <div>{t('editor.loadError')}</div>
        </div>
      )}

      {/* ===== LEFT RAIL ===== */}
      <nav
        className={`mh-island mh-rail${railCollapsed ? ' collapsed' : ''}`}
        aria-label={t('editor.scenesNav')}
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
              {/* Studio mode (embedded in the workspace): the rail is the ONLY
                  side navigation, so the episode selector stays — but the
                  brand row goes (the workspace top bar already carries the
                  project identity + back affordance). */}
              {!embedded && (
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
              )}
              <div className="mh-ep-selector-wrap">
                <button
                  type="button"
                  className="mh-ep-selector"
                  aria-expanded={embedded ? epSwitchOpen : episodePanelOpen}
                  aria-label={
                    embedded ? t('editor.switchEpisode') : t('editor.manageEpisodes')
                  }
                  onClick={() =>
                    embedded
                      ? setEpSwitchOpen((v) => !v)
                      : setEpisodePanelOpen((v) => !v)
                  }
                >
                  <div className="mh-ep-name">{currentEpisodeTitle}</div>
                  <div className="mh-ep-sub">
                    {t('editor.sceneCount', { count: scenes.length })}
                  </div>
                </button>
                {/* Embedded switcher: a plain episode list that hands the
                    switch to the workspace (it re-resolves the episode's
                    script and remounts the shell) — management lives in the
                    workspace's Episodes module, not here. */}
                {embedded && epSwitchOpen && (
                  <div className="mh-ep-switch-menu" data-testid="editor-ep-switch-menu">
                    {(episodesForSwitcher ?? []).map((ep) => (
                      <button
                        key={ep.episode_id}
                        type="button"
                        data-testid={`editor-ep-switch-${ep.episode_id}`}
                        className={`mh-ep-switch-item${
                          sameEpisodeId(ep.episode_id, currentEpisodeId) ? ' current' : ''
                        }`}
                        onClick={() => {
                          setEpSwitchOpen(false);
                          if (!sameEpisodeId(ep.episode_id, currentEpisodeId)) {
                            onSwitchEpisode?.(ep.episode_id);
                          }
                        }}
                      >
                        {ep.title}
                      </button>
                    ))}
                  </div>
                )}
                {!embedded && episodePanelOpen && projectId && (
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
            <div className="mh-rail-scroll">
              <RailEntities
                characters={railCharacters}
                locations={railLocations}
                onSelect={handleSelectScene}
              />
              <section className="mh-rail-section" aria-label={t('editor.scenesLabel')}>
                <div className="mh-rail-section-head">
                  <span className="mh-rail-section-label">{t('editor.scenesLabel')}</span>
                  {scenes.length > 0 && (
                    <span className="mh-rail-count-badge">{scenes.length}</span>
                  )}
                </div>
                <SceneRail
                  scenes={scenes}
                  activeSceneId={state.activeSceneId}
                  onSelect={handleSelectScene}
                />
              </section>
            </div>
          </>
        )}
      </nav>

      {/* ===== CENTER PAPER COLUMN ===== */}
      <main className="mh-center-col" aria-label={t('editor.paperColumn')}>
        <div className="mh-center-topbar">
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
          <div className="mh-topbar-right">
            {COLLAB_ENABLED && <PresenceAvatars users={onlineUsers} />}
            <SaveIndicator state={aggregateState} queued={offlineCount} />
            <button
              type="button"
              className="mh-icon-btn"
              aria-label={t('editor.toggleTheme')}
              onClick={toggleTheme}
            >
              {state.theme === 'dark' ? '☾' : '☼'}
            </button>
          </div>
        </div>

        <div className="mh-page-frame">
          {diffCommit ? (
            <VersionDiff
              scriptId={scriptId}
              commit={diffCommit}
              scenes={scenes}
              onBack={() => setDiffCommit(null)}
            />
          ) : railView === 'nodes' ? (
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
            <BeatsView scenes={scenes} scriptId={scriptId} onOpenScene={handleOpenScene} />
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
              <div className="mh-sheet">
                <div className="mh-sheet-inner">
                  {state.mode === 'outline' ? (
                    <OutlineView
                      scenes={scenes}
                      chapters={chapters}
                      onOpenScene={handleOpenScene}
                      onReload={reloadAll}
                    />
                  ) : (
                    <>
                      {scenes.map((s, i) => {
                        const mounted =
                          !windowed || (i >= sceneWindow.start && i <= sceneWindow.end);
                        if (!mounted) {
                          // A windowed-out scene is still a valid drop target so a
                          // drag can cross the mounted window: dropping on the
                          // placeholder lands the dragged scene BEFORE it. Keyboard
                          // reorder (Alt+Arrow) covers the a11y path, so the
                          // decorative placeholder stays aria-hidden.
                          return (
                            <div
                              key={s.id}
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
                          );
                        }
                        return (
                          <SceneBlock
                            key={s.id}
                            scene={s}
                            index={i}
                            format={state.format}
                            mentionCandidates={mentionCandidates}
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
                            onRegisterRemoteApply={
                              COLLAB_ENABLED ? registerRemoteApply : undefined
                            }
                            remoteStale={COLLAB_ENABLED && droppedScenes.has(String(s.id))}
                            onRemoteStaleHandled={
                              COLLAB_ENABLED ? handleRemoteStaleHandled : undefined
                            }
                          />
                        );
                      })}
                      {orphanChapters.map((ch) => (
                        <ChapterFallback
                          key={ch.id}
                          chapter={ch}
                          converting={!!converting[ch.id]}
                          onConvert={handleConvertChapter}
                        />
                      ))}
                    </>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="mh-keyboard-hint">
          <kbd>Tab</kbd> {t('editor.hintTab')} <span className="sep">·</span>
          <kbd>Enter</kbd> {t('editor.hintEnter')} <span className="sep">·</span>
          <kbd>@</kbd> {t('editor.hintMention')}
        </div>
      </main>

      {/* ===== RIGHT PANEL ===== */}
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
              scriptId={scriptId}
              onCompareCommit={handleCompareCommit}
              onRolledBack={handleRolledBack}
            />
          </>
        )}
      </aside>

      {showImportModal && projectId && (
        <ImportScriptModal
          projectId={projectId}
          onClose={() => setShowImportModal(false)}
        />
      )}
    </div>
  );
}
