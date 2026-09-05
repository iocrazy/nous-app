import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
// The real `t` interpolates `{{name}}` / `{{count}}` from the options object,
// so a mock that hands back the raw defaultValue would render "Insert slide
// {{name}}" where the app renders a filename — a mock that disagrees with the
// boundary it stands in for. Same shape as LibraryGrid.test.tsx next door.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : ((d as { defaultValue?: string } | undefined)?.defaultValue ?? k).replace(
            /\{\{(\w+)\}\}/g,
            (_m: string, n: string) => String((d as Record<string, unknown> | undefined)?.[n] ?? ''),
          ),
  }),
}));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { LibraryPromptList } from './LibraryPromptList';
import type { PromptEntry } from '../../../services/promptsService';

const base = { tags: [], positive_zh: null, negative_en: null, negative_zh: null, params: null, slides: null, updated_at: '' };
const items: PromptEntry[] = [
  { ...base, key: 'template:1', form: 'template', origin: 'typed', title: 'Rain', positive_en: 'hero close-up', thumbs: [], source: { store: 'assets', id: '1' } },
  { ...base, key: 'album:7', form: 'album', origin: 'extracted', title: 'Harvest', positive_en: 'winking', thumbs: [{ url: '/api/v1/media/9/slides/002.jpg', kind: 'image' }], slides: [{ name: '002.jpg', url: null, positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null }, { name: '003.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null }], source: { store: 'uploads', id: '7' } },
  { ...base, key: 'image:11', form: 'image', origin: 'captioned', title: 'Courtyard', positive_en: 'a young woman', thumbs: [{ url: '/api/v1/resources/11/cover', kind: 'image' }], source: { store: 'uploads', id: '11' } },
];

describe('LibraryPromptList', () => {
  it('renders a row per entry with form tag, thumb state and muted captioned', () => {
    render(<LibraryPromptList items={items} activeKey="album:7" onActivate={() => {}} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    const rows = screen.getAllByTestId('library-prompt-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelector('[data-empty="true"]')).not.toBeNull();       // template with no pictures
    expect(rows[1]).toHaveAttribute('data-active', 'true');
    expect(rows[1].textContent).toContain('1 of 2 slides with text');
    expect(rows[2].className).toContain('text-canvas-muted');
  });
  it('activates on click and shows error/empty states', () => {
    const onActivate = vi.fn();
    const { rerender } = render(<LibraryPromptList items={items} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    fireEvent.click(screen.getAllByTestId('library-prompt-row')[0]);
    expect(onActivate).toHaveBeenCalledWith('template:1');
    rerender(<LibraryPromptList items={[]} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={new Error('x')} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    rerender(<LibraryPromptList items={[]} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    expect(screen.getByText('Nothing Here Yet')).toBeInTheDocument();
  });
});
