/**
 * useEditorState — the shell-level view reducer for the v2 editor (spec v3 §3.1).
 *
 * This owns the *chrome* state that the three-zone shell coordinates: which doc
 * mode is showing (Script/Outline/Cover), which layout engine renders the page
 * (Hollywood/Asian), the light/dark theme, the logical cursor, and the active
 * scene highlight. It deliberately does NOT own scene content or the op queue —
 * that is useSceneSync's job, one instance per scene.
 *
 * Theme is the only slice that persists: the initial value is read from
 * localStorage('editor.theme') so a writer's night-draft preference survives a
 * reload, and toggleTheme writes it back. Everything else is session state.
 */
import { useMemo, useReducer } from 'react';
import type { CursorState } from './editorMachine';
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
  | { type: 'setCursor'; cursor: CursorState | null }
  | { type: 'setActiveScene'; sceneId: string | null }
  | { type: 'setNextInsertType'; elementType: ElementType };

const THEME_STORAGE_KEY = 'editor.theme';

function readStoredTheme(): EditorTheme {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function persistTheme(theme: EditorTheme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (err) {
    // Non-fatal: a writer in private mode just loses theme memory, not the editor.
    console.error('[useEditorState] failed to persist theme', err);
  }
}

function reducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'setMode':
      return { ...state, mode: action.mode };
    case 'setFormat':
      return { ...state, format: action.format };
    case 'toggleTheme': {
      const theme: EditorTheme = state.theme === 'light' ? 'dark' : 'light';
      persistTheme(theme);
      return { ...state, theme };
    }
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
    theme: readStoredTheme(),
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
      setCursor: (cursor: CursorState | null) => dispatch({ type: 'setCursor', cursor }),
      setActiveScene: (sceneId: string | null) => dispatch({ type: 'setActiveScene', sceneId }),
      setNextInsertType: (elementType: ElementType) =>
        dispatch({ type: 'setNextInsertType', elementType }),
    }),
    [],
  );
  return { state, ...actions };
}
