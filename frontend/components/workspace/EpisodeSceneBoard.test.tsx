/**
 * EpisodeSceneBoard (三视图 Task 1) — the "Storyboard" view's vertical scene
 * card stream. Pins: one card per scene with real slate metadata, Open
 * deep-links out via a callback, Auto Storyboard is a two-click-confirm
 * dispatch (mirrors StoryboardView:153-200 — logic ported, not imported), and
 * the shot list degrades to one of two empty-state copies depending on
 * whether the scene itself has any content yet.
 */
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listShots: vi.fn(),
  autoStoryboard: vi.fn(),
}));
vi.mock('../../editor/sceneService', () => svc);

import { EpisodeSceneBoard } from './EpisodeSceneBoard';

const scene = (over: Partial<SceneDoc> = {}): SceneDoc => ({
  id: '200',
  script_id: '1',
  chapter_id: null,
  scene_number: null,
  heading_int_ext: 'INT',
  location_text: 'Kitchen',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_1', type: 'action', text: 'She enters.' }],
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
  lighting: null,
  description: 'Establishing shot of the kitchen.',
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

describe('EpisodeSceneBoard', () => {
  beforeEach(() => {
    svc.listShots.mockResolvedValue([]);
  });

  it('renders one card per scene, self-fetching scenes and shots in parallel', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' }), scene({ id: '201' })]);
    svc.listShots.mockImplementation(async (sceneId: string) =>
      sceneId === '200' ? [shot({ id: '900' })] : [],
    );
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId('scene-column-200')).toBeInTheDocument();
      expect(screen.getByTestId('scene-column-201')).toBeInTheDocument();
    });
    expect(svc.listScenes).toHaveBeenCalledWith('1');
    expect(svc.listShots).toHaveBeenCalledWith('200');
    expect(svc.listShots).toHaveBeenCalledWith('201');
  });

  it('shows real scene_number in the metadata grid, falling back to a 1-based index when absent', async () => {
    svc.listScenes.mockResolvedValue([
      scene({ id: '200', scene_number: '7A' }),
      scene({ id: '201', scene_number: null }),
    ]);
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);

    await waitFor(() => expect(screen.getByTestId('scene-column-200')).toBeInTheDocument());
    expect(screen.getByTestId('scene-column-200')).toHaveTextContent('7A');
    // Second scene has no scene_number → falls back to its 1-based position (2).
    expect(screen.getByTestId('scene-column-201')).toHaveTextContent('2');
    // I-E / LOCATION / D-N cells surface the real fields too.
    expect(screen.getByTestId('scene-column-200')).toHaveTextContent('INT');
    expect(screen.getByTestId('scene-column-200')).toHaveTextContent('Kitchen');
    expect(screen.getByTestId('scene-column-200')).toHaveTextContent('DAY');
  });

  it('falls back to an em dash for missing I-E/LOCATION/D-N fields', async () => {
    svc.listScenes.mockResolvedValue([
      scene({ id: '200', heading_int_ext: null, location_text: null, time_of_day: null }),
    ]);
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('scene-column-200')).toBeInTheDocument());
    // Three blank slate fields render the placeholder dash.
    expect(screen.getByTestId('scene-column-200').textContent?.match(/—/g)?.length).toBe(3);
  });

  it('calls onOpenScene with the scene id when Open is clicked', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    const onOpenScene = vi.fn();
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={onOpenScene} onOpenShot={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('ep-scene-open-200')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('ep-scene-open-200'));
    expect(onOpenScene).toHaveBeenCalledWith('200');
  });

  it('requires two clicks on Auto Storyboard before dispatching', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    svc.autoStoryboard.mockResolvedValue('task-1');
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('ep-scene-auto-200')).toBeInTheDocument());

    const autoBtn = screen.getByTestId('ep-scene-auto-200');
    fireEvent.click(autoBtn); // arm
    expect(svc.autoStoryboard).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(autoBtn); // confirm → dispatch
    });
    expect(svc.autoStoryboard).toHaveBeenCalledWith('200');
  });

  it('polls listShots after Auto Storyboard confirm and renders the grown list', async () => {
    vi.useFakeTimers();
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    svc.listShots.mockResolvedValue([]); // mount: no shots yet
    svc.autoStoryboard.mockResolvedValue('task-1');
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);
    await act(async () => {}); // flush mount load

    const autoBtn = screen.getByTestId('ep-scene-auto-200');
    fireEvent.click(autoBtn); // arm
    svc.listShots.mockResolvedValue([shot({ id: '950' })]);
    await act(async () => {
      fireEvent.click(autoBtn); // confirm → dispatch
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // The poll settled on the grown list: the empty-state copy is gone and the
    // new shot's card now renders (Task 3 layout: cards show a shot badge +
    // hint, not the description — this only checks that shot 950 mounted).
    const shotsBox = screen.getByTestId('ep-scene-shots-200');
    expect(shotsBox).not.toHaveTextContent('projects.sceneBoard.noShots');
    expect(screen.getByTestId('shot-card-950')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('shows the "run auto storyboard" empty state for a scripted scene with no shots', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200', elements: [{ id: 'el_1', type: 'action', text: 'x' }] })]);
    svc.listShots.mockResolvedValue([]);
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId('ep-scene-shots-200')).toHaveTextContent(
        'projects.sceneBoard.noShots',
      ),
    );
  });

  it('shows the "no content yet" empty state for a blank scene with no shots', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200', elements: [] })]);
    svc.listShots.mockResolvedValue([]);
    render(<EpisodeSceneBoard scriptId="1" onOpenScene={() => {}} onOpenShot={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId('ep-scene-shots-200')).toHaveTextContent(
        'projects.sceneBoard.emptyScene',
      ),
    );
  });

  // Task 3 (分列布局 + 镜头卡进画布): scenes lay out as fixed-width columns,
  // not full-width rows — the whole point being a screenplay slate you can
  // scan left-to-right like a real storyboard. Both ids exceed 2^53 to prove
  // the string-id convention (#1006) holds through the new markup — no
  // `Number()` round-trip anywhere in the render path.
  it('lays scenes out as fixed-width columns, not full-width rows', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '9007199254740995' })]);
    render(
      <EpisodeSceneBoard scriptId="sc1" onOpenScene={vi.fn()} onOpenShot={vi.fn()} />,
    );
    const col = await screen.findByTestId('scene-column-9007199254740995');
    expect(col.className).toMatch(/w-\[236px\]/);
    expect(screen.getByTestId('scene-columns').className).toMatch(/overflow-x-auto/);
  });

  // Task 3 修复轮2 (2026-08-10 用户拍板): the shot's own sceneId rides along
  // (the column it's rendered in already has it) — the editor deep-link
  // orchestration in ProjectWorkspace needs it to route via
  // `openEpisodeScript(episode, 'storyboard', sceneId)`. Both ids stay
  // strings, no `Number()` round-trip.
  it('clicking a shot card reports the shot id AND its scene id', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '9007199254740995' })]);
    svc.listShots.mockResolvedValue([shot({ id: '9007199254740997', scene_id: '9007199254740995' })]);
    const onOpenShot = vi.fn();
    render(
      <EpisodeSceneBoard scriptId="sc1" onOpenScene={vi.fn()} onOpenShot={onOpenShot} />,
    );
    (await screen.findByTestId('shot-card-9007199254740997')).click();
    expect(onOpenShot).toHaveBeenCalledWith('9007199254740997', '9007199254740995');
  });
});
