/**
 * The References card. The rules are tested in coverReferences.test.ts; this
 * file pins only what the RENDER is responsible for:
 *
 *   1. The person slot has no remove button. It is reserved by the style —
 *      offering a ✕ on it invites the user to create the exact state the
 *      style calls a blocker.
 *   2. The person slot only appears when the style asks for one. Always
 *      showing it would read as a bug in styles that never use it.
 *   3. A refused add SAYS WHICH refusal. "Nothing happened" after pressing Add
 *      is the failure mode this codebase keeps re-learning.
 *   4. At nine the Add tile is disabled rather than hidden, and the count turns
 *      red — a control that vanishes leaves the user hunting for it.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';
import { MAX_REFERENCES, type CoverReference } from './coverReferences';

vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CoverReferencePool } from './CoverReferencePool';

const gen = (n: number): string => `34158859979982${n}`;
const frame = (n: number, at = 3.1): CoverReference => ({
  kind: 'frame',
  genId: gen(n),
  url: `/api/v1/generated-media/${gen(n)}/cover`,
  timestampSeconds: at,
});
const template = (n: number): CoverReference => ({
  kind: 'template',
  genId: gen(n),
  url: `/api/v1/generated-media/${gen(n)}/cover`,
  label: 'Bold headline',
  templateId: `tpl-${n}`,
});
const person = (n: number): CoverReference => ({
  kind: 'person',
  genId: gen(n),
  url: `/api/v1/generated-media/${gen(n)}/cover`,
});

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

function renderPool(over: Partial<React.ComponentProps<typeof CoverReferencePool>> = {}) {
  const onRemove = vi.fn();
  const onAddFromTemplates = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <CoverReferencePool
        refs={[]}
        onRemove={onRemove}
        onAddFromTemplates={onAddFromTemplates}
        {...over}
      />
    </I18nextProvider>,
  );
  return { onRemove, onAddFromTemplates };
}

describe('CoverReferencePool', () => {
  it('counts frames and templates against ONE shared budget', () => {
    renderPool({ refs: [frame(1), template(2)] });

    expect(screen.getByTestId('cover-ref-count').textContent).toBe(
      `2 / ${MAX_REFERENCES}`,
    );
  });

  it('captions a frame with its timestamp', () => {
    renderPool({ refs: [frame(1, 66.42)] });

    expect(screen.getByText('1:06.4')).toBeTruthy();
  });

  it('captions a template with its saved name', () => {
    renderPool({ refs: [template(1)] });

    expect(screen.getByText('Bold headline')).toBeTruthy();
  });

  it('gives the person slot NO remove button', () => {
    renderPool({ refs: [person(1), frame(2)], requiresPerson: true });

    // Positive proof both tiles rendered, so the count below is meaningful.
    expect(screen.getByTestId('cover-ref-person')).toBeTruthy();
    expect(screen.getByText('0:03.1')).toBeTruthy();
    // Exactly one ✕ — the frame's. The person's slot is reserved by the style.
    expect(screen.getAllByLabelText('Remove reference')).toHaveLength(1);
  });

  it('shows the person slot as a blocker when the style needs one and it is missing', () => {
    renderPool({ refs: [], requiresPerson: true });

    const slot = screen.getByTestId('cover-ref-person');
    expect(slot.className).toContain('missing');
    expect(screen.getByText('not set')).toBeTruthy();
  });

  it('hides the person slot entirely for a style that does not ask for one', () => {
    renderPool({ refs: [frame(1)], requiresPerson: false });

    // Paired with proof the pool DID render, so this is not vacuous.
    expect(screen.getByTestId('cover-ref-pool')).toBeTruthy();
    expect(screen.queryByTestId('cover-ref-person')).toBeNull();
  });

  it('removes by generated-media id', () => {
    const { onRemove } = renderPool({ refs: [frame(7)] });

    fireEvent.click(screen.getByLabelText('Remove reference'));

    expect(onRemove).toHaveBeenCalledWith(gen(7));
  });

  it('disables the Add tile at nine instead of hiding it', () => {
    const full = Array.from({ length: MAX_REFERENCES }, (_, i) => frame(i));
    const { onAddFromTemplates } = renderPool({ refs: full });

    const add = screen.getByTestId('cover-ref-add-template') as HTMLButtonElement;
    // Still on screen — a control that vanishes leaves the user hunting.
    expect(add).toBeTruthy();
    expect(add.disabled).toBe(true);
    fireEvent.click(add);
    expect(onAddFromTemplates).not.toHaveBeenCalled();
  });

  it('turns the count red once the pool is full', () => {
    const full = Array.from({ length: MAX_REFERENCES }, (_, i) => frame(i));
    renderPool({ refs: full });

    expect(screen.getByTestId('cover-ref-count').className).toContain('bad');
  });

  it('says WHICH refusal happened — full', () => {
    renderPool({ refs: [frame(1)], refusal: 'full' });

    expect(screen.getByTestId('cover-ref-full')).toBeTruthy();
    expect(screen.queryByTestId('cover-ref-dup')).toBeNull();
  });

  it('says WHICH refusal happened — duplicate', () => {
    renderPool({ refs: [frame(1)], refusal: 'duplicate' });

    expect(screen.getByTestId('cover-ref-dup')).toBeTruthy();
    expect(screen.queryByTestId('cover-ref-full')).toBeNull();
  });

  it('shows no refusal box when the last add was fine', () => {
    renderPool({ refs: [frame(1)] });

    expect(screen.getByTestId('cover-ref-pool')).toBeTruthy();
    expect(screen.queryByTestId('cover-ref-full')).toBeNull();
    expect(screen.queryByTestId('cover-ref-dup')).toBeNull();
  });
});

describe('CoverReferencePool — upload (v4)', () => {
  it('offers Upload only when the parent can take a file, and hands it over', () => {
    const onUpload = vi.fn();
    renderPool({ refs: [], onUpload });
    const file = new File(['x'], 'ref.png', { type: 'image/png' });

    fireEvent.change(screen.getByTestId('cover-ref-upload-input'), {
      target: { files: [file] },
    });

    expect(onUpload).toHaveBeenCalledWith(file);
  });

  it('disables Upload at nine, like the template tile', () => {
    const refs = Array.from({ length: MAX_REFERENCES }, (_, i) => frame(i));
    renderPool({ refs, onUpload: vi.fn() });
    expect((screen.getByTestId('cover-ref-upload') as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows no Upload tile without a handler', () => {
    renderPool({ refs: [] });
    expect(screen.queryByTestId('cover-ref-upload')).toBeNull();
  });
});
