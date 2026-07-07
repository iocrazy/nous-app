// Regression test for a GFM loose-list bug: when task-list items are
// separated by a blank line, CommonMark marks the whole list "loose" and
// wraps each item's inline content in a <p>, so the checkbox ends up nested
// as `li > p > input` instead of `li > input`. The old `li` renderer only
// scanned *direct* children (React.Children.map) for a checkbox to
// cloneElement a `taskIndex` prop onto, so a `<p>` direct child made the scan
// miss the nested `input` entirely — its `taskIndex` stayed `undefined`,
// which the `input` renderer treats as "not a task checkbox" and disables.
// Loose lists are extremely common in real notes (paragraphs separated by
// blank lines, pasted checklists), so this made checkboxes silently
// unclickable in a very ordinary case.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { NoteMarkdown } from './NoteMarkdown';

describe('NoteMarkdown with loose GFM task lists (blank line between items)', () => {
  it('renders all checkboxes enabled and reports correct document-order indices, under StrictMode', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <React.StrictMode>
        <NoteMarkdown source={'- [ ] a\n\n- [ ] b\n\n- [ ] c'} onToggleTask={onToggleTask} />
      </React.StrictMode>,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    expect(boxes.length).toBe(3);

    for (const box of Array.from(boxes)) {
      expect((box as HTMLInputElement).disabled).toBe(false);
    }

    fireEvent.click(boxes[1]);
    fireEvent.click(boxes[2]);

    expect(onToggleTask.mock.calls).toEqual([[1], [2]]);
  });
});
