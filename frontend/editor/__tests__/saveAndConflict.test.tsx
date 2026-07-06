import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// EditorShell integration needs a scene source and a forced-conflict sync.
const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listEpisodes: vi.fn(),
  createScene: vi.fn(),
  applyOps: vi.fn(),
  updateSceneMeta: vi.fn().mockResolvedValue({}),
  newElementId: () => 'el_seed',
}));
vi.mock('../sceneService', () => svc);

const resolveSpy = vi.hoisted(() => vi.fn());
vi.mock('../useSceneSync', () => ({
  useSceneSync: (scene: { elements: unknown[]; content_version: number }) => ({
    elements: scene.elements,
    version: scene.content_version,
    saveState: 'conflict' as const,
    conflict: { mine: [], theirs: [] },
    dispatchOps: () => {},
    resolveConflict: resolveSpy,
    flush: async () => {},
  }),
}));

import { SaveIndicator, aggregateSaveState } from '../components/SaveIndicator';
import { ConflictBar } from '../components/ConflictBar';
import { EditorShell } from '../components/EditorShell';
import type { SaveState } from '../useSceneSync';
import type { SceneDoc } from '../types';

afterEach(() => {
  cleanup();
  svc.listScenes.mockReset();
  resolveSpy.mockReset();
});

describe('SaveIndicator', () => {
  const cases: { state: SaveState; label: string }[] = [
    { state: 'saved', label: 'editor.saved' },
    { state: 'saving', label: 'editor.saving' },
    { state: 'retrying', label: 'editor.retrying' },
    { state: 'offline', label: 'editor.offlineQueued' },
    { state: 'conflict', label: 'editor.conflictShort' },
  ];

  it.each(cases)('renders $state with its label, state marker and polite live region', ({ state, label }) => {
    render(<SaveIndicator state={state} />);
    const el = screen.getByTestId('save-indicator');
    expect(el).toHaveTextContent(label);
    expect(el).toHaveAttribute('data-state', state);
    expect(el).toHaveAttribute('aria-live', 'polite');
  });

  it('shows the queued count only when offline', () => {
    const { rerender } = render(<SaveIndicator state="offline" queued={3} />);
    expect(screen.getByTestId('save-indicator')).toHaveTextContent('3');
    rerender(<SaveIndicator state="saving" queued={3} />);
    expect(screen.getByTestId('save-indicator')).not.toHaveTextContent('3');
  });
});

describe('aggregateSaveState', () => {
  it('prioritises conflict over everything, then offline, retrying, saving, saved', () => {
    expect(aggregateSaveState(['saved', 'conflict'])).toBe('conflict');
    expect(aggregateSaveState(['saving', 'offline'])).toBe('offline');
    expect(aggregateSaveState(['saved', 'retrying', 'saving'])).toBe('retrying');
    expect(aggregateSaveState(['saving', 'saved'])).toBe('saving');
    expect(aggregateSaveState(['saved', 'saved'])).toBe('saved');
    expect(aggregateSaveState([])).toBe('saved');
  });
});

describe('ConflictBar', () => {
  it('offers Keep mine / Take theirs and routes each choice', () => {
    const onKeepMine = vi.fn();
    const onTakeTheirs = vi.fn();
    render(<ConflictBar onKeepMine={onKeepMine} onTakeTheirs={onTakeTheirs} />);
    expect(screen.getByText('editor.conflictMessage')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'editor.keepMine' }));
    expect(onKeepMine).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'editor.takeTheirs' }));
    expect(onTakeTheirs).toHaveBeenCalledTimes(1);
  });
});

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: 's1',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_1', type: 'action', text: 'A' }],
  ...over,
});

describe('EditorShell conflict wiring', () => {
  it('surfaces the conflict bar + conflict save state and routes Keep mine to the scene', async () => {
    svc.listScenes.mockResolvedValue([scene({})]);
    render(<EditorShell scriptId="1" />);

    await waitFor(() => expect(screen.getByTestId('conflict-bar')).toBeInTheDocument());
    expect(screen.getByTestId('save-indicator')).toHaveAttribute('data-state', 'conflict');

    fireEvent.click(screen.getByRole('button', { name: 'editor.keepMine' }));
    expect(resolveSpy).toHaveBeenCalledWith('mine');
  });
});
