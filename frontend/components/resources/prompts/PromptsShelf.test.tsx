import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../../contexts/ResourcesContext', () => ({ useResourcesContext: () => ({ scopeId: '9000', resPath: (p: string) => p, refreshAssetCounts: vi.fn() }) }));
vi.mock('../SendToCanvasModal', () => ({ SendToCanvasModal: (p: { positive: string }) => <div data-testid="send-modal">{p.positive}</div> }));
vi.mock('../../prompts/SaveAsTemplateDialog', () => ({ SaveAsTemplateDialog: () => <div data-testid="save-dialog" /> }));
const fetchPrompts = vi.fn();
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a) }));

import { PromptsShelf } from './PromptsShelf';

const image = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'cheerful woman, oranges', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080, steps: 28 }, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '2026-09-03T00:00:00Z' };
const captioned = { ...image, key: 'image:11', origin: 'captioned', title: 'Courtyard', params: null, positive_en: 'A young woman', source: { store: 'uploads', id: '11' } };
const album = { ...image, key: 'album:7', form: 'album', title: 'Orange harvest', params: null, source: { store: 'uploads', id: '7' }, thumbs: [], slides: [
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '005.jpg', url: '/api/v1/media/9/slides/005.jpg', positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
] };
const page = { items: [image, captioned, album], total: 3, by_form: { template: 0, image: 2, album: 1 }, by_origin: { typed: 0, extracted: 1, captioned: 2 } };

function mount() {
  return render(<MemoryRouter initialEntries={['/resources/assets/prompt']}><PromptsShelf /></MemoryRouter>);
}

describe('PromptsShelf', () => {
  beforeEach(() => { fetchPrompts.mockReset(); fetchPrompts.mockResolvedValue(page); });

  it('renders text-first cards with form and origin counts', async () => {
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(screen.getByRole('button', { name: 'Images 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Captioned 2' })).toBeInTheDocument();
    expect(screen.getAllByTestId('prompt-card')).toHaveLength(3);
  });

  it('captioned card has no params row and is muted', async () => {
    mount();
    const card = (await screen.findByText('A young woman')).closest('[data-testid="prompt-card"]')!;
    expect(card).toHaveAttribute('data-origin', 'captioned');
    expect(card.querySelector('[data-testid="prompt-params"]')).toBeNull();
    expect(card.textContent).toContain('this text describes the picture');
  });

  it('album card expands to slides and disables Send on textless ones', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    const rows = screen.getAllByTestId('prompt-slide-row');
    expect(rows).toHaveLength(2);
    expect(rows[1].querySelector('button')).toBeDisabled();
    fireEvent.click(rows[0].querySelector('button')!);
    expect(screen.getByTestId('send-modal')).toHaveTextContent('winking');
  });

  it('form chip refetches with the filter and writes the URL', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Albums 1' }));
    await waitFor(() => expect(fetchPrompts).toHaveBeenLastCalledWith('9000', expect.objectContaining({ form: 'album' })));
  });

  it('a failed load is an error row with Retry, not an empty page', async () => {
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    mount();
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText('No prompts yet')).toBeNull();
  });

  it('true empty state names the three ways a prompt arrives', async () => {
    fetchPrompts.mockResolvedValueOnce({ items: [], total: 0, by_form: { template: 0, image: 0, album: 0 }, by_origin: { typed: 0, extracted: 0, captioned: 0 } });
    mount();
    expect(await screen.findByText(/No prompts yet/)).toBeInTheDocument();
  });
});
