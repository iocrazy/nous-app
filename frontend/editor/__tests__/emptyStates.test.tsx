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
  it('cold-start offers Create Story and never an Import affordance', () => {
    const onCreateStory = vi.fn();
    render(<ColdStart onCreateStory={onCreateStory} />);

    const create = screen.getByRole('button', { name: 'editor.createStory' });
    fireEvent.click(create);
    expect(onCreateStory).toHaveBeenCalledTimes(1);

    // Import Script must not appear anywhere on the cold-start screen.
    expect(screen.queryByText(/import/i)).toBeNull();
  });

  it('empty-scene hint nudges Tab / Character', () => {
    render(<EmptySceneHint />);
    expect(screen.getByText('editor.emptyScene')).toBeInTheDocument();
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
});
