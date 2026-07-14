import { render, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, ScriptElement, SceneDoc } from '../types';

// i18n: echo the key so assertions stay language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// sceneService: EditorShell (listScenes) + SceneBlock (newElementId /
// updateSceneMeta) both resolve through this file-level mock.
const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listEpisodes: vi.fn(),
  createScene: vi.fn(),
  updateSceneMeta: vi.fn().mockResolvedValue({}),
  newElementId: () => 'el_ffffffff',
}));
vi.mock('../sceneService', () => svc);

// EditorShell (Task 10) also reads chapters + shows convert toasts.
vi.mock('../../services/scriptService', () => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

// useSceneSync: stateful mock so optimistic edits render; no network.
vi.mock('../useSceneSync', async () => {
  const React = await import('react');
  return {
    useSceneSync: (scene: SceneDoc) => {
      const [elements, setElements] = React.useState(scene.elements);
      return {
        elements,
        version: scene.content_version,
        saveState: 'saved' as const,
        conflict: null,
        dispatchOps: (_ops: ElementOp[], optimistic: ScriptElement[]) => setElements(optimistic),
        resolveConflict: () => {},
        flush: async () => {},
      };
    },
  };
});

import { EditorShell } from '../components/EditorShell';
import { readStoredFormat, persistFormat } from '../formatStorage';

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_00000001', type: 'action', text: 'One pool of light.' }],
  ...over,
});

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('per-script format persistence', () => {
  it('round-trips through localStorage keyed by script id', () => {
    expect(readStoredFormat('s1')).toBeNull();
    persistFormat('s1', 'asian');
    expect(readStoredFormat('s1')).toBe('asian');
    // Isolated per script id.
    expect(readStoredFormat('s2')).toBeNull();
    persistFormat('s1', 'hollywood');
    expect(readStoredFormat('s1')).toBe('hollywood');
  });

  it('ignores an unrecognised stored value', () => {
    localStorage.setItem('editor.format.s3', 'nonsense');
    expect(readStoredFormat('s3')).toBeNull();
  });

  it('restores the persisted asian format when the shell remounts', async () => {
    // The Asian typeset renders the △ action prefix (ASIAN_PREFIX) through the
    // TipTap NodeView — a proxy for "the asian format is active".
    persistFormat('7', 'asian');
    svc.listScenes.mockResolvedValue([scene({ id: '111' })]);

    const first = render(<EditorShell scriptId="7" />);
    await waitFor(() => expect(first.container.querySelector('.as-prefix')).toBeInTheDocument());
    first.unmount();

    // Remount: the asian engine is active again from persisted storage.
    const second = render(<EditorShell scriptId="7" />);
    await waitFor(() => expect(second.container.querySelector('.as-prefix')).toBeInTheDocument());
    expect(second.container.querySelector('.hw-action')).toBeNull();
  });
});
