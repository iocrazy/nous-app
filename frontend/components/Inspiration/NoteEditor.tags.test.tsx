// NoteEditor inline #tag autocomplete — chips render on prefix match, click
// completes the tag and emits the updated markdown. Mirrors the old textarea
// composer's suggestion behavior (frontend/components/Inspiration/Composer.tsx)
// but driven through real TipTap editor transactions instead of DOM input
// events (jsdom cannot simulate ProseMirror keystrokes).
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NoteEditor } from './NoteEditor';

// Same jsdom/ProseMirror layout polyfills as NoteEditor.test.tsx — required
// for the editor to mount and report selection geometry.
const zeroRect = {
  bottom: 0,
  height: 0,
  left: 0,
  right: 0,
  toJSON: () => ({}),
  top: 0,
  width: 0,
  x: 0,
  y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

function getEditorInstance() {
  return (window as unknown as Record<string, unknown>).__noteEditorInstance as {
    commands: { insertContent: (content: string) => boolean; focus: (pos?: 'start' | 'end') => boolean };
  };
}

describe('NoteEditor inline #tag autocomplete', () => {
  it('typing # shows prefix-matched suggestions; click completes the tag', async () => {
    const onChange = vi.fn();
    render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks', 'formats']} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    const ed = getEditorInstance();
    act(() => {
      ed.commands.focus('end');
      ed.commands.insertContent('#ho');
    });
    expect(await screen.findByText('#hooks')).toBeTruthy();
    fireEvent.click(screen.getByText('#hooks'));
    expect(onChange.mock.calls.at(-1)?.[0]).toContain('#hooks ');
    expect(screen.queryByText('#formats')).toBeNull();
  });

  it('no suggestions render until a tag prefix is active', async () => {
    const onChange = vi.fn();
    render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks', 'formats']} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    expect(screen.queryByText('#hooks')).toBeNull();
    expect(screen.queryByText('#formats')).toBeNull();
  });

  it('exact-match prefix does not render itself as a suggestion', async () => {
    const onChange = vi.fn();
    render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks']} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    const ed = getEditorInstance();
    act(() => {
      ed.commands.focus('end');
      ed.commands.insertContent('#hooks');
    });
    await waitFor(() => expect(document.querySelector('.ProseMirror')?.textContent).toContain('#hooks'));
    expect(screen.queryByText('#hooks', { selector: 'button' })).toBeNull();
  });

  it('does not show suggestions for a # typed inside a code block', async () => {
    const onChange = vi.fn();
    render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks', 'formats']} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    const ed = getEditorInstance() as unknown as {
      commands: { insertContent: (c: string) => boolean; focus: (pos?: 'start' | 'end') => boolean };
      chain: () => { focus: () => { toggleCodeBlock: () => { run: () => boolean } } };
    };
    act(() => {
      ed.commands.focus('end');
      ed.chain().focus().toggleCodeBlock().run();
      ed.commands.insertContent('#ho');
    });
    await waitFor(() => expect(document.querySelector('.ProseMirror pre code')?.textContent).toContain('#ho'));
    expect(screen.queryByText('#hooks')).toBeNull();
  });

  it('suggestions hide on blur', async () => {
    const onChange = vi.fn();
    render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks', 'formats']} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    const ed = getEditorInstance();
    act(() => {
      ed.commands.focus('end');
      ed.commands.insertContent('#ho');
    });
    expect(await screen.findByText('#hooks')).toBeTruthy();
    const pm = document.querySelector('.ProseMirror') as HTMLElement;
    fireEvent.blur(pm);
    await waitFor(() => expect(screen.queryByText('#hooks')).toBeNull());
  });
});
