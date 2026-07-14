/**
 * useEditorState — the shell-level view reducer for the v2 editor (spec v3 §3.1).
 *
 * This owns the *chrome* state that the three-zone shell coordinates: which doc
 * mode is showing (Script/Outline/Cover), which layout engine renders the page
 * (Hollywood/Asian), the light/dark theme, the logical cursor, and the active
 * scene highlight. It deliberately does NOT own scene content or the op queue —
 * that is useSceneSync's job, one instance per scene.
 *
 * Theme follows the app theme (`<html data-theme>`, owned by ThemeContext):
 * the initial value is read from the document at mount, and EditorShell keeps
 * it in sync via `setTheme` when the app theme changes. The ☾/☼ toggle is a
 * session-only override — it deliberately does NOT persist, so the editor can
 * never come up light inside a dark app again (the old localStorage
 * 'editor.theme' default did exactly that). Everything else is session state.
 */
import { useMemo, useReducer } from 'react';
import type { CursorState } from './types';
import type { ElementType } from './types';

export type EditorMode = 'script' | 'outline' | 'cover';
export type EditorFormat = 'hollywood' | 'asian';
export type EditorTheme = 'light' | 'dark';

export interface EditorState {
  mode: EditorMode;
  format: EditorFormat;
  theme: EditorTheme;
  cursor: CursorState | null;
  activeSceneId: string | null;
  /** Element type the toolbar will apply to the NEXT insert when no line has focus. */
  nextInsertType: ElementType;
}

export type EditorAction =
  | { type: 'setMode'; mode: EditorMode }
  | { type: 'setFormat'; format: EditorFormat }
  | { type: 'toggleTheme' }
  | { type: 'setTheme'; theme: EditorTheme }
  | { type: 'setCursor'; cursor: CursorState | null }
  | { type: 'setActiveScene'; sceneId: string | null }
  | { type: 'setNextInsertType'; elementType: ElementType };

/** The app theme ThemeContext stamped on <html> (also set pre-React by the
 * index.html anti-flash script), so no context wiring is needed here. */
export function readAppTheme(): EditorTheme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function reducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'setMode':
      return { ...state, mode: action.mode };
    case 'setFormat':
      return { ...state, format: action.format };
    case 'toggleTheme':
      return { ...state, theme: state.theme === 'light' ? 'dark' : 'light' };
    case 'setTheme':
      if (state.theme === action.theme) return state;
      return { ...state, theme: action.theme };
    case 'setCursor':
      return { ...state, cursor: action.cursor };
    case 'setActiveScene':
      // Bail on no-op: the IntersectionObserver re-reports the same top scene
      // on every intersection tick; returning the same state object lets React
      // skip the re-render entirely.
      if (state.activeSceneId === action.sceneId) return state;
      return { ...state, activeSceneId: action.sceneId };
    case 'setNextInsertType':
      return { ...state, nextInsertType: action.elementType };
    default:
      return state;
  }
}

function init(initialFormat: EditorFormat): EditorState {
  return {
    mode: 'script',
    format: initialFormat,
    theme: readAppTheme(),
    cursor: null,
    activeSceneId: null,
    nextInsertType: 'action',
  };
}

export interface EditorStateApi {
  state: EditorState;
  setMode: (mode: EditorMode) => void;
  setFormat: (format: EditorFormat) => void;
  toggleTheme: () => void;
  setTheme: (theme: EditorTheme) => void;
  setCursor: (cursor: CursorState | null) => void;
  setActiveScene: (sceneId: string | null) => void;
  setNextInsertType: (elementType: ElementType) => void;
}

export interface UseEditorStateOptions {
  /** Initial layout engine (e.g. restored from per-script persistence). */
  initialFormat?: EditorFormat;
}

export function useEditorState(options?: UseEditorStateOptions): EditorStateApi {
  const [state, dispatch] = useReducer(reducer, options?.initialFormat ?? 'hollywood', init);
  // Stable action identities: effects list these in their deps (e.g. the
  // IntersectionObserver auto-highlight), and a fresh closure per render would
  // tear the observer down every frame — a sustained re-render loop in real
  // browsers that jsdom (no IntersectionObserver) can never catch.
  const actions = useMemo(
    () => ({
      setMode: (mode: EditorMode) => dispatch({ type: 'setMode', mode }),
      setFormat: (format: EditorFormat) => dispatch({ type: 'setFormat', format }),
      toggleTheme: () => dispatch({ type: 'toggleTheme' }),
      setTheme: (theme: EditorTheme) => dispatch({ type: 'setTheme', theme }),
      setCursor: (cursor: CursorState | null) => dispatch({ type: 'setCursor', cursor }),
      setActiveScene: (sceneId: string | null) => dispatch({ type: 'setActiveScene', sceneId }),
      setNextInsertType: (elementType: ElementType) =>
        dispatch({ type: 'setNextInsertType', elementType }),
    }),
    [],
  );
  return { state, ...actions };
}
