/**
 * Beats M2 Arrangement tests: the sub-view toggle (default + persistence) on
 * BeatsView, and the ArrangementView timeline geometry / tray / zoom driven
 * directly. Pointer-drag persistence is covered by the e2e spec (real timing);
 * here we assert the render math and the non-drag mutations.
 */
import { render, screen, fireEvent, waitFor, cleanup, act, within } from '@testing-library/react';
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

const scriptSvc = vi.hoisted(() => ({
  fetchScriptProject: vi.fn(),
  updateScriptProject: vi.fn(),
}));
vi.mock('../../services/scriptService', () => scriptSvc);

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
  scriptSvc.fetchScriptProject.mockResolvedValue({ target_duration_sec: null });
  scriptSvc.updateScriptProject.mockResolvedValue({});
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
  function renderArrangement(beats: Beat[], zoom: number | null = 6) {
    if (zoom != null) localStorage.setItem('editor.beatsZoom.1', String(zoom));
    const onUpdate = vi.fn();
    const onCreate = vi.fn();
    render(
      <ArrangementView
        scriptId="1"
        beats={beats}
        scenes={noScenes}
        targetDurationSec={null}
        onAdd={vi.fn()}
        onUpdate={onUpdate}
        onCreate={onCreate}
        onOpenScene={vi.fn()}
        onSetTargetDuration={vi.fn()}
        onApplyTemplate={vi.fn()}
      />,
    );
    return { onUpdate, onCreate };
  }

  it('positions and sizes an arranged card from start/duration × pxPerSec', async () => {
    renderArrangement([beat({ id: 'a', start_sec: 60, duration_sec: 120 })], 6);
    const card = await screen.findByTestId('arr-card');
    // left = 60 × 6 = 360; width = 120 × 6 = 720, minus the 8px inter-card gap → 712
    expect(card).toHaveStyle({ left: '360px', width: '712px' });
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

  it('opens the Edit Beat modal and saves a changed title via onUpdate', async () => {
    const { onUpdate } = renderArrangement([
      beat({ id: 'a', start_sec: 0, duration_sec: 60, title: 'Setup' }),
    ]);
    fireEvent.click(await screen.findByTestId('arr-card-edit'));
    const modal = await screen.findByTestId('beat-edit-modal');
    fireEvent.change(within(modal).getByTestId('beat-edit-title'), {
      target: { value: 'Opening Image' },
    });
    fireEvent.click(within(modal).getByTestId('beat-edit-save'));
    // Only the changed field is committed (exclude_unset semantics).
    expect(onUpdate).toHaveBeenCalledWith('a', { title: 'Opening Image' });
    await waitFor(() => expect(screen.queryByTestId('beat-edit-modal')).toBeNull());
  });

  it('auto-fits the timeline on first open when no zoom is stored', async () => {
    // No editor.beatsZoom seed → the auto-fit path runs. Stub the viewport width
    // (jsdom reports 0) so Fit has a real width to fit into.
    const proto = window.HTMLElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'clientWidth');
    Object.defineProperty(proto, 'clientWidth', { configurable: true, get: () => 800 });
    try {
      renderArrangement([beat({ id: 'a', start_sec: 0, duration_sec: 600 })], null);
      await screen.findByTestId('arr-card');
      // Auto-fit → ~800/600 px·s⁻¹, width ≈ 792 — far below the default-zoom (6)
      // width of 3592, proving the fit ran instead of the default applying.
      await waitFor(() => {
        const w = Number.parseFloat((screen.getByTestId('arr-card') as HTMLElement).style.width);
        expect(w).toBeLessThan(1500);
      });
    } finally {
      if (desc) Object.defineProperty(proto, 'clientWidth', desc);
      else delete (proto as unknown as Record<string, unknown>).clientWidth;
    }
  });
});

describe('laper topbar + NLE drag guide', () => {
  it('shows the beat count and creates a beat from the topbar Add', async () => {
    svc.listBeats.mockResolvedValue([
      beat({ id: 'a', start_sec: 0, duration_sec: 30 }),
      beat({ id: 'b', start_sec: 30, duration_sec: 30 }),
    ]);
    render(<BeatsView scriptId="1" scenes={noScenes} onOpenScene={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('beats-arrangement')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('beats-topbar-add'));
    await waitFor(() =>
      expect(svc.createBeat).toHaveBeenCalledWith('1', {
        title: 'editor.beatDefaultTitle',
        start_sec: 60,
        duration_sec: 60,
      }),
    );
  });

  it('renders a full-height guide with a time chip while dragging, gone on release', async () => {
    render(
      <ArrangementView
        scriptId="1"
        beats={[beat({ id: 'a', start_sec: 60, duration_sec: 30 })]}
        scenes={noScenes}
        targetDurationSec={null}
        onAdd={vi.fn()}
        onUpdate={vi.fn()}
        onCreate={vi.fn()}
        onOpenScene={vi.fn()}
        onSetTargetDuration={vi.fn()}
        onApplyTemplate={vi.fn()}
      />,
    );
    const card = await screen.findByTestId('arr-card');
    expect(screen.queryByTestId('arr-drag-guide')).toBeNull();
    fireEvent.pointerDown(card, { pointerId: 1, button: 0, clientX: 400 });
    fireEvent.pointerMove(card, { pointerId: 1, clientX: 430 });
    const guide = screen.getByTestId('arr-drag-guide');
    // moved +30px at 6 px/s = +5s → snapped start 65 → guide at 65×6 = 390px
    expect(guide.style.left).toBe('390px');
    expect(guide).toHaveTextContent("1'05");
    fireEvent.pointerUp(card, { pointerId: 1 });
    expect(screen.queryByTestId('arr-drag-guide')).toBeNull();
  });
});

describe('template replace ordering (data-loss guard)', () => {
  it('creates ALL template beats before deleting any existing beat', async () => {
    const calls: string[] = [];
    svc.listBeats.mockResolvedValue([
      beat({ id: 'old1', start_sec: 0, duration_sec: 30 }),
      beat({ id: 'old2', start_sec: 30, duration_sec: 30 }),
    ]);
    svc.createBeat.mockImplementation(async () => {
      calls.push('create');
      return beat({ id: `n${calls.length}` });
    });
    svc.deleteBeat.mockImplementation(async (id: string) => {
      calls.push(`delete:${id}`);
    });
    render(<BeatsView scriptId="1" scenes={noScenes} onOpenScene={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId('arr-card')).toHaveLength(2));

    fireEvent.click(screen.getByTestId('arr-templates'));
    const wizard = await screen.findByTestId('beats-template-wizard');
    fireEvent.click(
      within(wizard)
        .getAllByTestId('beats-template-card')
        .find((c) => (c as HTMLElement).dataset.key === 'five_beats')!,
    );
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → length
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → mode
    fireEvent.click(within(wizard).getByTestId('beats-template-mode-replace'));
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // Generate

    await waitFor(() => expect(calls.filter((c) => c.startsWith('delete')).length).toBe(2));
    // Every create precedes every delete — a mid-batch create failure must
    // leave the user's existing sheet untouched.
    const lastCreate = calls.lastIndexOf('create');
    const firstDelete = calls.findIndex((c) => c.startsWith('delete'));
    expect(calls.filter((c) => c === 'create')).toHaveLength(5);
    expect(firstDelete).toBeGreaterThan(lastCreate);
  });
});
