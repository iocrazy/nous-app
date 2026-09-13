/**
 * The form / origin markers on a prompt row.
 *
 * These went from words-in-a-pill to icons because the words wrapped: at a
 * ~300px card width the CJK labels broke mid-word (「单 图」, 「AI 打 标」)
 * and the card's top row grew a second storey. The risk in that change is
 * losing the NAME along with the text, so that is what most of these pin —
 * an icon with no accessible name is a marker only people who already know
 * the system can read.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: string) => d ?? k,
  }),
}));

import { FormTag, OriginTag } from './PromptTags';

describe('FormTag', () => {
  it.each([
    ['template', 'Template'],
    ['image', 'Image'],
    ['album', 'Album'],
  ] as const)('%s keeps its name reachable without printing it', (form, label) => {
    render(<FormTag form={form} />);
    const tag = screen.getByTestId('prompt-form-tag');
    expect(tag.getAttribute('aria-label')).toBe(label);
    expect(tag.getAttribute('title')).toBe(label);
    expect(tag.getAttribute('data-form')).toBe(form);
    // The point of the change: the word is not occupying the row.
    expect(tag.textContent).toBe('');
    expect(tag.querySelector('svg')).toBeTruthy();
  });

  it('draws each form differently — one glyph for all three would say nothing', () => {
    const { container: a } = render(<FormTag form="template" />);
    const { container: b } = render(<FormTag form="image" />);
    const { container: c } = render(<FormTag form="album" />);
    const html = [a, b, c].map((el) => el.innerHTML);
    expect(new Set(html).size).toBe(3);
  });
});

describe('OriginTag', () => {
  it.each([
    ['typed', 'Typed'],
    ['extracted', 'Extracted'],
    ['captioned', 'Captioned'],
  ] as const)('%s keeps its name reachable', (origin, label) => {
    render(<OriginTag origin={origin} params={null} />);
    expect(screen.getByTestId('prompt-origin-tag').getAttribute('title')).toBe(label);
    expect(screen.getByRole('img', { name: label })).toBeTruthy();
  });

  it('renders nothing at all when the origin is unknown', () => {
    render(<OriginTag origin={null} />);
    expect(screen.queryByTestId('prompt-origin-tag')).toBeNull();
  });

  // The tool name is DATA, not one of three known states — there is no glyph
  // for `gpt-6-astra`, and hiding it would drop the one thing on the row that
  // says which model to go back to.
  it('keeps the extracting tool as visible text beside the icon', () => {
    render(<OriginTag origin="extracted" params={{ tool: 'gpt-6-astra' }} />);
    const tag = screen.getByTestId('prompt-origin-tag');
    expect(tag.textContent).toBe('gpt-6-astra');
    expect(tag.getAttribute('title')).toBe('Extracted · gpt-6-astra');
  });

  it('falls back to the model when no tool is recorded', () => {
    render(<OriginTag origin="extracted" params={{ model: 'GhostMix-V2' }} />);
    expect(screen.getByTestId('prompt-origin-tag').textContent).toBe('GhostMix-V2');
  });

  // Reading the model name twice — once from the wrapper's label, once from
  // the text node — is what a single aria-label on the whole span would do.
  it('names the origin once, not the origin and the tool together', () => {
    render(<OriginTag origin="extracted" params={{ tool: 'gpt-6-astra' }} />);
    expect(screen.getByRole('img', { name: 'Extracted' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: /gpt-6-astra/ })).toBeNull();
  });

  // A picture's own prompt carries params, but they belong to the picture,
  // not to the person who typed it.
  it('shows no tool for typed or captioned rows even when params exist', () => {
    render(<OriginTag origin="captioned" params={{ tool: 'nous' }} />);
    expect(screen.getByTestId('prompt-origin-tag').textContent).toBe('');
  });
});
