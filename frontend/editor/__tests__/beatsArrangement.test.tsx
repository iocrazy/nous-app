/**
 * Beats M2 Arrangement tests: the sub-view toggle (default + persistence) on
 * BeatsView, and the ArrangementView timeline geometry / tray / zoom driven
 * directly. Pointer-drag persistence is covered by the e2e spec (real timing);
 * here we assert the render math and the non-drag mutations.
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
import { ArrangementView } from '../beats/ArrangementView';

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
  svc.listBeats.mockResolvedValue([]);
  svc.createBeat.mockResolvedValue(beat({ id: 'a' }));
  svc.updateBeat.mockResolvedValue(beat({ id: 'a' }));
  // jsdom has no scrollIntoView — the left-rail focus calls it.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

const noScenes: SceneDoc[] = [];

describe('BeatsView sub-view toggle', () => {
  it('defaults to the Arrangement timeline sub-view', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a', start_sec: 0, duration_sec: 30 })]);
    render(<BeatsView scriptId="1" scenes={noScenes} onOpenScene={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId('beats-arrangement')).toBeInTheDocument());
    expect(screen.queryByTestId('beats-view')).toBeNull();
  });

  it('switches to List and persists the choice per-script', async () => {
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    render(<BeatsView scriptId="1" scenes={noScenes} onOpenScene={vi.fn()} />);

    await screen.findByTestId('beats-arrangement');
    fireEvent.click(screen.getByTestId('beats-subview-list'));

    await waitFor(() => expect(screen.getByTestId('beats-view')).toBeInTheDocument());
    expect(localStorage.getItem('editor.beatsView.1')).toBe('list');

    fireEvent.click(screen.getByTestId('beats-subview-arrangement'));
    await waitFor(() => expect(screen.getByTestId('beats-arrangement')).toBeInTheDocument());
    expect(localStorage.getItem('editor.beatsView.1')).toBe('arrangement');
  });

  it('reads a persisted List choice on mount', async () => {
    localStorage.setItem('editor.beatsView.1', 'list');
    svc.listBeats.mockResolvedValue([beat({ id: 'a' })]);
    render(<BeatsView scriptId="1" scenes={noScenes} onOpenScene={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId('beats-view')).toBeInTheDocument());
    expect(screen.queryByTestId('beats-arrangement')).toBeNull();
  });
});

describe('ArrangementView timeline geometry', () => {
  function renderArrangement(beats: Beat[], zoom = 6) {
    localStorage.setItem('editor.beatsZoom.1', String(zoom));
    const onUpdate = vi.fn();
    const onCreate = vi.fn();
    render(
      <ArrangementView
        scriptId="1"
        beats={beats}
        onAdd={vi.fn()}
        onUpdate={onUpdate}
        onCreate={onCreate}
      />,
    );
    return { onUpdate, onCreate };
  }

  it('positions and sizes an arranged card from start/duration × pxPerSec', async () => {
    renderArrangement([beat({ id: 'a', start_sec: 60, duration_sec: 120 })], 6);
    const card = await screen.findByTestId('arr-card');
    // left = 60 × 6 = 360; width = 120 × 6 = 720
    expect(card).toHaveStyle({ left: '360px', width: '720px' });
  });

  it('renders a duration chip using the shared formatter', async () => {
    renderArrangement([beat({ id: 'a', start_sec: 0, duration_sec: 600 })], 6);
    const card = await screen.findByTestId('arr-card');
    expect(card).toHaveTextContent('10m');
  });

  it('sorts start_sec-null beats into the Unarranged tray, not onto lanes', async () => {
    renderArrangement([
      beat({ id: 'arranged', start_sec: 0, duration_sec: 30 }),
      beat({ id: 'loose', title: 'Loose', start_sec: null }),
    ]);
    await screen.findByTestId('arr-card');
    const trayCards = screen.getAllByTestId('arr-tray-card');
    expect(trayCards).toHaveLength(1);
    expect(trayCards[0]).toHaveAttribute('data-beat-id', 'loose');
    // The arranged beat is a lane card, not a tray card.
    expect(screen.getByTestId('arr-card')).toHaveAttribute('data-beat-id', 'arranged');
  });

  it('Place assigns start_sec at the end of the timeline via updateBeat', async () => {
    const { onUpdate } = renderArrangement([beat({ id: 'loose', start_sec: null })]);
    fireEvent.click(await screen.findByTestId('arr-tray-place'));
    // No arranged beats yet → end of timeline is 0.
    expect(onUpdate).toHaveBeenCalledWith('loose', { start_sec: 0 });
  });

  it('double-clicking empty timeline creates a beat at that instant', async () => {
    const { onCreate } = renderArrangement(
      [beat({ id: 'a', start_sec: 0, duration_sec: 30 })],
      6,
    );
    const lanes = await screen.findByTestId('arr-lanes');
    fireEvent.doubleClick(lanes, { clientX: 120 });
    // x=120 → 120/6 = 20s, snapped to the 5s seconds-grid → 20; default duration 60.
    expect(onCreate).toHaveBeenCalledWith({ start_sec: 20, duration_sec: 60 });
  });

  it('Fit view rescales zoom to the viewport and shrinks the card', async () => {
    renderArrangement([beat({ id: 'a', start_sec: 0, duration_sec: 600 })], 30);
    const card = await screen.findByTestId('arr-card');
    const wideWidth = Number.parseFloat((card as HTMLElement).style.width);
    expect(wideWidth).toBeGreaterThan(10000); // 600 × 30 = 18000

    // Give the viewport a measurable width so Fit has something to fit into.
    const viewport = screen.getByTestId('arr-viewport');
    Object.defineProperty(viewport, 'clientWidth', { configurable: true, value: 800 });

    await act(async () => {
      fireEvent.click(screen.getByTestId('arr-fit'));
    });

    const fittedWidth = Number.parseFloat((screen.getByTestId('arr-card') as HTMLElement).style.width);
    expect(fittedWidth).toBeLessThan(wideWidth);
    // Fit persists the new zoom per-script.
    expect(localStorage.getItem('editor.beatsZoom.1')).not.toBe('30');
  });

  it('highlights a beat when its left-rail row is clicked', async () => {
    renderArrangement([beat({ id: 'a', start_sec: 0, duration_sec: 30 })]);
    const row = await screen.findByTestId('arr-list-item');
    fireEvent.click(row);
    expect(row.className).toContain('selected');
  });
});
