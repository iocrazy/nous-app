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
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createScene, listScenes } from '../sceneService';
import type { CursorState } from '../editorMachine';
import type { ElementType, SceneDoc } from '../types';
import { useEditorState, type EditorFormat, type EditorMode } from '../useEditorState';
import { EDITOR_SHELL_STYLES } from './editorShellStyles';
import { SceneBlock, type TypeCommand } from './SceneBlock';
import { SceneRail } from './SceneRail';
import { ElementToolbar } from './ElementToolbar';
import { WritingPanel } from './WritingPanel';

type LoadState = 'loading' | 'ready' | 'error';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

export function EditorShell({ scriptId }: { scriptId: string }) {
  const { t } = useTranslation();
  const { state, setMode, setFormat, toggleTheme, setActiveScene, setCursor, setNextInsertType } =
    useEditorState();
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [typeCommand, setTypeCommand] = useState<TypeCommand | null>(null);

  const shellRef = useRef<HTMLDivElement | null>(null);
  const cursorRef = useRef<CursorState | null>(null);
  const nonceRef = useRef(0);
  cursorRef.current = state.cursor;

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

  const handleFormatChange = useCallback(
    (format: EditorFormat) => setFormat(format),
    [setFormat],
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
            <div className="mh-rail-section-label">{t('editor.scenesLabel')}</div>
            <SceneRail
              scenes={scenes}
              activeSceneId={state.activeSceneId}
              onSelect={handleSelectScene}
            />
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
            <div className="mh-save-indicator" data-testid="save-indicator-slot">
              <span className="mh-save-dot" aria-hidden="true" />
              {t('editor.saved')}
            </div>
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
          ) : (
            <div className="mh-sheet-scroll">
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
                        onFocusElement={handleFocusElement}
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
