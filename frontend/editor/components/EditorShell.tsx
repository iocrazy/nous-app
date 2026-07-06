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
} from '../sceneService';
import { fetchScriptProject } from '../../services/scriptService';
import { useToast } from '../../components/Toast';
import type { CursorState } from '../editorMachine';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';
import { useEditorState, type EditorFormat, type EditorMode } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { persistFormat, readStoredFormat } from '../formatStorage';
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
import { NodesView } from '../nodes/NodesView';
import { StoryboardView } from '../storyboard/StoryboardView';
import { OutlineView } from './OutlineView';
import { EpisodePanel } from './EpisodePanel';
import { RailEntities } from './RailEntities';
import { deriveRailCharacters, deriveRailLocations } from '../railDerive';
import { ElementToolbar } from './ElementToolbar';
import { WritingPanel, deriveStatistics } from './WritingPanel';
import { SaveIndicator, aggregateSaveState } from './SaveIndicator';
import { ConflictBar } from './ConflictBar';
import { ColdStart } from './EmptyStates';
import { ChapterFallback } from './ChapterFallback';

type LoadState = 'loading' | 'ready' | 'error';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

export function EditorShell({ scriptId }: { scriptId: string }) {
  const { t } = useTranslation();
  // Restore the per-script layout engine synchronously so the first paint uses
  // it (no engine flash on remount).
  const initialFormat = useMemo(() => readStoredFormat(scriptId) ?? undefined, [scriptId]);
  const { state, setMode, setFormat, toggleTheme, setActiveScene, setCursor, setNextInsertType } =
    useEditorState({ initialFormat });
  const { addToast } = useToast();
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [chapters, setChapters] = useState<ScriptChapter[]>([]);
  // Episode dimension (Task 5): the owning project, this script's episode, and
  // the project's episode list power the rail Ep selector + management panel.
  const [projectId, setProjectId] = useState<string | null>(null);
  const [currentEpisodeId, setCurrentEpisodeId] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [episodePanelOpen, setEpisodePanelOpen] = useState(false);
  // Live (optimistic) elements lifted from each SceneBlock so Statistics + rail
  // entities reflect in-flight edits, not just the last loaded snapshot (Task 6 ⑥).
  const [liveElements, setLiveElements] = useState<Record<string, ScriptElement[]>>({});
  const [converting, setConverting] = useState<Record<string, boolean>>({});
  const [loadState, setLoadState] = useState<LoadState>('loading');
  // Central-column view: the script sheet or the scene-node projection. The top
  // Script/Outline/Cover tabs are a separate axis and stay put (spec §3.1).
  const [railView, setRailView] = useState<RailView>('script');
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

  // Script-wide CAST names feed the @-mention / character-cue picker.
  const mentionCandidates = useMemo(() => deriveStatistics(statsScenes).cast, [statsScenes]);

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
      className="mh-editor-shell"
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
            <RailModules activeView={railView} onSelect={setRailView} />
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
                onClick={() => setMode(m)}
              >
                {tabLabel[m]}
              </button>
            ))}
          </div>
          <div className="mh-topbar-right">
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
          {railView === 'nodes' ? (
            <NodesView
              scenes={scenes}
              chapters={chapters}
              onOpenScene={handleOpenScene}
              scriptId={scriptId}
              onReload={reloadAll}
            />
          ) : railView === 'storyboard' ? (
            <StoryboardView scenes={scenes} scriptId={scriptId} />
          ) : state.mode === 'cover' ? (
            <div className="mh-sheet-scroll">
              <div className="mh-cover-card" data-testid="cover-placeholder">
                {t('editor.coverPlaceholder')}
              </div>
            </div>
          ) : showColdStart ? (
            <div className="mh-sheet-scroll">
              <ColdStart onCreateStory={handleCreateStory} />
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
            />
          </>
        )}
      </aside>
    </div>
  );
}
