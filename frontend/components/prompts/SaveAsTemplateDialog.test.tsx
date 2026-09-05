import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
// Interpolating `t`, copied from the sibling TemplateForm.test.tsx: the toast
// assertion below wants the CODE, and a passthrough mock would hand back a
// literal `{{code}}` that real i18next never renders.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => {
      const text = typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k;
      if (typeof d !== 'object' || d === null) return text;
      const opts = d as Record<string, unknown>;
      return text.replace(/\{\{(\w+)\}\}/g, (m, name: string) => (name in opts ? String(opts[name]) : m));
    },
  }),
}));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useOptionalToast: () => ({ addToast }) }));
const saveAsTemplate = vi.fn(async (..._a: unknown[]) => ({ assetId: '900' }));
vi.mock('../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../services/promptsService')>()), saveAsTemplate: (...a: unknown[]) => saveAsTemplate(...a) }));
import { SaveAsTemplateDialog, initialTemplateValue } from './SaveAsTemplateDialog';
import type { PromptEntry } from '../../services/promptsService';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'p', positive_zh: null, negative_en: 'n', negative_zh: null, params: null, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const album: PromptEntry = { ...image, key: 'album:7', form: 'album', title: 'Harvest', source: { store: 'uploads', id: '7' }, slides: [
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '003.jpg', url: '/api/v1/media/9/slides/003.jpg', positive_en: 'leaning', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '005.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
] };

describe('initialTemplateValue', () => {
  it('prefills from an image and ticks its one picture', () => {
    expect(initialTemplateValue(image, undefined, 'en')).toEqual({ title: 'Bicycle', group: '', positive: 'p', negative: 'n', exampleIds: ['10'] });
  });
  it('joins ticked slides as numbered lines; examples is the album resource', () => {
    expect(initialTemplateValue(album, ['002.jpg', '003.jpg'], 'en')).toEqual({ title: 'Harvest', group: '', positive: '1. winking\n2. leaning', negative: '', exampleIds: ['7'] });
  });
});

describe('SaveAsTemplateDialog', () => {
  it('saves and reports the new asset id', async () => {
    const onSaved = vi.fn();
    render(<SaveAsTemplateDialog scopeId="9000" entry={image} lang="en" onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('900'));
    expect(saveAsTemplate).toHaveBeenCalledWith('9000', expect.objectContaining({ title: 'Bicycle', positive: 'p', exampleResourceIds: ['10'] }));
    expect(addToast).toHaveBeenCalledWith('Saved to Mine', 'success');
  });
  it('refuses an empty title or positive without calling the API', () => {
    render(<SaveAsTemplateDialog scopeId="9000" entry={{ ...image, title: '', positive_en: null }} lang="en" onClose={() => {}} onSaved={() => {}} />);
    expect(screen.getByRole('button', { name: 'Save to Mine' })).toBeDisabled();
  });
  it('a failed save stays open and toasts the error code', async () => {
    saveAsTemplate.mockRejectedValueOnce(Object.assign(new Error('nope'), { code: 'duplicate_name' }));
    const onSaved = vi.fn();
    render(<SaveAsTemplateDialog scopeId="9000" entry={image} lang="en" onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('Could not save: duplicate_name', 'error'));
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByTestId('template-form')).toBeInTheDocument();
  });
});
