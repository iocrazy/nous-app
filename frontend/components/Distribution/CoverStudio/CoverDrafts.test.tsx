/**
 * The Drafts card. Four properties the render is responsible for:
 *
 *   1. The four drafts are ONE image with four hit areas, not four images. That
 *      is the skill's Core Rule (a round of ideas costs one generation), and
 *      rendering four <img> would both misrepresent the output and quadruple
 *      the requests.
 *   2. "Make #N the final cover" is unreachable until a draft is picked. A
 *      button that fires with nothing selected would redraw an arbitrary
 *      quadrant — the user gets a cover they did not choose.
 *   3. The prompt panel shows the SERVER's string verbatim. It exists so the
 *      user can see why a cover came out the way it did; a reconstructed one
 *      would answer that question wrongly.
 *   4. A failed generation shows the server's own sentence, not a generic one.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';

vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CoverDrafts } from './CoverDrafts';

const GRID = '/api/v1/generated-media/341582104263581/cover';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

function renderDrafts(over: Partial<React.ComponentProps<typeof CoverDrafts>> = {}) {
  const onSelect = vi.fn();
  const onRefine = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverDrafts
        stage="picking"
        gridUrl={GRID}
        selected={null}
        onSelect={onSelect}
        onRefine={onRefine}
        {...over}
      />
    </I18nextProvider>,
  );
  return { onSelect, onRefine };
}

describe('CoverDrafts', () => {
  it('renders the four drafts as ONE image with four hit areas', () => {
    renderDrafts();

    // Exactly one picture…
    const imgs = screen.getByTestId('cover-draft-grid').querySelectorAll('img');
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute('src')).toBe(`https://api.test${GRID}`);
    // …and four ways to pick a quadrant of it.
    for (const n of [1, 2, 3, 4]) {
      expect(screen.getByTestId(`cover-draft-${n}`)).toBeTruthy();
    }
  });

  it('reports which quadrant was clicked', () => {
    const { onSelect } = renderDrafts();

    fireEvent.click(screen.getByTestId('cover-draft-3'));

    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it('cannot make a final cover before a draft is picked', () => {
    // Firing with nothing selected would redraw an arbitrary quadrant and hand
    // the user a cover they did not choose.
    const { onRefine } = renderDrafts({ selected: null });

    const btn = screen.getByTestId('cover-make-final') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(onRefine).not.toHaveBeenCalled();
  });

  it('names the picked draft on the button', () => {
    renderDrafts({ selected: 2 });

    expect(screen.getByTestId('cover-make-final').textContent).toContain('#2');
  });

  it('cannot make a final cover before any drafts exist', () => {
    const { onRefine } = renderDrafts({ gridUrl: undefined, selected: 2, stage: 'idle' });

    expect((screen.getByTestId('cover-make-final') as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByTestId('cover-make-final'));
    expect(onRefine).not.toHaveBeenCalled();
  });

  it('says drafts are being drawn while stage 1 runs', () => {
    renderDrafts({ gridUrl: undefined, stage: 'drafting' });

    expect(screen.getByTestId('cover-draft-empty').textContent).toMatch(
      /Drawing four drafts/,
    );
  });

  it('shows the server prompt verbatim', () => {
    const prompt = 'Create one 2x2 preview grid image.\n\nTopic: 三分钟看懂内容差.';
    renderDrafts({ prompt });

    // Exact string — a panel that paraphrases what was sent answers "why did
    // the cover look like that" wrongly.
    expect(screen.getByTestId('cover-prompt-text').textContent).toBe(prompt);
  });

  it('hides the prompt panel when there is nothing to show', () => {
    renderDrafts({ prompt: undefined });

    // Paired with proof the card rendered, so this is not vacuous.
    expect(screen.getByTestId('cover-draft-grid')).toBeTruthy();
    expect(screen.queryByTestId('cover-prompt-text')).toBeNull();
  });

  it('surfaces the server sentence when a generation failed', () => {
    renderDrafts({
      gridUrl: undefined,
      stage: 'idle',
      error: 'no_credit: subscription quota exhausted',
    });

    expect(screen.getByTestId('cover-drafts-error').textContent).toContain('no_credit');
  });

  it('disables refine while a redraw is already running', () => {
    const { onRefine } = renderDrafts({ selected: 1, stage: 'refining' });

    expect((screen.getByTestId('cover-make-final') as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByTestId('cover-make-final'));
    expect(onRefine).not.toHaveBeenCalled();
  });
});


describe('CoverDrafts — progress and steps', () => {
  it('hides its own steps when told the stage already shows them', () => {
    renderDrafts({ showSteps: false });
    expect(document.querySelector('.cs-steps')).toBeNull();
  });

  it('runs an indeterminate bar when the engine gives no number', () => {
    renderDrafts({ stage: 'drafting', gridUrl: undefined, progressPercent: null });
    expect(document.querySelector('.cs-progress .bar')?.className).toContain('indeterminate');
    expect(screen.queryByTestId('cover-progress-percent')).toBeNull();
  });

  it('shows the percent and the clock when the engine gives a number', () => {
    renderDrafts({ stage: 'drafting', gridUrl: undefined, progressPercent: 62, elapsedSeconds: 17 });
    expect(screen.getByTestId('cover-progress-percent').textContent).toBe('62%');
    expect(screen.getByTestId('cover-progress-elapsed').textContent).toContain('17');
  });
});
