import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listEpisodes: vi.fn(),
  createScene: vi.fn(),
  applyOps: vi.fn(),
  updateSceneMeta: vi.fn().mockResolvedValue({}),
  newElementId: () => 'el_seed',
}));
vi.mock('../sceneService', () => svc);

// EditorShell (Task 10) also reads chapters + shows convert toasts.
const scriptSvc = vi.hoisted(() => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../services/scriptService', () => scriptSvc);
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

import { ColdStart, EmptySceneHint } from '../components/EmptyStates';
import { EditorShell } from '../components/EditorShell';
import type { SceneDoc } from '../types';

afterEach(() => {
  cleanup();
  svc.listScenes.mockReset();
  svc.createScene.mockReset();
  svc.applyOps.mockReset();
});

describe('EmptyStates components', () => {
  it('cold-start offers Create Story and, when enabled, Import Script', () => {
    const onCreateStory = vi.fn();
    const onImport = vi.fn();
    render(<ColdStart onCreateStory={onCreateStory} onImport={onImport} />);

    fireEvent.click(screen.getByRole('button', { name: 'editor.createStory' }));
    expect(onCreateStory).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'editor.importScript' }));
    expect(onImport).toHaveBeenCalledTimes(1);
  });

  it('cold-start omits Import when no onImport handler is given', () => {
    render(<ColdStart onCreateStory={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'editor.importScript' })).toBeNull();
  });

  it('empty-scene hint nudges Tab / Character', () => {
    render(<EmptySceneHint />);
    expect(screen.getByText('editor.emptyScene')).toBeInTheDocument();
  });

  it('empty-scene hint is a real seed affordance: click and Enter fire onSeed', () => {
    const onSeed = vi.fn();
    render(<EmptySceneHint onSeed={onSeed} />);
    const hint = screen.getByTestId('empty-scene-hint');
    // It is a focusable button, not a dead <div> — this is what unblocks typing
    // into a previously dead-end empty scene.
    expect(hint).toHaveAttribute('role', 'button');
    expect(hint).toHaveAttribute('tabindex', '0');
    fireEvent.click(hint);
    expect(onSeed).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(hint, { key: 'Enter' });
    expect(onSeed).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(hint, { key: 'Tab' });
    expect(onSeed).toHaveBeenCalledTimes(3);
  });
});

const seededScene: SceneDoc = {
  id: 's1',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: null,
  location_text: null,
  time_of_day: null,
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_seed', type: 'action', text: '' }],
};

describe('EditorShell cold start', () => {
  it('shows the cold-start screen when there are no scenes and has no Import', async () => {
    svc.listScenes.mockResolvedValue([]);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('cold-start')).toBeInTheDocument());
    expect(screen.queryByText(/import/i)).toBeNull();
  });

  it('creates a story with a seeded action row and focuses it', async () => {
    svc.listScenes.mockResolvedValueOnce([]).mockResolvedValue([seededScene]);
    svc.createScene.mockResolvedValue({ ...seededScene, elements: [] });
    svc.applyOps.mockResolvedValue({ content_version: 2, elements: seededScene.elements });

    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('cold-start')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'editor.createStory' }));

    await waitFor(() => expect(svc.createScene).toHaveBeenCalled());
    // The seeded action row renders and receives focus (Tab-ready).
    await waitFor(() =>
      expect(document.activeElement?.getAttribute('data-el-id')).toBe('el_seed'),
    );
  });

  it('legacy script (chapters, no scenes) shows chapter cards with Convert, NOT cold start', async () => {
    svc.listScenes.mockResolvedValue([]);
    scriptSvc.fetchScriptProject.mockResolvedValueOnce({
      chapters: [{ id: 'ch1', title: 'Chapter One', content: 'Old prose to convert.' }],
    });
    render(<EditorShell scriptId="1" />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'editor.convertToScenes' })).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('cold-start')).toBeNull();
    expect(screen.getByText('Old prose to convert.')).toBeInTheDocument();
  });

  it('EMPTY legacy chapter offers Start Writing → creates a typeable scene (no LLM)', async () => {
    svc.listScenes.mockResolvedValueOnce([]).mockResolvedValue([seededScene]);
    scriptSvc.fetchScriptProject.mockResolvedValueOnce({
      chapters: [{ id: 'ch1', title: 'Chapter One', content: '' }], // empty → no prose to split
    });
    svc.createScene.mockResolvedValue({
      ...seededScene,
      chapter_id: 'ch1',
      elements: [],
      content_version: 1,
    });
    svc.applyOps.mockResolvedValue({ content_version: 2, elements: seededScene.elements });

    render(<EditorShell scriptId="1" />);
    // An empty chapter shows "Start Writing", NOT the AI "Convert to Scenes".
    const startBtn = await screen.findByRole('button', { name: 'editor.startWriting' });
    expect(screen.queryByRole('button', { name: 'editor.convertToScenes' })).toBeNull();

    fireEvent.click(startBtn);
    // Client-side: a scene linked to the chapter + a seeded action row — no
    // convert workflow is invoked.
    await waitFor(() =>
      expect(svc.createScene).toHaveBeenCalledWith(
        '1',
        expect.objectContaining({ chapter_id: 'ch1' }),
      ),
    );
    await waitFor(() => expect(svc.applyOps).toHaveBeenCalled());
  });
});
