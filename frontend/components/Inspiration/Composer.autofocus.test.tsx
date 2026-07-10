import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const createNote = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { Composer } from './Composer';

describe('Composer autoFocus', () => {
  it('autoFocus focuses the textarea with caret at start', () => {
    render(
      <Composer
        onCreated={vi.fn()}
        tagSuggestions={[]}
        autoFocus
        prefill={{ content: '\n\n#food #shortform', refHotspot: { title: 'x' } }}
      />,
    );
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(document.activeElement).toBe(ta);
    expect(ta.selectionStart).toBe(0);
    expect(ta.value).toContain('#food #shortform');
  });
});
