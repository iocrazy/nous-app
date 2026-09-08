/**
 * Every canvas island that centres itself or anchors to the right edge has to
 * CONSUME the Library reservation the surface root publishes.
 *
 * Publishing it and consuming it are two separate failures, and only the
 * second one is what the user sees: `CanvasPage.libraryInset.test.tsx` proves
 * the variables exist, this file proves the islands read them. Before the fix
 * the composer was `inset-x-0 mx-auto` and the Arrange button / save badge
 * were `right-4`, so opening the panel put all three underneath it — the
 * reported symptom was the Prompts page's "Save current… / New…" footer
 * sitting on top of the composer's Group / Export / Import row.
 *
 * Class strings are the assertion because jsdom has no Tailwind: nothing here
 * computes a layout, so the honest thing to check is that the declaration
 * mentions the variable. A rename cannot drift past this file — the variable
 * names are imported, not typed out.
 */

import React from 'react';
import { render, cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: '/test', state: null }),
  useNavigate: () => vi.fn(),
}));

import { CanvasComposer } from '../smart/CanvasComposer';
import { ArrangeSelectedButton } from './ArrangeSelectedButton';
import { TopNodeBar } from './TopNodeBar';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { INSET_BOTTOM_VAR, INSET_RIGHT_VAR } from '../library/libraryInset';

const RIGHT = `var(${INSET_RIGHT_VAR}`;
const BOTTOM = `var(${INSET_BOTTOM_VAR}`;

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('canvas islands stop where the Library panel starts', () => {
  it('the composer centres in what is left, and clears a bottom drawer', () => {
    useCanvasCoreStore.setState({ kind: 'smart', nodes: [], connections: [], selection: [] });
    render(<CanvasComposer />);

    const composer = screen.getByRole('toolbar', { name: 'Smart canvas composer' });
    expect(composer.className).toContain(RIGHT);
    expect(composer.className).toContain(BOTTOM);
    // `inset-x-0` is what centred it across the WHOLE surface. Leaving it in
    // beside the new right anchor would silently win the cascade back.
    expect(composer.className).not.toMatch(/\binset-x-0\b/);
  });

  it('the Arrange button stops beside the panel instead of behind it', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        { id: 'a', type: 'prompt', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'prompt', position: { x: 10, y: 10 }, data: {} },
      ],
      connections: [],
      selection: ['a', 'b'],
    } as never);
    render(<ArrangeSelectedButton />);

    const btn = screen.getByTestId('arrange-selected-btn');
    expect(btn.className).toContain(RIGHT);
    expect(btn.className).toContain(BOTTOM);
    expect(btn.className).not.toMatch(/\bright-4\b/);
  });

  it('the top node bar centres in what is left', () => {
    useCanvasCoreStore.setState({ kind: 'smart', nodes: [], connections: [], selection: [] });
    render(<TopNodeBar surfaceRef={React.createRef<HTMLDivElement>()} />);

    const bar = screen.getByTestId('top-node-bar');
    expect(bar.className).toContain(RIGHT);
    // Centring by transform is centring on the full width by definition — the
    // reservation cannot reach a `-translate-x-1/2`.
    expect(bar.className).not.toMatch(/-translate-x-1\/2/);
  });
});
