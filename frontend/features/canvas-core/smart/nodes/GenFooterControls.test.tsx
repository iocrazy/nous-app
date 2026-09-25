/**
 * GenFooterControls — IC-parity footer pills (⑨): each control is a pill
 * that pops an upward panel (model list with a selected dot, ratio grid
 * with semantic labels, quality Auto/Low/Medium/High, count 1-8 grid).
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GenFooterControls } from './GenFooterControls';

afterEach(cleanup);

const MODELS = [
  { name: 'codex-image', display_name: 'GPT Image (Codex)' },
  { name: 'jimeng-cli-image', display_name: 'Dreamina Image' },
];

function renderBar(gen: Record<string, unknown>, onChange = vi.fn()) {
  render(
    <GenFooterControls
      gen={{ kind: 'image', model: '', ratio: '1:1', count: 1, ...gen } as never}
      models={MODELS as never}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe('GenFooterControls', () => {
  it('model pill pops a list; picking marks and patches', () => {
    const onChange = renderBar({});
    fireEvent.click(screen.getByTestId('pill-model'));
    // Rows read as the admin identifier (row name here: no actual_model).
    const opt = screen.getByRole('button', { name: /^codex-image/ });
    expect(screen.queryByText(/GPT Image \(Codex\)/)).toBeNull();
    fireEvent.click(opt);
    expect(onChange).toHaveBeenCalledWith({ model: 'codex-image' });
  });

  it('size pill pops the ratio grid with semantic labels', () => {
    const onChange = renderBar({});
    fireEvent.click(screen.getByTestId('pill-size'));
    expect(screen.getByText('Square')).toBeInTheDocument();
    expect(screen.getByText('Tall')).toBeInTheDocument();
    fireEvent.click(
      screen.getAllByTestId('ratio-option').find((b) => b.textContent?.includes('9:16'))!,
    );
    expect(onChange).toHaveBeenCalledWith({ ratio: '9:16' });
  });

  it('quality pill offers Auto/Low/Medium/High', () => {
    const onChange = renderBar({});
    fireEvent.click(screen.getByTestId('pill-quality'));
    const q = () => screen.getAllByTestId('quality-option');
    fireEvent.click(q().find((b) => b.textContent === 'Low')!);
    expect(onChange).toHaveBeenCalledWith({ quality: 'low' });
    // Auto clears the knob (provider default).
    fireEvent.click(screen.getByTestId('pill-quality'));
    fireEvent.click(q().find((b) => b.textContent === 'Auto')!);
    expect(onChange).toHaveBeenCalledWith({ quality: undefined });
  });

  it('count pill pops the 1-8 grid', () => {
    const onChange = renderBar({});
    fireEvent.click(screen.getByTestId('pill-count'));
    const buttons = screen.getAllByTestId('count-option');
    expect(buttons).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: '4' }));
    expect(onChange).toHaveBeenCalledWith({ count: 4 });
  });

  it('video kind: aspect pill, no quality/count', () => {
    renderBar({ kind: 'video', aspect: '16:9' });
    expect(screen.queryByTestId('pill-quality')).toBeNull();
    expect(screen.queryByTestId('pill-count')).toBeNull();
    fireEvent.click(screen.getByTestId('pill-size'));
    expect(
      screen.getAllByTestId('ratio-option').some((b) => b.textContent?.includes('16:9')),
    ).toBe(true);
  });

  it('only one popover open at a time; second click closes', () => {
    renderBar({});
    fireEvent.click(screen.getByTestId('pill-model'));
    fireEvent.click(screen.getByTestId('pill-size'));
    expect(screen.queryByRole('button', { name: /Dreamina/ })).toBeNull();
    expect(screen.getByText('Square')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pill-size'));
    expect(screen.queryByText('Square')).toBeNull();
  });
});

it('video kind shows a Duration pill and picks 10s', () => {
  const onChange = vi.fn();
  render(
    <GenFooterControls
      gen={{ kind: 'video', model: '', aspect: '16:9' }}
      onChange={onChange}
      models={[]}
    />,
  );
  fireEvent.click(screen.getByTestId('pill-duration'));
  fireEvent.click(screen.getByText('10s'));
  expect(onChange).toHaveBeenCalledWith({ duration: 10 });
});

it('video kind has a resolution pill and an Adaptive aspect option', () => {
  const onChange = vi.fn();
  render(
    <GenFooterControls
      gen={{ kind: 'video', model: '', aspect: '16:9' }}
      onChange={onChange}
      models={[]}
    />,
  );
  fireEvent.click(screen.getByTestId('pill-vres'));
  fireEvent.click(screen.getAllByTestId('vres-option')[2]); // 1080P
  expect(onChange).toHaveBeenCalledWith({ resolution: '1080p' });
  fireEvent.click(screen.getByTestId('pill-size'));
  fireEvent.click(screen.getByText('Adaptive'));
  expect(onChange).toHaveBeenCalledWith({ aspect: 'auto' });
});

// ── picker labels ────────────────────────────────────────────────────────────
// 2026-09-25: a platform row goes by exactly the identifier the admin entered
// (actual_model, else the row name — jimeng-local rows carry an empty
// actual_model in production). display_name is not shown at all: it was
// authored by migrations, the admin page never shows it, and the user asked
// for the admin names verbatim. The old "· local" suffix and the "(Local)"
// tag therefore cannot appear either.
import { modelLabel } from './GenFooterControls';

describe('modelLabel', () => {
  it('is actual_model, like the admin card, and never includes display_name', () => {
    const label = modelLabel({
      name: 'openai-image-flare',
      display_name: 'GPT Image 2.5 Flare (OpenAI API)',
      actual_model: 'gpt-image-2.5-flare',
    });
    expect(label).toBe('gpt-image-2.5-flare');
    expect(label).not.toContain('GPT Image 2.5 Flare');
  });
  it('falls back to the row name for a local row and never appends "· local"', () => {
    expect(
      modelLabel({ name: 'jimeng-local-image', display_name: 'Dreamina (Local)', actual_model: '', is_local: true }),
    ).toBe('jimeng-local-image');
    expect(modelLabel({ name: 'a-row', display_name: 'Something (本地)', is_local: true })).toBe('a-row');
    expect(
      modelLabel({ name: 'codex-local-image', display_name: 'GPT Image (Codex, local)', is_local: true }),
    ).toBe('codex-local-image');
    expect(modelLabel({ name: 'x-row' })).toBe('x-row');
  });
});

describe('GenFooterControls — model popover rows', () => {
  it('lists a local row by its admin identifier only, with no display name and no "· local"', () => {
    render(
      <GenFooterControls
        gen={{ kind: 'image', model: '', ratio: '1:1', count: 1 } as never}
        models={[{ name: 'codex-local-image', display_name: 'GPT Image (Codex, local)', is_local: true }] as never}
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('pill-model'));
    const row = screen.getByRole('button', { name: /^codex-local-image/ });
    expect(row.textContent).toBe('codex-local-image');
    expect(screen.queryByText(/GPT Image \(Codex, local\)/)).toBeNull();
    expect(screen.queryByText(/· local/)).toBeNull();
  });
});

describe('GenFooterControls — model not loaded on nous-engine', () => {
  const WITH_IDLE = [
    { name: 'codex-image', display_name: 'GPT Image (Codex)', last_test_status: 'ok' },
    { name: 'nous-studio-image', display_name: 'Studio Image', last_test_status: 'idle' },
  ];

  function renderIdle(gen: Record<string, unknown>, onChange = vi.fn()) {
    render(
      <GenFooterControls
        gen={{ kind: 'image', model: '', ratio: '1:1', count: 1, ...gen } as never}
        models={WITH_IDLE as never}
        onChange={onChange}
      />,
    );
    return onChange;
  }

  it('lists the idle model disabled, with the reason, and ignores a click on it', () => {
    const onChange = renderIdle({});
    fireEvent.click(screen.getByTestId('pill-model'));
    const row = screen.getByRole('button', { name: /^nous-studio-image/ }) as HTMLButtonElement;
    expect(row.disabled).toBe(true);
    expect(row.getAttribute('title')).toBe('Not loaded on nous-engine');
    expect(row.textContent).toContain('Not loaded on nous-engine');
    fireEvent.click(row);
    expect(onChange).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: /^codex-image/ }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('keeps a saved idle model on the pill, titled with the reason', () => {
    renderIdle({ model: 'nous-studio-image' });
    const pill = screen.getByTestId('pill-model');
    expect(pill.textContent).toContain('nous-studio-image');
    expect(pill.textContent).not.toContain('Studio Image');
    expect(pill.getAttribute('title')).toBe('Not loaded on nous-engine');
  });
});
