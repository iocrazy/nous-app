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

  // The list stays clickable while the confirm is open, and the sheet's copy
  // names only the NODE — so a sheet that applied "whatever is selected now"
  // would silently write a different prompt than the one it asked about.
  it('the confirm applies the entry it was opened for, not the row selected since', async () => {
    const other: PromptEntry = { ...image, key: 'image:11', title: 'Second', positive_en: 'second', negative_en: 'second neg', params: { width: 1000, height: 1000 } };
    fetchPrompts.mockResolvedValue({ ...page, items: [image, other], total: 2 });
    mount({ body: 'old', gen: { kind: 'image', model: '', ratio: '3:2', count: 1 } });
    const rows = await screen.findAllByTestId('library-prompt-row');
    fireEvent.click(rows[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    fireEvent.click(rows[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    const data = (useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data;
    expect(data.body).toBe('cheerful');
    expect(data.negative_body).toBe('flare');
    expect(data.gen?.ratio).toBe('16:9');
  });

  it('the confirm goes away when the target does, so Replace is never a dead button', async () => {
    const { rerender } = mount({ body: 'old' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(screen.getByTestId('library-prompt-confirm')).toBeInTheDocument();
    rerender(<LibraryPromptsPage target={null} targetData={null} />);
    expect(screen.queryByTestId('library-prompt-confirm')).toBeNull();
  });

  it('read-only says so, at both the consequence line and the preview hint, and withholds Save as template', async () => {
    useCanvasCoreStore.setState({ nodes: [node({})] as never, projectId: null, readOnly: true } as never);
    render(<LibraryPromptsPage target={target} targetData={useCanvasCoreStore.getState().nodes[0].data as PromptNodeData} />);
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    expect(screen.getByTestId('library-consequence')).toHaveTextContent('Browse only · this canvas is read-only');
    expect(screen.getByTestId('library-prompt-preview')).toHaveTextContent('Browse only · this canvas is read-only');
    expect(screen.queryByText('Pick a prompt node first')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save as template…' })).toBeDisabled();
  });

  it('Tab skips the project segment when the canvas has no project', async () => {
    mount({});
    const root = (await screen.findByTestId('library-prompt-row')).closest('[data-testid="library-prompts-page"]')!;
    fireEvent.keyDown(root, { key: 'Tab' });
    // 'project' would be sent with a null projectId and answered 422; its chip
    // is disabled, so the user could not get back out of it either.
    expect(useLibraryStore.getState().promptSegment).toBe('mine');
  });

  it('Tab reaches the project segment once the canvas has one', async () => {
    useCanvasCoreStore.setState({ nodes: [node({})] as never, projectId: '7001', readOnly: false } as never);
    render(<LibraryPromptsPage target={target} targetData={useCanvasCoreStore.getState().nodes[0].data as PromptNodeData} />);
    const root = (await screen.findByTestId('library-prompt-row')).closest('[data-testid="library-prompts-page"]')!;
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(useLibraryStore.getState().promptSegment).toBe('project');
    // Drain the refetch the segment change starts, so it does not land after
    // the test and warn about an update outside act().
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2));
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

  // I3: promoting a Chinese prompt while the panel shows 中 used to write the
  // text into the ENGLISH columns, so the new template read "EN only" and its
  // 中 toggle greyed out — no error, no data loss, nothing that would report it.
  it('Save as template writes a zh-only entry into the zh columns', async () => {
    const zh: PromptEntry = { ...image, positive_en: null, negative_en: null, positive_zh: '雨中的自行车', negative_zh: '模糊' };
    fetchPrompts.mockResolvedValue({ ...page, items: [zh] });
    useLibraryStore.setState({ promptLang: 'zh' } as never);
    mount({});
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Save as template…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(saveAsTemplate).toHaveBeenCalled());
    const input = saveAsTemplate.mock.calls.at(-1)![1] as Record<string, unknown>;
    expect(input.positiveZh).toBe('雨中的自行车');
    expect(input.positive).toBe('');
  });

  it('Save current from a node stays in the en columns — the node has no language', async () => {
    mount({ body: 'my prompt', negative_body: 'ugly' });
    await screen.findByTestId('library-prompt-row');
    fireEvent.click(screen.getByRole('button', { name: 'Save current…' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Mine 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(saveAsTemplate).toHaveBeenCalled());
    const input = saveAsTemplate.mock.calls.at(-1)![1] as Record<string, unknown>;
    expect(input.positive).toBe('my prompt');
    expect(input.positiveZh).toBeUndefined();
  });

  // The panel opens with focus in the search box (`focusSearch`), so a guard
  // that hands EVERY key to the field makes the arrow navigation unavailable
  // at exactly the moment the page opens — the user must click a row first.
  it('ArrowDown moves the selection while the search box has focus', async () => {
    fetchPrompts.mockResolvedValue({ ...page, items: [image, { ...image, key: 'image:11', title: 'Second', positive_en: 'second' }], total: 2 });
    mount({});
    await screen.findAllByTestId('library-prompt-row');
    const search = screen.getByTestId('library-search');
    search.focus();
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    expect(screen.getAllByTestId('library-prompt-row')[0]).toHaveAttribute('aria-selected', 'true');
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

/**
 * 「这两个保存当前和新建不像 button，而且这个位置不好」——用户实拍反馈。
 *
 * 两个毛病是分开的，测试也分开钉：
 *
 * 1. **不像 button**：它们原来带 `border-transparent`，在深色底上就是两行
 *    纯文字。这个面板里凡是可点的都带 `border-canvas-line`（`chipClass`
 *    就是这么定的），这两个是唯二的例外。
 * 2. **位置不好**：它们躺在最底下的状态条里，跟「N 条提示词」挤在一起。
 *    可它们写入的是 Mine 那个货架，所以归属在分段行，不在计数旁边。
 *
 * 加图标带来的真实回归风险是无障碍名被污染——既有用例全靠
 * `getByRole('button', { name: 'New…' })` 找它们，所以这里正面钉住。
 */
describe('LibraryPromptsPage — 创建动作的位置与可点性', () => {
  beforeEach(() => {
    fetchPrompts.mockReset().mockResolvedValue(page);
    fetchPromptCounts.mockReset().mockResolvedValue({ mine: 1, project: null, system: 0 });
    useLibraryStore.setState({ open: true, page: 'prompts', query: '', promptSegment: 'mine', promptForm: null, promptLang: 'en' } as never);
  });

  it('两个创建动作在头部动作组里，不在底部状态条里', async () => {
    mount({});
    await screen.findByTestId('library-prompt-row');

    const actions = screen.getByTestId('library-prompt-actions');
    const footer = screen.getByTestId('library-prompts-footer');

    for (const name of ['Save current…', 'New…']) {
      const btn = screen.getByRole('button', { name });
      expect(actions).toContainElement(btn);
      expect(footer).not.toContainElement(btn);
    }
    // 状态条只剩计数——它是货架的说明，不是操作区。
    expect(footer.querySelectorAll('button')).toHaveLength(0);
  });

  it('两个创建动作带可见边框，跟面板里其他可点元素一样', async () => {
    mount({});
    await screen.findByTestId('library-prompt-row');

    for (const name of ['Save current…', 'New…']) {
      const btn = screen.getByRole('button', { name });
      // 这一条就是缺陷本身：透明边框 = 看不出能点。
      expect(btn.className).not.toMatch(/border-transparent/);
      expect(btn.className).toContain('border-canvas-line');
    }
  });

  it('图标不进无障碍名——既有的按名查询必须继续可用', async () => {
    mount({});
    await screen.findByTestId('library-prompt-row');

    // 图标在，但名字仍然只有文字：`getByRole(name)` 是全套测试找它们的唯一途径。
    expect(screen.getByRole('button', { name: 'New…' }).querySelector('svg')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Save current…' }).querySelector('svg')).not.toBeNull();
  });
});
