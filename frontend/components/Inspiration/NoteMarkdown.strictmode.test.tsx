// Regression test for a StrictMode-only bug: the checkbox index used to come
// from a mutable per-render `useRef` counter, reset once per NoteMarkdown
// render and incremented once per `input` renderer invocation. In
// React.StrictMode, React double-invokes each child component function to
// surface impure renders; the `input` renderer is its own fiber, so its
// second invocation kept incrementing the *same* shared counter instead of
// re-reading a stable value, producing indices like [1, 3, 5] instead of
// [0, 1, 2]. Production builds don't double-invoke, so this was invisible
// outside dev/QA — but dev is exactly where this note-taking flow gets used
// and clicking checkbox #1 would flip the wrong task.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { NoteMarkdown } from './NoteMarkdown';

describe('NoteMarkdown under React.StrictMode', () => {
  it('reports stable document-order indices for sequential checkbox clicks', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <React.StrictMode>
        <NoteMarkdown source={'- [ ] a\n- [ ] b\n- [ ] c'} onToggleTask={onToggleTask} />
      </React.StrictMode>,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    expect(boxes.length).toBe(3);

    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    fireEvent.click(boxes[2]);

    expect(onToggleTask.mock.calls).toEqual([[0], [1], [2]]);
  });
});
