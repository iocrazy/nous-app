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
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { listScenes } from '../sceneService';
import type { SceneDoc } from '../types';
import { useEditorState, type EditorMode } from '../useEditorState';
import { EDITOR_SHELL_STYLES } from './editorShellStyles';

type LoadState = 'loading' | 'ready' | 'error';

const DOC_MODES: EditorMode[] = ['script', 'outline', 'cover'];

export function EditorShell({ scriptId }: { scriptId: string }) {
  const { t } = useTranslation();
  const { state, setMode, toggleTheme, setActiveScene } = useEditorState();
  const [scenes, setScenes] = useState<SceneDoc[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoadState('loading');
    listScenes(scriptId)
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows].sort((a, b) => a.sort_order - b.sort_order);
        setScenes(sorted);
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

  const tabLabel: Record<EditorMode, string> = {
    script: t('editor.tabScript'),
    outline: t('editor.tabOutline'),
    cover: t('editor.tabCover'),
  };

  return (
    <div className="mh-editor-shell" data-theme={state.theme} data-editor-shell>
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
            <div className="mh-scene-list">
              {scenes.map((s, i) => {
                const ext = (s.heading_int_ext ?? '').toUpperCase() === 'EXT';
                return (
                  <button
                    type="button"
                    key={s.id}
                    className={`mh-scene-row${state.activeSceneId === s.id ? ' active' : ''}`}
                    onClick={() => setActiveScene(s.id)}
                  >
                    <span className="mh-scene-num-chip">{i + 1}</span>
                    <span className="mh-scene-meta-text">
                      <span className="mh-scene-row-head">
                        <span className={`mh-ie-badge ${ext ? 'ext' : 'int'}`}>
                          {ext ? 'EXT' : 'INT'}
                        </span>
                        <span className="mh-scene-title">
                          {s.location_text || t('editor.untitledScene')}
                        </span>
                      </span>
                      <span className="mh-scene-slug">
                        {s.elements[0]?.text || t('editor.emptyScene')}
                      </span>
                    </span>
                  </button>
                );
              })}
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
              <div className="mh-sheet">
                <div className="mh-sheet-inner">
                  {state.mode === 'outline' ? (
                    <div className="mh-doc-outline" data-testid="outline-placeholder">
                      <div className="mh-doc-title">{t('editor.episodeOne')}</div>
                      <p className="mh-doc-p">{t('editor.outlinePlaceholder')}</p>
                    </div>
                  ) : (
                    scenes.map((s, i) => {
                      const ext = (s.heading_int_ext ?? '').toUpperCase() === 'EXT';
                      return (
                        <div className="mh-scene-block" key={s.id}>
                          <div className="mh-scene-headrow">
                            <span className="mh-scene-num-badge">{i + 1}</span>
                            <span className={`mh-scene-chip ${ext ? 'ie-ext' : 'ie-int'}`}>
                              {ext ? 'EXT' : 'INT'}
                            </span>
                            {s.location_text && (
                              <span className="mh-scene-chip loc">
                                {s.location_text.toUpperCase()}
                              </span>
                            )}
                            {s.time_of_day && (
                              <span className="mh-scene-chip loc">
                                {s.time_of_day.toUpperCase()}
                              </span>
                            )}
                          </div>
                          {s.elements.length === 0 ? (
                            <div className="mh-el-line mh-placeholder-line">
                              {t('editor.emptyScene')}
                            </div>
                          ) : (
                            s.elements.map((el) => (
                              <div className="mh-el-line" key={el.id}>
                                {el.text}
                              </div>
                            ))
                          )}
                        </div>
                      );
                    })
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
            <div className="mh-panel-body">
              <div className="mh-panel-hint">{t('editor.statisticsSoon')}</div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
