/**
 * Beats M3 UI: the template Apply wizard, the target-length control + conform
 * prompt, and the methodology Guide drawer — driven through ArrangementView,
 * which owns all three overlays. Pure render/interaction assertions with mocked
 * callbacks (the network writes live in BeatsView, covered by beatsView tests).
 */
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Beat } from '../sceneService';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: Record<string, unknown>) => (opts ? `${k}:${JSON.stringify(opts)}` : k) }),
}));

import { ArrangementView } from '../beats/ArrangementView';

const noScenes: SceneDoc[] = [];

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

function renderArr(beats: Beat[], targetDurationSec: number | null) {
  const onSetTargetDuration = vi.fn();
  const onApplyTemplate = vi.fn();
  const onUpdate = vi.fn();
  localStorage.setItem('editor.beatsZoom.1', '6');
  render(
    <ArrangementView
      scriptId="1"
      beats={beats}
      scenes={noScenes}
      targetDurationSec={targetDurationSec}
      onAdd={vi.fn()}
      onUpdate={onUpdate}
      onCreate={vi.fn()}
      onOpenScene={vi.fn()}
      onSetTargetDuration={onSetTargetDuration}
      onApplyTemplate={onApplyTemplate}
    />,
  );
  return { onSetTargetDuration, onApplyTemplate, onUpdate };
}

beforeEach(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

describe('target-length control', () => {
  it('shows "Set length" when unset and commits a preset directly (no beats)', () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], null);
    const btn = screen.getByTestId('arr-length-btn');
    expect(btn).toHaveTextContent('editor.arrSetLength');

    fireEvent.click(btn);
    // 45' preset = 2700s
    const preset = screen.getAllByTestId('arr-length-preset').find((b) => b.dataset.sec === '2700')!;
    fireEvent.click(preset);
    // No previous target → commits straight through, no conform prompt.
    expect(h.onSetTargetDuration).toHaveBeenCalledWith(2700);
    expect(screen.queryByTestId('beats-conform-modal')).toBeNull();
  });

  it('parses a custom m:ss entry and commits it', () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], null);
    fireEvent.click(screen.getByTestId('arr-length-btn'));
    fireEvent.change(screen.getByTestId('arr-length-custom'), { target: { value: '1:30' } });
    fireEvent.click(screen.getByTestId('arr-length-custom-apply'));
    expect(h.onSetTargetDuration).toHaveBeenCalledWith(90);
  });

  it('prompts conform when changing the target with beats arranged, and stretches on demand', () => {
    const h = renderArr(
      [
        beat({ id: 'a', start_sec: 0, duration_sec: 30 }),
        beat({ id: 'b', start_sec: 30, duration_sec: 30 }),
      ],
      60,
    );
    fireEvent.click(screen.getByTestId('arr-length-btn'));
    const preset = screen.getAllByTestId('arr-length-preset').find((b) => b.dataset.sec === '180')!;
    fireEvent.click(preset);

    // Conform modal, not an immediate commit.
    expect(screen.getByTestId('beats-conform-modal')).toBeInTheDocument();
    expect(h.onSetTargetDuration).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('beats-conform-stretch'));
    // 60 → 180 (×3): both beats rescaled, then the new target persisted.
    expect(h.onUpdate).toHaveBeenCalledWith('a', { start_sec: 0, duration_sec: 90 });
    expect(h.onUpdate).toHaveBeenCalledWith('b', { start_sec: 90, duration_sec: 90 });
    expect(h.onSetTargetDuration).toHaveBeenCalledWith(180);
  });

  it('resize-ruler-only skips the stretch but still persists the target', () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], 60);
    fireEvent.click(screen.getByTestId('arr-length-btn'));
    fireEvent.click(screen.getAllByTestId('arr-length-preset').find((b) => b.dataset.sec === '180')!);
    fireEvent.click(screen.getByTestId('beats-conform-resize'));
    expect(h.onUpdate).not.toHaveBeenCalled();
    expect(h.onSetTargetDuration).toHaveBeenCalledWith(180);
  });
});

describe('template Apply wizard', () => {
  it('applies Save the Cat to a 60s target as 15 append beats (issue baseline)', async () => {
    // Empty script → enter via the empty-state card (the topbar only exists once
    // there are beats). Empty → wizard forces append, skipping the mode step.
    const h = renderArr([], null);
    fireEvent.click(
      screen.getAllByTestId('beats-template-empty-card').find((c) => c.dataset.key === 'save_the_cat')!,
    );

    const wizard = await screen.findByTestId('beats-template-wizard');
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // preselected → length

    // Pick the 60s preset then Generate (no existing beats → the Next is Generate).
    fireEvent.click(within(wizard).getAllByTestId('beats-template-preset').find((b) => b.dataset.sec === '60')!);
    fireEvent.click(within(wizard).getByTestId('beats-template-next'));

    expect(h.onApplyTemplate).toHaveBeenCalledTimes(1);
    const [beats, mode, target] = h.onApplyTemplate.mock.calls[0];
    expect(mode).toBe('append');
    expect(target).toBe(60);
    expect(beats).toHaveLength(15);
    const midpoint = beats.find((b: Beat) => b.beat_role === 'save_the_cat.midpoint');
    expect(midpoint.start_sec).toBe(30);
  });

  it('offers append vs replace when beats already exist', async () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], 600);
    fireEvent.click(screen.getByTestId('arr-templates'));
    const wizard = await screen.findByTestId('beats-template-wizard');
    fireEvent.click(within(wizard).getAllByTestId('beats-template-card').find((c) => c.dataset.key === 'five_beats')!);
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → length
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → mode (beats exist)

    fireEvent.click(within(wizard).getByTestId('beats-template-mode-replace'));
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // Generate

    const [beats, mode] = h.onApplyTemplate.mock.calls[0];
    expect(mode).toBe('replace');
    expect(beats).toHaveLength(5);
  });

  it('opens the wizard preselected from an empty-state template card', async () => {
    renderArr([], null);
    const cards = screen.getAllByTestId('beats-template-empty-card');
    fireEvent.click(cards.find((c) => c.dataset.key === 'kishotenketsu')!);
    const wizard = await screen.findByTestId('beats-template-wizard');
    // Preselected → the kishotenketsu card reads as selected.
    const selected = within(wizard)
      .getAllByTestId('beats-template-card')
      .find((c) => c.dataset.key === 'kishotenketsu')!;
    expect(selected.className).toContain('selected');
  });
});

describe('methodology Guide drawer', () => {
  it('opens with three tabs and switches the active methodology', async () => {
    renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], null);
    fireEvent.click(screen.getByTestId('arr-guide'));
    const drawer = await screen.findByTestId('beats-guide-drawer');
    expect(within(drawer).getAllByTestId('beats-guide-tab')).toHaveLength(3);

    fireEvent.click(within(drawer).getAllByTestId('beats-guide-tab').find((t) => t.dataset.key === 'kishotenketsu')!);
    // The body renders the kishotenketsu beats (ki … ketsu).
    expect(within(drawer).getByTestId('beats-guide-body')).toHaveTextContent('kishotenketsu.ki.name');

    fireEvent.click(within(drawer).getByTestId('beats-guide-close'));
    await waitFor(() => expect(screen.queryByTestId('beats-guide-drawer')).toBeNull());
  });
});
