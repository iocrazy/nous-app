/**
 * Apply must never fail silently — and must never SAVE the wrong thing
 * silently either.
 *
 * Reported: "I drew with the brush and then Apply Brush does nothing." Then,
 * with a screenshot: "the brushed result has no base image, only the strokes."
 *
 * The brush path is a chain of optional links; every one of them used to
 * fail by doing nothing. Worse, when the base image could not be read the
 * export quietly shipped the annotation layer alone and the editor saved THAT
 * as the result — an overlay on a transparent background, presented as the
 * user's picture.
 *
 * Contract: a failed export gets a visible reason; an export that could not
 * include the base image is NOT committed, and the reason says so.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../smart/mediaUrl', () => ({
  mediaSrc: (u: string) => u,
  fullResSrc: (u: string) => u,
  fullResPath: (u: string) => u,
}));

let exportResult: { blob: Blob | null; baseIncluded: boolean } = {
  blob: null,
  baseIncluded: false,
};
vi.mock('./PaintCanvas', async () => {
  const React = await import('react');
  return {
    PaintCanvas: React.forwardRef(function PaintCanvasStub(
      props: { onHistoryChange?: (canUndo: boolean, canRedo: boolean) => void },
      ref: React.Ref<unknown>,
    ) {
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

const png = () => new Blob(['x'], { type: 'image/png' });

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
    exportResult = { blob: null, baseIncluded: false };
    const onBrushCommit = openBrush();

    fireEvent.click(screen.getByTestId('editor-apply'));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent, 'no visible reason — the button just looks broken').toBeTruthy();
    expect(onBrushCommit).not.toHaveBeenCalled();
  });

  it('does NOT save an overlay-only export, and says why', async () => {
    // The base image could not be fetched. Saving the strokes on a transparent
    // background as "the result" is exactly the screenshot the user sent.
    exportResult = { blob: png(), baseIncluded: false };
    const onBrushCommit = openBrush();

    fireEvent.click(screen.getByTestId('editor-apply'));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/base image/i);
    expect(onBrushCommit, 'committed an overlay-only image').not.toHaveBeenCalled();
  });

  it('commits and shows no error when the base was included', async () => {
    exportResult = { blob: png(), baseIncluded: true };
    const onBrushCommit = openBrush();

    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(onBrushCommit).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('clears a previous error once a later apply works', async () => {
    exportResult = { blob: png(), baseIncluded: false };
    const onBrushCommit = openBrush();
    fireEvent.click(screen.getByTestId('editor-apply'));
    await screen.findByRole('alert');

    exportResult = { blob: png(), baseIncluded: true };
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(onBrushCommit).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
