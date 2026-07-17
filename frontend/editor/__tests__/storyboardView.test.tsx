/**
 * StoryboardView + rail Storyboard slot tests (Phase B P3).
 *
 * Covers: a column per scene with its shots loaded in parallel, Add Shot,
 * inline-confirm delete, Auto Storyboard dispatch + poll-until-shots-grow,
 * within-column reorder anchoring, and the rail slot routing the central pane
 * to the shot board (aria-current following).
 */
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../sceneService';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => {
  class ShotGenerateDisabledError extends Error {
    constructor() {
      super('shot_generate_disabled');
      this.name = 'ShotGenerateDisabledError';
    }
  }
  return {
    listShots: vi.fn(),
    getShot: vi.fn(),
    createShot: vi.fn(),
    updateShot: vi.fn(),
    deleteShot: vi.fn(),
    moveShot: vi.fn(),
    autoStoryboard: vi.fn(),
    generateShot: vi.fn(),
    ShotGenerateDisabledError,
    // EditorShell also pulls these from the module:
    listScenes: vi.fn(),
    listEpisodes: vi.fn(),
    convertToScenes: vi.fn(),
    createScene: vi.fn(),
    applyOps: vi.fn(),
    moveScene: vi.fn(),
    updateSceneMeta: vi.fn(),
    newElementId: () => 'el_test0001',
  };
});
vi.mock('../sceneService', () => svc);

vi.mock('../../services/scriptService', () => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
  useOptionalToast: () => ({ addToast: vi.fn() }),
}));

import { StoryboardView, videoRegenSettled } from '../storyboard/StoryboardView';
import { EditorShell } from '../components/EditorShell';

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '200',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Kitchen',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [],
  ...over,
});

const shot = (over: Partial<Shot> = {}): Shot => ({
  id: '900',
  scene_id: '200',
  shot_number: 1,
  shot_type: 'WIDE',
  camera_angle: 'EYE',
  camera_movement: 'STATIC',
  focal_length: '35mm',
  lighting: 'Soft key',
  description: 'Establishing.',
  image_url: null,
  thumbnail_url: null,
  video_url: null,
  status: 'empty',
  sort_order: 1000,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('StoryboardView', () => {
  beforeEach(() => {
    svc.listShots.mockResolvedValue([]);
  });

  it('renders one column per scene and loads shots in parallel', async () => {
    svc.listShots.mockImplementation(async (sceneId: string) =>
      sceneId === '200' ? [shot({ id: '900', scene_id: '200' })] : [],
    );
    render(<StoryboardView scenes={[scene({ id: '200' }), scene({ id: '201' })]} scriptId="1" />);

    await waitFor(() => expect(screen.getAllByTestId('storyboard-column')).toHaveLength(2));
    // Both scenes were fetched; scene 200's shot card mounted.
    expect(svc.listShots).toHaveBeenCalledWith('200');
    expect(svc.listShots).toHaveBeenCalledWith('201');
    await waitFor(() => expect(screen.getByTestId('shot-card')).toBeInTheDocument());
  });

  it('adds a shot through the column footer', async () => {
    svc.createShot.mockResolvedValue(shot({ id: '910' }));
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('storyboard-column')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByText('editor.storyboardAddShot'));
    });
    expect(svc.createShot).toHaveBeenCalledWith('200', {});
  });

  it('deletes a shot after inline confirm', async () => {
    svc.listShots.mockResolvedValue([shot({ id: '900' })]);
    svc.deleteShot.mockResolvedValue(undefined);
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('shot-card')).toBeInTheDocument());

    const del = screen.getByLabelText('editor.shotDelete');
    fireEvent.click(del); // arm
    await act(async () => {
      fireEvent.click(del); // confirm
    });
    expect(svc.deleteShot).toHaveBeenCalledWith('900');
  });

  it('dispatches Auto Storyboard and polls until the shot list grows', async () => {
    vi.useFakeTimers();
    svc.listShots.mockResolvedValue([]); // mount: no shots yet
    svc.autoStoryboard.mockResolvedValue('task-1');
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await act(async () => {}); // flush the parallel mount load

    const autoBtn = screen.getByText('editor.storyboardAuto');
    fireEvent.click(autoBtn); // arm inline confirm
    // The breakdown has landed by the time the poll next fires.
    svc.listShots.mockResolvedValue([shot({ id: '950' })]);
    await act(async () => {
      fireEvent.click(autoBtn); // confirm → dispatch
    });
    expect(svc.autoStoryboard).toHaveBeenCalledWith('200');

    // First poll tick observes the grown shot list → column reloads.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByTestId('shot-card')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('reorders a shot within its column via a before/after anchor', async () => {
    svc.listShots.mockResolvedValue([
      shot({ id: '900', sort_order: 1000 }),
      shot({ id: '901', sort_order: 2000 }),
    ]);
    svc.moveShot.mockResolvedValue(shot({ id: '900' }));
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await waitFor(() => expect(screen.getAllByTestId('shot-card')).toHaveLength(2));

    const [cardA, cardB] = screen.getAllByTestId('shot-card');
    fireEvent.dragStart(cardA);
    await act(async () => {
      fireEvent.drop(cardB);
    });
    // jsdom rects are zero-sized → pointer lands on the "after" half.
    expect(svc.moveShot).toHaveBeenCalledWith('900', { after_shot_id: '901' });
  });

  it('generates a shot: dispatch → optimistic generating → poll to done thumbnail', async () => {
    vi.useFakeTimers();
    svc.listShots.mockResolvedValue([shot({ id: '900', status: 'empty' })]);
    svc.generateShot.mockResolvedValue('gen-task');
    svc.getShot.mockResolvedValue(
      shot({ id: '900', status: 'done', thumbnail_url: 'https://x/t.png' }),
    );
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await act(async () => {}); // flush mount load

    expect(screen.queryByRole('img')).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByText('editor.shotGenerate'));
    });
    expect(svc.generateShot).toHaveBeenCalledWith('900');

    // First poll tick sees status='done' → the thumbnail lands on screen.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole('img')).toBeInTheDocument();
    vi.useRealTimers();
  });

  // MUST stay last in this describe: a 404 flips a module-level session flag that
  // degrades every subsequent Generate button (it survives remounts by design).
  it('degrades every Generate control when the endpoint 404s (flag off)', async () => {
    svc.listShots.mockResolvedValue([shot({ id: '900', status: 'empty' })]);
    svc.generateShot.mockRejectedValue(new svc.ShotGenerateDisabledError());
    render(<StoryboardView scenes={[scene({ id: '200' })]} scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('shot-card')).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByText('editor.shotGenerate'));
    });
    expect(svc.generateShot).toHaveBeenCalledWith('900');
    await waitFor(() => expect(screen.getByText('editor.shotGenerate')).toBeDisabled());
    expect(screen.getByText('editor.shotGenerate')).toHaveAttribute(
      'title',
      'editor.shotGenerateComingSoon',
    );
  });
});

describe('videoRegenSettled — M1 re-generation poll guard', () => {
  it('does NOT settle while the shot still shows its prior video url', () => {
    // Re-generating a shot that already has a video: the poll must not be
    // satisfied by the stale url still present on the first tick.
    expect(videoRegenSettled('https://x/old.mp4', 'https://x/old.mp4')).toBe(false);
  });

  it('settles once a NEW url arrives', () => {
    expect(videoRegenSettled('https://x/new.mp4', 'https://x/old.mp4')).toBe(true);
  });

  it('settles on a first-ever video (no prior url)', () => {
    expect(videoRegenSettled('https://x/first.mp4', null)).toBe(true);
  });

  it('does not settle while there is still no video', () => {
    expect(videoRegenSettled(null, null)).toBe(false);
    expect(videoRegenSettled(null, 'https://x/old.mp4')).toBe(false);
  });
});

describe('EditorShell — Storyboard slot routing', () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    svc.listShots.mockResolvedValue([]);
  });

  it('routes the central pane to the shot board and reflects the active slot', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument(), { timeout: 15000 });

    expect(screen.queryByTestId('storyboard-view')).toBeNull();

    const slot = screen.getByRole('button', { name: /moduleStoryboard/ });
    expect(slot).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(slot);
    });

    expect(screen.getByTestId('storyboard-view')).toBeInTheDocument();
    expect(slot).toHaveAttribute('aria-current', 'page');
  }, 20000);
});
