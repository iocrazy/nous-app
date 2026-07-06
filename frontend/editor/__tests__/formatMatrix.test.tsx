import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HollywoodLayout } from '../render/HollywoodLayout';
import { AsianLayout } from '../render/AsianLayout';
import type { LayoutHandlers } from '../render/HollywoodLayout';
import type { ElementOp, ElementType, ScriptElement, SceneDoc } from '../types';

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

import { SceneBlock } from '../components/SceneBlock';
import { EditorShell } from '../components/EditorShell';
import { readStoredFormat, persistFormat } from '../formatStorage';

const noopHandlers = (): LayoutHandlers => ({
  onInput: vi.fn(),
  onKeyDown: vi.fn(),
  onFocus: vi.fn(),
  onPaste: vi.fn(),
  onCompositionStart: vi.fn(),
  onCompositionEnd: vi.fn(),
});

const ALL_TYPES: ElementType[] = [
  'action',
  'character',
  'dialogue',
  'paren',
  'transition',
  'comment',
  'subtitle',
];

const oneOf = (type: ElementType): ScriptElement[] => [
  { id: 'el_00000001', type, text: 'Sample line' },
];

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

describe('format matrix — every element type × both engines', () => {
  it.each(ALL_TYPES)('Hollywood renders hw-%s for a %s element', (type) => {
    const { container } = render(
      <HollywoodLayout elements={oneOf(type)} focusedElementId={null} handlers={noopHandlers()} />,
    );
    expect(container.querySelector('[data-el-id="el_00000001"]')).toHaveClass(`hw-${type}`);
  });

  it.each(ALL_TYPES)('Asian renders as-%s for a %s element', (type) => {
    const { container } = render(
      <AsianLayout elements={oneOf(type)} focusedElementId={null} handlers={noopHandlers()} />,
    );
    expect(container.querySelector('[data-el-id="el_00000001"]')).toHaveClass(`as-${type}`);
  });

  it('emits the △ action marker only in the Asian engine', () => {
    const hollywood = render(
      <HollywoodLayout
        elements={oneOf('action')}
        focusedElementId={null}
        handlers={noopHandlers()}
      />,
    );
    expect(hollywood.container.querySelector('.as-prefix')).toBeNull();
    cleanup();

    const asian = render(
      <AsianLayout elements={oneOf('action')} focusedElementId={null} handlers={noopHandlers()} />,
    );
    expect(asian.container.querySelector('.as-prefix')?.textContent).toBe('△');
  });
});

describe('switching format re-renders the scene through the other engine', () => {
  it('shows the △ marker once the format flips to asian', () => {
    const s = scene({});
    const { container, rerender } = render(<SceneBlock scene={s} index={0} format="hollywood" />);
    expect(container.querySelector('.as-prefix')).toBeNull();
    expect(container.querySelector('.hw-action')).toBeInTheDocument();

    rerender(<SceneBlock scene={s} index={0} format="asian" />);
    expect(container.querySelector('.as-prefix')?.textContent).toBe('△');
    expect(container.querySelector('.hw-action')).toBeNull();
  });
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
