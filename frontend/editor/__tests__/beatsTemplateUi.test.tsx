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

// M4: ArrangementView hosts MemoRail — stub Toast + Auth + the notes API.
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('../../services/inspirationService', () => ({
  listAnchoredNotes: vi.fn().mockResolvedValue([]),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
  uploadAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `att/${id}`,
}));

import { ArrangementView } from '../beats/ArrangementView';
import type { CustomTemplate } from '../beats/beatTemplateService';

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

function renderArr(
  beats: Beat[],
  targetDurationSec: number | null,
  extra: { customTemplates?: CustomTemplate[] } = {},
) {
  const onSetTargetDuration = vi.fn();
  const onApplyTemplate = vi.fn();
  const onUpdate = vi.fn();
  const onSaveTemplate = vi.fn();
  const onDeleteCustomTemplate = vi.fn();
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
      customTemplates={extra.customTemplates ?? []}
      onSaveTemplate={onSaveTemplate}
      onDeleteCustomTemplate={onDeleteCustomTemplate}
    />,
  );
  return { onSetTargetDuration, onApplyTemplate, onUpdate, onSaveTemplate, onDeleteCustomTemplate };
}

const customTpl = (over: Partial<CustomTemplate> & { id: string }): CustomTemplate => ({
  name: 'My Structure',
  anchors: [
    { title: 'Hook', summary: null, pctStart: 0, pctEnd: 10, color: '#b8b0a0' },
    { title: 'Payoff', summary: null, pctStart: 80, pctEnd: 100, color: null },
  ],
  ...over,
});

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

describe('custom templates (M3.5)', () => {
  it('lists custom templates beside the built-in three in the wizard', async () => {
    renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], 600, {
      customTemplates: [customTpl({ id: '77', name: 'My Two-Beater' })],
    });
    fireEvent.click(screen.getByTestId('arr-templates'));
    const wizard = await screen.findByTestId('beats-template-wizard');
    const cards = within(wizard).getAllByTestId('beats-template-card');
    // 3 built-in + 1 custom.
    expect(cards).toHaveLength(4);
    const custom = cards.find((c) => c.dataset.key === 'custom:77')!;
    expect(custom).toHaveTextContent('My Two-Beater');
    expect(custom.dataset.custom).toBe('true');
  });

  it('applies a custom template, replaying its stored anchors verbatim', async () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], 600, {
      customTemplates: [customTpl({ id: '77', name: 'My Two-Beater' })],
    });
    fireEvent.click(screen.getByTestId('arr-templates'));
    const wizard = await screen.findByTestId('beats-template-wizard');
    fireEvent.click(within(wizard).getAllByTestId('beats-template-card').find((c) => c.dataset.key === 'custom:77')!);
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → length
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // → mode (beats exist)
    fireEvent.click(within(wizard).getByTestId('beats-template-mode-append'));
    fireEvent.click(within(wizard).getByTestId('beats-template-next')); // Generate

    expect(h.onApplyTemplate).toHaveBeenCalledTimes(1);
    const [beats] = h.onApplyTemplate.mock.calls[0];
    expect(beats).toHaveLength(2);
    // Custom beats carry the user's own title and no methodology role.
    expect(beats[0].title).toBe('Hook');
    expect(beats[0].beat_role).toBeNull();
    expect(beats[1].title).toBe('Payoff');
  });

  it('deletes a custom template on a two-click confirm', async () => {
    const h = renderArr([beat({ id: 'a', start_sec: 0, duration_sec: 30 })], 600, {
      customTemplates: [customTpl({ id: '77', name: 'My Two-Beater' })],
    });
    fireEvent.click(screen.getByTestId('arr-templates'));
    const wizard = await screen.findByTestId('beats-template-wizard');
    // First click arms the confirm; nothing deleted yet.
    fireEvent.click(within(wizard).getByTestId('beats-template-custom-delete'));
    expect(h.onDeleteCustomTemplate).not.toHaveBeenCalled();
    // Second click on the armed button deletes.
    fireEvent.click(within(wizard).getByTestId('beats-template-custom-delete-confirm'));
    expect(h.onDeleteCustomTemplate).toHaveBeenCalledWith('77');
  });

  it('save-as-template is disabled with no arranged beats and opens the name modal otherwise', () => {
    // Empty script → the topbar Save button lives only in the populated view, so
    // render with an arranged beat and drive the toolbar button.
    const h = renderArr([beat({ id: 'a', start_sec: 60, duration_sec: 120 })], 600);
    const saveBtn = screen.getByTestId('arr-save-template');
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    const modal = screen.getByTestId('beats-save-template-modal');
    fireEvent.change(within(modal).getByTestId('beats-save-template-name'), {
      target: { value: 'My Custom Sheet' },
    });
    fireEvent.click(within(modal).getByTestId('beats-save-template-save'));

    expect(h.onSaveTemplate).toHaveBeenCalledTimes(1);
    const [name, anchors] = h.onSaveTemplate.mock.calls[0];
    expect(name).toBe('My Custom Sheet');
    // Anchors reverse-computed from the arranged beat against the 600s total.
    expect(anchors).toEqual([
      { title: 'Setup', summary: null, pctStart: 10, pctEnd: 30, color: null },
    ]);
  });

  it('disables save-as-template when only unarranged (tray) beats exist', () => {
    // A single tray beat (start_sec null) → the populated topbar renders, but
    // nothing is arranged, so there is no geometry to snapshot → button disabled.
    renderArr([beat({ id: 'tray', start_sec: null })], 600);
    expect(screen.getByTestId('arr-save-template')).toBeDisabled();
  });
});

