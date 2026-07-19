/**
 * BeatsView tests (PR-BT2): CRUD chain, drag→move API, scene-chip jump, empty
 * state, and inline title edit. React Flow-free — BeatsView is a plain list —
 * so we mock the sceneService beats API + Toast and drive the real component.
 */
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Beat } from '../sceneService';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listBeats: vi.fn(),
  createBeat: vi.fn(),
  updateBeat: vi.fn(),
  deleteBeat: vi.fn(),
  moveBeat: vi.fn(),
}));
vi.mock('../sceneService', () => svc);

const toast = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: toast.addToast }),
  useOptionalToast: () => ({ addToast: toast.addToast }),
}));

import { BeatsView } from '../beats/BeatsView';

const scene = (over: Partial<SceneDoc> & { id: string }): SceneDoc => ({
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Room',
  time_of_day: 'DAY',
  content_version: 1,
  elements: [],
  sort_order: 0,
  ...over,
});

const beat = (over: Partial<Beat> & { id: string }): Beat => ({
  script_id: '1',
  title: 'Setup',
  summary: null,
  scene_ids: [],
  sort_order: 1000,
  start_sec: null,
  duration_sec: null,
  beat_role: null,
  color: null,
  ...over,
});

beforeEach(() => {
  // M2 added a per-script sub-view toggle defaulting to the Arrangement timeline.
  // These are the List sub-view regressions, so pin the persisted choice to
  // 'list' (scriptId "1") before mount — one seed keeps all 17 assertions intact.
  localStorage.setItem('editor.beatsView.1', 'list');
  // Every mutation returns a promise so the component's `.catch` chains resolve.
  svc.listBeats.mockResolvedValue([]);
  svc.createBeat.mockResolvedValue(beat({ id: 'a' }));
  svc.updateBeat.mockResolvedValue(beat({ id: 'a' }));
  svc.deleteBeat.mockResolvedValue(undefined);
  svc.moveBeat.mockResolvedValue(beat({ id: 'a' }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

function renderView(scenes: SceneDoc[] = []) {
  const onOpenScene = vi.fn();
  render(<BeatsView scriptId="1" scenes={scenes} onOpenScene={onOpenScene} />);
  return { onOpenScene };
}

describe('BeatsView empty state', () => {
  it('shows the empty state and adds the first beat', async () => {
    svc.listBeats.mockResolvedValue([]);
    svc.createBeat.mockResolvedValue(beat({ id: 'a' }));
    renderView();

    await waitFor(() => expect(screen.getByTestId('beats-empty')).toBeInTheDocument());

    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    fireEvent.click(screen.getByTestId('beats-add'));

    // Adds now arrange at the timeline end (0 on an empty script) — an
    // unplaced beat would land in the below-the-fold tray where nobody finds it.
    await waitFor(() =>
      expect(svc.createBeat).toHaveBeenCalledWith('1', {
        title: 'editor.beatDefaultTitle',
        start_sec: 0,
        duration_sec: 60,
      }),
    );
  });
});

describe('BeatsView CRUD', () => {
  it('commits an inline title edit on blur', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', title: 'Setup' })]);
    renderView();

    const input = await screen.findByDisplayValue('Setup');
    fireEvent.change(input, { target: { value: 'Opening Image' } });
    fireEvent.blur(input);

    expect(svc.updateBeat).toHaveBeenCalledWith('a', { title: 'Opening Image' });
  });

  it('reverts an emptied title without calling update', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', title: 'Setup' })]);
    renderView();
    const input = await screen.findByDisplayValue('Setup');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.blur(input);
    expect(svc.updateBeat).not.toHaveBeenCalled();
  });

  it('deletes a beat only after a second (confirming) click', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    svc.deleteBeat.mockResolvedValue(undefined);
    renderView();

    const del = await screen.findByTestId('beat-delete');
    fireEvent.click(del);
    expect(svc.deleteBeat).not.toHaveBeenCalled();
    expect(del).toHaveTextContent('editor.beatConfirmDelete');

    fireEvent.click(del);
    expect(svc.deleteBeat).toHaveBeenCalledWith('a');
  });
});

describe('BeatsView scene links', () => {
  it('jumps to a linked scene when its chip is clicked', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', scene_ids: ['s1'] })]);
    const { onOpenScene } = renderView([scene({ id: 's1' })]);

    const chip = await screen.findByTestId('beat-scene-chip');
    fireEvent.click(chip.querySelector('.mh-beat-chip-label')!);
    expect(onOpenScene).toHaveBeenCalledWith('s1');
  });

  it('links a scene through the picker', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', scene_ids: [] })]);
    renderView([scene({ id: 's1' })]);

    // UiSelect: open the trigger, then pick the scene option (its onChange fires
    // from the portal option click, not a native <select> change event).
    const trigger = await screen.findByLabelText('editor.beatLinkScene');
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('option', { name: 'INT · Room · DAY' }));
    expect(svc.updateBeat).toHaveBeenCalledWith('a', { scene_ids: ['s1'] });
  });
});

describe('BeatsView arrangement fields (M1)', () => {
  it('renders a duration chip in minutes for a long beat', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', duration_sec: 600 })]);
    renderView();
    const chip = await screen.findByTestId('beat-duration-chip');
    expect(chip).toHaveTextContent('10m');
  });

  it('renders a duration chip in seconds for a short beat (<180s)', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', duration_sec: 90 })]);
    renderView();
    const chip = await screen.findByTestId('beat-duration-chip');
    expect(chip).toHaveTextContent('90s');
  });

  it('renders a color bar when a color is set', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', color: '#b8b0a0' })]);
    renderView();
    const bar = await screen.findByTestId('beat-color-bar');
    expect(bar).toHaveStyle({ background: '#b8b0a0' });
  });

  it('old (all-NULL) beats render with no chip and no color bar', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    renderView();
    await screen.findByTestId('beat-card');
    expect(screen.queryByTestId('beat-duration-chip')).toBeNull();
    expect(screen.queryByTestId('beat-color-bar')).toBeNull();
  });

  it('commits a duration edit as an integer through updateBeat', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    renderView();
    // The duration input lives in the (expandable) notes/edit panel.
    fireEvent.click(await screen.findByText('editor.beatNotes'));
    const input = await screen.findByLabelText('editor.beatDuration');
    fireEvent.change(input, { target: { value: '120' } });
    fireEvent.blur(input);
    expect(svc.updateBeat).toHaveBeenCalledWith('a', { duration_sec: 120 });
  });

  it('clearing the duration input commits null (clear semantics)', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', duration_sec: 600 })]);
    renderView();
    const input = await screen.findByLabelText('editor.beatDuration');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    expect(svc.updateBeat).toHaveBeenCalledWith('a', { duration_sec: null });
  });

  it('an invalid duration reverts to the saved value without a network call', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', duration_sec: 600 })]);
    renderView();
    const input = await screen.findByLabelText('editor.beatDuration');
    fireEvent.change(input, { target: { value: '-5' } });
    fireEvent.blur(input);
    expect(svc.updateBeat).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe('600');
  });

  it('a duration beyond the PG INTEGER ceiling reverts instead of round-tripping', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', duration_sec: 600 })]);
    renderView();
    const input = await screen.findByLabelText('editor.beatDuration');
    fireEvent.change(input, { target: { value: '9999999999' } });
    fireEvent.blur(input);
    expect(svc.updateBeat).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe('600');
  });

  it('clicking the selected swatch clears the color (toggle-off)', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', color: '#b8a9a0' })]);
    renderView();
    const swatches = await screen.findAllByTestId('beat-color-swatch');
    fireEvent.click(swatches[0]);
    expect(svc.updateBeat).toHaveBeenCalledWith('a', { color: null });
  });

  it('picks a color swatch and commits it through updateBeat', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    renderView();
    fireEvent.click(await screen.findByText('editor.beatNotes'));
    const swatches = await screen.findAllByTestId('beat-color-swatch');
    fireEvent.click(swatches[0]);
    expect(svc.updateBeat).toHaveBeenCalledWith('a', {
      color: expect.stringMatching(/^#[0-9a-fA-F]{6}$/),
    });
  });
});

describe('BeatsView reorder', () => {
  it('calls move with after_beat_id when dropped on a later beat', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' }), beat({ id: 'b', title: 'Second' })]);
    svc.moveBeat.mockResolvedValue(beat({ id: 'a' }));
    renderView();

    const cards = await screen.findAllByTestId('beat-card');
    const rowA = cards[0].parentElement as HTMLElement;
    const rowB = cards[1].parentElement as HTMLElement;
    // Deterministic drop-edge: bottom half of B's row → 'after'.
    rowB.getBoundingClientRect = () =>
      ({ top: 0, height: 100, bottom: 100, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON() {} }) as DOMRect;

    const handleA = rowA.querySelector('[data-testid="beat-drag-handle"]')!;
    fireEvent.dragStart(handleA, { dataTransfer: { setData: vi.fn() } });
    fireEvent.dragOver(rowB, { clientY: 80 });
    await act(async () => {
      fireEvent.drop(rowB, { clientY: 80 });
    });

    expect(svc.moveBeat).toHaveBeenCalledWith('a', { after_beat_id: 'b' });
  });
});
