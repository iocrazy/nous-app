import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

// i18n: echo the key so assertions are language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  moveScene: vi.fn().mockResolvedValue({}),
  convertToScenes: vi.fn().mockResolvedValue('task-1'),
  createScene: vi.fn(),
  applyOps: vi.fn(),
  updateSceneMeta: vi.fn().mockResolvedValue({}),
  newElementId: () => 'el_new0001',
}));
vi.mock('../sceneService', () => svc);

const scriptSvc = vi.hoisted(() => ({ fetchScriptProject: vi.fn() }));
vi.mock('../../services/scriptService', () => scriptSvc);

const toast = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast: toast.addToast }) }));

import { EditorShell } from '../components/EditorShell';

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: 'scene1',
  script_id: 's1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_1', type: 'action', text: 'One.' }],
  ...over,
});

const threeScenes: SceneDoc[] = [
  scene({ id: 'scene1', sort_order: 0 }),
  scene({ id: 'scene2', sort_order: 1 }),
  scene({ id: 'scene3', sort_order: 2 }),
];

const chapter = (over: Partial<ScriptChapter>): ScriptChapter => ({
  id: 'ch1',
  script_id: 's1',
  position_x: 0,
  position_y: 0,
  data_json: {},
  sort_order: 0,
  created_at: '',
  updated_at: '',
  title: 'Chapter One',
  content: 'Some legacy prose.',
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  svc.moveScene.mockResolvedValue({});
  svc.convertToScenes.mockResolvedValue('task-1');
});

const fakeDataTransfer = () => ({ setData: vi.fn(), effectAllowed: '' });

describe('scene reorder (drag + keyboard)', () => {
  it('drops scene 1 after scene 3 → moveScene(after_scene_id=scene3)', async () => {
    svc.listScenes.mockResolvedValue(threeScenes);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [] });
    const { container } = render(<EditorShell scriptId="s1" />);
    await waitFor(() => expect(screen.getAllByTestId('scene-block')).toHaveLength(3));

    const handle = screen.getAllByLabelText('editor.dragScene')[0];
    const target = container.querySelector('[data-scene-id="scene3"]') as HTMLElement;

    fireEvent.dragStart(handle, { dataTransfer: fakeDataTransfer() });
    fireEvent.dragOver(target, { clientY: 0 });
    fireEvent.drop(target, { clientY: 0 });

    await waitFor(() =>
      expect(svc.moveScene).toHaveBeenCalledWith('scene1', { after_scene_id: 'scene3' }),
    );
  });

  it('Alt+ArrowDown on the handle moves the scene after its next sibling', async () => {
    svc.listScenes.mockResolvedValue(threeScenes);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [] });
    render(<EditorShell scriptId="s1" />);
    await waitFor(() => expect(screen.getAllByTestId('scene-block')).toHaveLength(3));

    const handle = screen.getAllByLabelText('editor.dragScene')[0];
    fireEvent.keyDown(handle, { key: 'ArrowDown', altKey: true });

    await waitFor(() =>
      expect(svc.moveScene).toHaveBeenCalledWith('scene1', { after_scene_id: 'scene2' }),
    );
  });
});

describe('legacy chapter fallback + convert', () => {
  it('renders a prose card for an unclaimed chapter and converts on click', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: 'scene1', chapter_id: null })]);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [chapter({})] });
    render(<EditorShell scriptId="s1" />);

    const card = await screen.findByTestId('chapter-fallback');
    expect(within(card).getByText('Chapter One')).toBeInTheDocument();
    expect(within(card).getByText('Some legacy prose.')).toBeInTheDocument();

    fireEvent.click(within(card).getByText('editor.convertToScenes'));
    await waitFor(() => expect(svc.convertToScenes).toHaveBeenCalledWith('s1', 'ch1'));
  });

  it('does NOT render a fallback for a chapter that already has scenes', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: 'scene1', chapter_id: 'ch1' })]);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [chapter({ id: 'ch1' })] });
    render(<EditorShell scriptId="s1" />);

    await waitFor(() => expect(screen.getByTestId('scene-block')).toBeInTheDocument());
    expect(screen.queryByTestId('chapter-fallback')).toBeNull();
  });
});

describe('windowed rendering', () => {
  it('mounts at most 11 scene blocks for a 100-scene script', async () => {
    const many: SceneDoc[] = Array.from({ length: 100 }, (_, i) =>
      scene({ id: `scene${i}`, sort_order: i }),
    );
    svc.listScenes.mockResolvedValue(many);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [] });
    render(<EditorShell scriptId="s1" />);

    await waitFor(() => expect(screen.getAllByTestId('scene-block').length).toBeGreaterThan(0));
    expect(screen.getAllByTestId('scene-block').length).toBeLessThanOrEqual(11);
    // The rest are cheap placeholders, keeping the scrollbar geometry.
    expect(screen.getAllByTestId('scene-placeholder').length).toBeGreaterThan(0);
  });

  it('drops a dragged scene onto a windowed-out placeholder → moveScene before it', async () => {
    const many: SceneDoc[] = Array.from({ length: 100 }, (_, i) =>
      scene({ id: `scene${i}`, sort_order: i }),
    );
    svc.listScenes.mockResolvedValue(many);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [] });
    render(<EditorShell scriptId="s1" />);
    await waitFor(() => expect(screen.getAllByTestId('scene-block').length).toBeGreaterThan(0));

    const handle = screen.getAllByLabelText('editor.dragScene')[0]; // scene0's grip
    const placeholder = screen.getAllByTestId('scene-placeholder')[0]; // a far-off scene
    const targetId = placeholder.getAttribute('data-scene-id')!;

    fireEvent.dragStart(handle, { dataTransfer: fakeDataTransfer() });
    fireEvent.dragOver(placeholder);
    fireEvent.drop(placeholder);

    await waitFor(() =>
      expect(svc.moveScene).toHaveBeenCalledWith('scene0', { before_scene_id: targetId }),
    );
  });
});

describe('a11y — Esc leaves edit mode', () => {
  it('sets data-editing=false on the shell root when Esc exits a line', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: 'scene1' })]);
    scriptSvc.fetchScriptProject.mockResolvedValue({ chapters: [] });
    const { container } = render(<EditorShell scriptId="s1" />);
    await waitFor(() => expect(screen.getByTestId('scene-block')).toBeInTheDocument());

    const root = container.querySelector('.mh-editor-shell') as HTMLElement;
    const line = container.querySelector('[data-el-id="el_1"]') as HTMLElement;

    fireEvent.focus(line);
    expect(root).toHaveAttribute('data-editing', 'true');

    fireEvent.keyDown(line, { key: 'Escape' });
    expect(root).toHaveAttribute('data-editing', 'false');
  });
});
