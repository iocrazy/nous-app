/**
 * Apply must never fail silently.
 *
 * Reported: "I drew with the brush and then Apply Brush does nothing."
 *
 * The brush path is four optional links deep —
 *   `paintRef.current?.exportComposite()` → `if (blob)` → `onBrushCommit?.()`
 * — and every one of them fails by doing nothing at all. Whatever the cause on
 * a given machine (canvas taint, a missing handler, an export that returns
 * null), the user sees an identical dead button and we get no signal back.
 *
 * This is the "typed failure feedback" rule from CLAUDE.md: a user action that
 * cannot complete must SAY so. It also turns the next report into a sentence
 * we can act on instead of "the button doesn't work".
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../smart/mediaUrl', () => ({ mediaSrc: (u: string) => u }));

// A paint canvas whose export fails the way a tainted one does.
let exportResult: Blob | null = null;
vi.mock('./PaintCanvas', async () => {
  const React = await import('react');
  return {
    PaintCanvas: React.forwardRef(function PaintCanvasStub(
      props: { onHistoryChange?: (canUndo: boolean, canRedo: boolean) => void },
      ref: React.Ref<unknown>,
    ) {
      // Report a stroke ONCE so Apply is enabled. `props` is a fresh object
      // every render, so depending on it here re-fires the parent's setState
      // forever — mount-only is what the real component does too.
      const notified = React.useRef(false);
      React.useEffect(() => {
        if (notified.current) return;
        notified.current = true;
        props.onHistoryChange?.(true, false);
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
      React.useImperativeHandle(ref, () => ({
        undo: () => {},
        redo: () => {},
        clear: () => {},
        isEmpty: () => false,
        exportComposite: async () => exportResult,
      }));
      return React.createElement('div', { 'data-testid': 'paint-stub' });
    }),
  };
});

import { UnifiedImageEditor } from './UnifiedImageEditor';

function openBrush(onBrushCommit = vi.fn()) {
  render(
    <UnifiedImageEditor
      open
      src="/img.png"
      alt="x"
      onClose={() => {}}
      onBrushCommit={onBrushCommit}
    />,
  );
  fireEvent.click(screen.getByTestId('editor-tab-brush'));
  return onBrushCommit;
}

describe('Apply feedback', () => {
  it('tells the user when the brush export produced nothing', async () => {
    exportResult = null;
    openBrush();

    fireEvent.click(screen.getByTestId('editor-apply'));

    const alert = await screen.findByRole('alert');
    expect(
      alert.textContent,
      'no visible reason was given — the button just looks broken',
    ).toBeTruthy();
  });

  it('does not show an error when the export succeeds', async () => {
    exportResult = new Blob(['x'], { type: 'image/png' });
    const onBrushCommit = openBrush();

    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(onBrushCommit).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('clears a previous error once a later apply works', async () => {
    exportResult = null;
    const onBrushCommit = openBrush();
    fireEvent.click(screen.getByTestId('editor-apply'));
    await screen.findByRole('alert');

    exportResult = new Blob(['x'], { type: 'image/png' });
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(onBrushCommit).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
