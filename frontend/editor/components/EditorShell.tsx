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
import { applyOps, createScene, listScenes, newElementId } from '../sceneService';
import type { CursorState } from '../editorMachine';
import type { ElementOp, ElementType, SceneDoc } from '../types';
import { useEditorState, type EditorFormat, type EditorMode } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { persistFormat, readStoredFormat } from '../formatStorage';
import { EDITOR_SHELL_STYLES } from './editorShellStyles';
import { SceneBlock, type TypeCommand, type SceneSyncStatus } from './SceneBlock';
import { SceneRail } from './SceneRail';
import { RailModules } from './RailModules';
import { RailEntities } from './RailEntities';
import { deriveRailCharacters, deriveRailLocations } from '../railDerive';
import { ElementToolbar } from './ElementToolbar';
import { WritingPanel, deriveStatistics } from './WritingPanel';
import { SaveIndicator, aggregateSaveState } from './SaveIndicator';
import { ConflictBar } from './ConflictBar';
import { ColdStart } from './EmptyStates';

type LoadState = 'loading' | 'ready' | 'error';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

export function EditorShell({ scriptId }: { scriptId: string }) {
  const { t } = useTranslation();
  // Restore the per-script layout engine synchronously so the first paint uses
  // it (no engine flash on remount).
  const initialFormat = useMemo(() => readStoredFormat(scriptId) ?? undefined, [scriptId]);
  const { state, setMode, setFormat, toggleTheme, setActiveScene, setCursor, setNextInsertType } =
    useEditorState({ initialFormat });
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [typeCommand, setTypeCommand] = useState<TypeCommand | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, SceneSyncStatus>>({});
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);

  const shellRef = useRef<HTMLDivElement | null>(null);
  const cursorRef = useRef<CursorState | null>(null);
  const nonceRef = useRef(0);
  cursorRef.current = state.cursor;

  const handleSyncStateChange = useCallback((sceneId: string, status: SceneSyncStatus) => {
    setSyncStates((prev) => ({ ...prev, [sceneId]: status }));
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
    return () => {
      cancelled = true;
    };
  }, [scriptId]);

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

  const handleFocusElement = useCallback((cursor: CursorState) => setCursor(cursor), [setCursor]);

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

  // Script-wide CAST names feed the @-mention / character-cue picker.
  const mentionCandidates = useMemo(() => deriveStatistics(scenes).cast, [scenes]);

  // Rail entity sections (laper info architecture) — Characters + Locations.
  const railCharacters = useMemo(() => deriveRailCharacters(scenes), [scenes]);
  const railLocations = useMemo(() => deriveRailLocations(scenes), [scenes]);

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

  const showColdStart = state.mode === 'script' && loadState === 'ready' && scenes.length === 0;

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
    <div className="mh-editor-shell" data-theme={state.theme} data-editor-shell ref={shellRef}>
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
              <div className="mh-ep-selector">
                <div className="mh-ep-name">{t('editor.episodeOne')}</div>
                <div className="mh-ep-sub">
                  {t('editor.sceneCount', { count: scenes.length })}
                </div>
              </div>
            </div>
            <RailModules />
            <div className="mh-rail-scroll">
              <RailEntities
                characters={railCharacters}
                locations={railLocations}
                onSelect={handleSelectScene}
              />
              <div className="mh-rail-section-label">{t('editor.scenesLabel')}</div>
              <SceneRail
                scenes={scenes}
                activeSceneId={state.activeSceneId}
                onSelect={handleSelectScene}
              />
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
          {state.mode === 'cover' ? (
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
            <div className="mh-sheet-scroll">
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
                    <div className="mh-doc-outline" data-testid="outline-placeholder">
                      <div className="mh-doc-title">{t('editor.episodeOne')}</div>
                      <p className="mh-doc-p">{t('editor.outlinePlaceholder')}</p>
                    </div>
                  ) : (
                    scenes.map((s, i) => (
                      <SceneBlock
                        key={s.id}
                        scene={s}
                        index={i}
                        format={state.format}
                        mentionCandidates={mentionCandidates}
                        onFocusElement={handleFocusElement}
                        onSyncStateChange={handleSyncStateChange}
                        typeCommand={typeCommand ?? undefined}
                      />
                    ))
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
              scenes={scenes}
              format={state.format}
              onFormatChange={handleFormatChange}
            />
          </>
        )}
      </aside>
    </div>
  );
}
