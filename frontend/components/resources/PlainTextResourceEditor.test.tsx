// frontend/components/resources/PlainTextResourceEditor.test.tsx
// Real TipTap in jsdom — mirror NoteEditor.test.tsx's layout polyfills.
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { PlainTextResourceEditor } from './PlainTextResourceEditor';

const zeroRect = { bottom: 0, height: 0, left: 0, right: 0, toJSON: () => ({}), top: 0, width: 0, x: 0, y: 0 };
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (!('Range' in globalThis)) {
  // jsdom provides Range; keep this guard harmless.
}

describe('PlainTextResourceEditor', () => {
  it('renders the initial text content into a code block', () => {
    const { container } = render(
      <PlainTextResourceEditor value={'PORT=8080\nHOST=localhost'} ext="env" />,
    );
    // The raw text lands verbatim in a <pre>/<code> (ProseMirror code block).
    expect(container.querySelector('pre')?.textContent).toBe('PORT=8080\nHOST=localhost');
  });

  it('is read-only when readOnly is set (no contenteditable=true)', () => {
    const { container } = render(
      <PlainTextResourceEditor value={'x'} ext="txt" readOnly />,
    );
    const editable = container.querySelector('[contenteditable="true"]');
    expect(editable).toBeNull();
  });
});
