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
  { name: 'codex-image', display_name: 'GPT Image 2 (Codex)' },
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
    const opt = screen.getByRole('button', { name: /GPT Image 2/ });
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
