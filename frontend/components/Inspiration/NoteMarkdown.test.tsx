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

describe('NoteMarkdown inline tag chips', () => {
  it('renders a tag-only line as a single chip without duplicating the raw text', () => {
    const { container } = render(<NoteMarkdown source={'#颜值'} onTagClick={vi.fn()} />);
    expect(screen.getByRole('button', { name: '#颜值' })).toBeTruthy();
    // exactly one occurrence of the tag text — the chip, not chip + leftover
    expect(container.textContent).toBe('#颜值');
  });

  it('keeps a mid-sentence tag inline with the surrounding text intact', () => {
    const { container } = render(<NoteMarkdown source={'look at #颜值 today'} onTagClick={vi.fn()} />);
    expect(screen.getByRole('button', { name: '#颜值' })).toBeTruthy();
    // chip lives inside the paragraph flow, surrounding words preserved
    expect(container.querySelector('p')?.textContent).toBe('look at #颜值 today');
  });

  it('does not chip a #word inside a fenced code block', () => {
    const { container } = render(<NoteMarkdown source={'```\n#nope in code\n```'} onTagClick={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
    // highlight may tokenize into spans, so read the raw text off the block
    expect(container.querySelector('pre')?.textContent).toContain('#nope in code');
  });

  it('does not chip a #word inside inline code', () => {
    const { container } = render(<NoteMarkdown source={'use `#nope` here'} onTagClick={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(container.querySelector('code')?.textContent).toBe('#nope');
  });

  it('clicking a chip fires onTagClick with the lowercased tag', () => {
    const onTagClick = vi.fn();
    render(<NoteMarkdown source={'idea #Hooks'} onTagClick={onTagClick} />);
    fireEvent.click(screen.getByRole('button', { name: '#Hooks' }));
    expect(onTagClick).toHaveBeenCalledWith('hooks');
  });

  it('renders a non-clickable styled span when no onTagClick is given', () => {
    render(<NoteMarkdown source={'idea #hooks'} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('#hooks').tagName).toBe('SPAN');
  });
});

describe('NoteMarkdown headings/blockquote styling', () => {
  it('## renders a styled h2, not body text', () => {
    const { container } = render(<NoteMarkdown source={'## Section title'} />);
    const h2 = container.querySelector('h2');
    expect(h2?.textContent).toBe('Section title');
    expect(h2?.className).toContain('font-bold');
  });

  it('> renders a styled blockquote', () => {
    const { container } = render(<NoteMarkdown source={'> quoted'} />);
    expect(container.querySelector('blockquote')?.className).toContain('border-l-2');
  });
});
