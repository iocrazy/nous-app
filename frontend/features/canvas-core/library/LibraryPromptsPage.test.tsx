import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../smart/canvasScope', () => ({ useCanvasScope: () => ({ scopeId: '9000', resPath: (p: string) => p }) }));
const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({ useOptionalToast: () => ({ addToast }) }));
const fetchPrompts = vi.fn();
const fetchPromptCounts = vi.fn();
const saveAsTemplate = vi.fn<(...a: unknown[]) => Promise<{ assetId: string }>>(async () => ({ assetId: '900' }));
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a), fetchPromptCounts: (...a: unknown[]) => fetchPromptCounts(...a), saveAsTemplate: (...a: unknown[]) => saveAsTemplate(...a) }));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useLibraryStore } from './libraryStore';
import { registerMentionHandle } from './mentionHandles';
import { LibraryPromptsPage } from './LibraryPromptsPage';
import type { PromptEntry } from '../../../services/promptsService';
import type { PromptNodeData } from '../smart/types';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: ['Lighting'], positive_en: 'cheerful', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080 }, thumbs: [], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const page = { items: [image], total: 1, by_form: { template: 0, image: 1, album: 0 }, by_origin: { typed: 0, extracted: 1, captioned: 0 } };
const node = (data: Partial<PromptNodeData>) => ({ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: '', gen: null, ...data } });
const target = { nodeId: 'p1', kind: 'prompt' as const, title: 'Prompt' };

function mount(data: Partial<PromptNodeData>, tgt = target) {
  useCanvasCoreStore.setState({ nodes: [node(data)] as never, projectId: null, readOnly: false } as never);
  return render(<LibraryPromptsPage target={tgt} targetData={useCanvasCoreStore.getState().nodes[0].data as PromptNodeData} />);
}

describe('LibraryPromptsPage', () => {
  beforeEach(() => {
    fetchPrompts.mockReset().mockResolvedValue(page);
    fetchPromptCounts.mockReset().mockResolvedValue({ mine: 1, project: null, system: 0 });
    saveAsTemplate.mockClear(); addToast.mockClear();
    useLibraryStore.setState({ open: true, page: 'prompts', query: '', promptSegment: 'mine', promptForm: null, promptLang: 'en' } as never);
  });

  it('hides the System segment when there are no presets and shows counts on Mine', async () => {
    mount({});
    await screen.findByTestId('library-prompt-row');
    expect(screen.getByRole('button', { name: 'Mine 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /System/ })).toBeNull();
  });

  it('Insert positive goes through the editor handle and never touches the body text', async () => {
    const insertText = vi.fn();
    const undo = registerMentionHandle('p1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    mount({ body: 'draft @' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' }));
    expect(insertText).toHaveBeenCalledWith('cheerful');
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('draft @');
    undo();
  });

  it('Insert positive falls back to appending when the editor is not mounted', async () => {
    mount({ body: 'draft @' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' }));
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('draft @\ncheerful');
    expect(addToast).toHaveBeenCalledWith('Added to the end — the card was off screen', 'info');
  });

  it('Apply all onto a non-empty body asks first, then replaces body/negative/ratio in one write', async () => {
    mount({ body: 'old', gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(screen.getByTestId('library-prompt-confirm')).toHaveTextContent('Replace the body of');
    const before = useCanvasCoreStore.getState().historyPast.length;
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    const data = (useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data;
    expect(data.body).toBe('cheerful'); expect(data.negative_body).toBe('flare'); expect(data.gen?.ratio).toBe('16:9');
    // ONE history entry, not two — but the store commits its undo snapshot on a
    // 250ms debounce, so the count is read after that commit rather than in the
    // same tick as the click.
    await waitFor(() => expect(useCanvasCoreStore.getState().historyPast.length).toBe(before + 1));
  });

  it('Apply all onto an empty body needs no confirmation', async () => {
    mount({ body: '' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(screen.queryByTestId('library-prompt-confirm')).toBeNull();
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('cheerful');
  });

  it('no target: actions disabled and the consequence line says so', async () => {
    mount({}, null as never);
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    expect(screen.getByRole('button', { name: 'Insert positive' })).toBeDisabled();
    expect(screen.getByTestId('library-consequence')).toHaveTextContent('Select a prompt node to insert or apply');
  });

  it('Save current prefills from the node and saves a template without pictures', async () => {
    mount({ body: 'my prompt', negative_body: 'ugly' });
    await screen.findByTestId('library-prompt-row');
    fireEvent.click(screen.getByRole('button', { name: 'Save current…' }));
    expect(screen.getByLabelText('Positive')).toHaveValue('my prompt');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Mine 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(saveAsTemplate).toHaveBeenCalledWith('9000', expect.objectContaining({ title: 'Mine 1', positive: 'my prompt', negative: 'ugly', exampleResourceIds: [] })));
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2)); // reload after save
  });

  // Ruling R17: the preview's per-slide button changes the selection AND acts
  // in the same tick, so the page must insert the slide it was HANDED. A page
  // that read its own `slideName` state here would insert slide 1 — the one
  // selected a moment ago — and no assertion on the default slide would notice.
  it('a per-slide Insert acts on the slide it was handed, not the one selected a tick ago', async () => {
    const slide = (name: string, positive: string) => ({ name, url: null, positive_en: positive, positive_zh: null, negative_en: null, negative_zh: null });
    const album: PromptEntry = { ...image, key: 'album:20', form: 'album', title: 'Trip', slides: [slide('01.jpg', 'first slide'), slide('02.jpg', 'second slide')] };
    fetchPrompts.mockResolvedValue({ ...page, items: [album], by_form: { template: 0, image: 0, album: 1 } });
    const insertText = vi.fn();
    const undo = registerMentionHandle('p1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    mount({});
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    const rows = await screen.findAllByTestId('library-prompt-slide');
    fireEvent.click(rows[1].querySelector('button')!);
    expect(insertText).toHaveBeenCalledWith('second slide');
    undo();
  });

  it('keyboard: ArrowDown moves, Enter inserts, Escape cancels an open form before closing', async () => {
    fetchPrompts.mockResolvedValue({ ...page, items: [image, { ...image, key: 'image:11', title: 'Second', positive_en: 'second' }], total: 2 });
    const insertText = vi.fn();
    const undo = registerMentionHandle('p1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    mount({});
    const root = (await screen.findAllByTestId('library-prompt-row'))[0].closest('[data-testid="library-prompts-page"]')!;
    // Two presses: the first selects row 0 from no selection at all (ruling R1).
    fireEvent.keyDown(root, { key: 'ArrowDown' });
    fireEvent.keyDown(root, { key: 'ArrowDown' });
    fireEvent.keyDown(root, { key: 'Enter' });
    expect(insertText).toHaveBeenCalledWith('second');
    fireEvent.click(screen.getByRole('button', { name: 'New…' }));
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(screen.queryByTestId('template-form')).toBeNull();
    expect(useLibraryStore.getState().open).toBe(true);
    undo();
  });
});
