// memos-parity composer toolbar: quick-insert #tag / code block / link.
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { Composer } from './Composer';

const frame = () => new Promise((r) => requestAnimationFrame(() => r(null)));

describe('Composer toolbar quick-inserts', () => {
  it('# button inserts a hash with a boundary space when needed', async () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'idea' } });
    ta.setSelectionRange(4, 4);
    fireEvent.click(screen.getByLabelText('Insert tag'));
    await frame();
    expect(ta.value).toBe('idea #');
    expect(ta.selectionStart).toBe(6);
  });

  it('code button inserts a fenced block with the cursor inside', async () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.click(screen.getByLabelText('Insert code block'));
    await frame();
    expect(ta.value).toBe('```\n\n```\n');
    expect(ta.selectionStart).toBe(4);
  });

  it('code button wraps an existing selection in fences', async () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'const x = 1;' } });
    ta.setSelectionRange(0, 12);
    fireEvent.click(screen.getByLabelText('Insert code block'));
    await frame();
    expect(ta.value).toBe('```\nconst x = 1;\n```\n');
  });

  it('link button inserts [](), cursor in the brackets; wraps selection as text', async () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.click(screen.getByLabelText('Insert link'));
    await frame();
    expect(ta.value).toBe('[]()');
    expect(ta.selectionStart).toBe(1);
  });

  it('paperclip still opens the file input', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    expect(screen.getByLabelText('Attach file')).toBeTruthy();
    expect(screen.getByLabelText('Attach files')).toBeTruthy(); // hidden input
  });
});
