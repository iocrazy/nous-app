import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { NoteMarkdown } from './NoteMarkdown';

describe('NoteMarkdown', () => {
  it('renders gfm task lists as checkboxes', () => {
    const { container } = render(<NoteMarkdown source={'- [ ] todo\n- [x] done'} />);
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    expect(boxes.length).toBe(2);
    expect((boxes[1] as HTMLInputElement).checked).toBe(true);
  });

  it('checkboxes are disabled when no onToggleTask is given', () => {
    const { container } = render(<NoteMarkdown source={'- [ ] todo'} />);
    expect((container.querySelector('input[type="checkbox"]') as HTMLInputElement).disabled).toBe(true);
  });

  it('clicking the Nth checkbox calls onToggleTask with its document index', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <NoteMarkdown source={'- [ ] a\n- [ ] b\n- [ ] c'} onToggleTask={onToggleTask} />,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(boxes[2]);
    expect(onToggleTask).toHaveBeenCalledWith(2);
  });

  it('renders fenced code with a language class for highlighting', () => {
    const { container } = render(<NoteMarkdown source={'```js\nconst x = 1;\n```'} />);
    // rehype-highlight adds hljs + language-* classes onto the <code>
    expect(container.querySelector('code.hljs, code[class*="language-"]')).toBeTruthy();
  });

  it('renders a gfm table', () => {
    render(<NoteMarkdown source={'| a | b |\n|---|---|\n| 1 | 2 |'} />);
    expect(screen.getByRole('table')).toBeTruthy();
  });
});
