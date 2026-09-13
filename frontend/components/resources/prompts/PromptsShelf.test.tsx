import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Interpolating `t` (same shape as TemplateForm.test.tsx): the cap notice
// carries the page size as `{{limit}}`, and a passthrough mock would render a
// literal `{{limit}}` that real i18next never shows.
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
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../../contexts/ResourcesContext', () => ({ useResourcesContext: () => ({ scopeId: '9000', resPath: (p: string) => p, refreshAssetCounts: vi.fn() }) }));
vi.mock('../SendToCanvasModal', () => ({ SendToCanvasModal: (p: { positive: string }) => <div data-testid="send-modal">{p.positive}</div> }));
vi.mock('../../prompts/SaveAsTemplateDialog', () => ({ SaveAsTemplateDialog: () => <div data-testid="save-dialog" /> }));
const fetchProjects = vi.fn();
vi.mock('../../../services/projectsService', () => ({ fetchProjects: (...a: unknown[]) => fetchProjects(...a) }));
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
const emptyPage = { items: [], total: 0, by_form: { template: 0, image: 0, album: 0 }, by_origin: { typed: 0, extracted: 0, captioned: 0 } };
const preset = { ...image, key: 'template:900', form: 'template', origin: 'typed', title: '6 expressions bust', positive_en: 'six basic expressions', params: null, thumbs: [], source: { store: 'assets', id: '900' } };
const presetPage = { items: [preset], total: 1, by_form: { template: 1, image: 0, album: 0 }, by_origin: { typed: 1, extracted: 0, captioned: 0 } };

function mount() {
  return render(<MemoryRouter initialEntries={['/resources/assets/prompt']}><PromptsShelf /></MemoryRouter>);
}

describe('PromptsShelf', () => {
  beforeEach(() => {
    fetchPrompts.mockReset();
    // Two fetches per render now: the shelf's own segment, and `system` for
    // the presets section. Routing on the segment (rather than a call-order
    // mock) keeps every assertion below reading about the segment it means.
    fetchPrompts.mockImplementation((_scope: unknown, opts: { segment?: string }) =>
      Promise.resolve(opts?.segment === 'system' ? emptyPage : page));
    fetchProjects.mockReset(); fetchProjects.mockResolvedValue([{ id: '77', name: 'Orchard Film', team_id: null }]);
  });

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

  // Ruling R6: `total` describes the whole unfiltered segment, so a filter that
  // matched nothing in a segment that HAS prompts must say so — telling that
  // user "no prompts yet" would send them making one they already have.
  it('filtered result of zero shows No prompts match, not the empty copy', async () => {
    fetchPrompts.mockResolvedValue({ items: [], total: 3, by_form: { template: 0, image: 2, album: 1 }, by_origin: { typed: 0, extracted: 1, captioned: 2 } });
    render(<MemoryRouter initialEntries={['/resources/assets/prompt?form=album']}><PromptsShelf /></MemoryRouter>);
    expect(await screen.findByText('No prompts match these filters')).toBeInTheDocument();
    expect(screen.queryByText(/No prompts yet/)).toBeNull();
  });

  // Ruling R12: the URL is the shelf's state, so a query it cannot round-trip
  // is a query the user cannot type. Trimming on serialize ate every space.
  it('the search box accepts a trailing space', async () => {
    mount();
    await screen.findByText('cheerful woman, oranges');
    const box = screen.getByLabelText('Search prompts');
    fireEvent.change(box, { target: { value: 'a ' } });
    expect(box).toHaveValue('a ');
  });

  // Ruling R13: `project=` was parsed and forwarded but had no control.
  it('the project selector switches the segment and refetches', async () => {
    mount();
    await screen.findByText('cheerful woman, oranges');
    const select = await screen.findByLabelText('Project');
    expect(screen.getByRole('option', { name: 'All projects' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: '77' } });
    await waitFor(() => expect(fetchPrompts).toHaveBeenLastCalledWith('9000', expect.objectContaining({ segment: 'project', projectId: '77' })));
  });

  it('a project list that fails to load leaves the shelf usable', async () => {
    fetchProjects.mockRejectedValueOnce(new Error('nope'));
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(await screen.findByLabelText('Project')).toBeInTheDocument();
  });

  // Ruling R14: no pagination this PR, so the cap has to be visible — a page
  // that silently stops at 200 next to an "All 640" chip is a lie by omission.
  // The number in the notice is interpolated from `PAGE_LIMIT`, not typed into
  // the copy — the two used to be able to drift apart silently.
  it('says so when the page is capped at 200', async () => {
    const many = Array.from({ length: 200 }, (_, i) => ({ ...image, key: `image:${i}` }));
    fetchPrompts.mockResolvedValue({ items: many, total: 640, by_form: { template: 0, image: 640, album: 0 }, by_origin: { typed: 0, extracted: 640, captioned: 0 } });
    mount();
    expect(await screen.findByText(/Showing the first 200/)).toBeInTheDocument();
  });

  it('says nothing about a cap on a short page', async () => {
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(screen.queryByText(/Showing the first 200/)).toBeNull();
  });
});

// The presets gap this closes: the catalog partitions by SEGMENT, and the
// shelf only ever asked for `mine` (or `project`). Every system preset prompt
// template — 10 of them live on production — was structurally unreachable
// here, so the Assets tab advertised prompt templates that the Prompts page
// then reported as "Templates 0".
//
// The shape of the fix is `AssetShelf`'s, not the canvas panel's: this
// component REPLACES `AssetShelf` for `assetType === 'prompt'`, and that
// component's second invariant already settled where presets go — "PRESETS
// ARE SEPARATED, NOT MIXED ... They get their own labelled section instead,
// so the badge and the team's own grid agree." A segment chip would have made
// the user click to discover content the sibling surface shows outright.
describe('PromptsShelf — system presets', () => {
  beforeEach(() => {
    fetchPrompts.mockReset();
    fetchPrompts.mockImplementation((_scope: unknown, opts: { segment?: string }) =>
      Promise.resolve(opts?.segment === 'system' ? presetPage : page));
    fetchProjects.mockReset(); fetchProjects.mockResolvedValue([]);
  });

  it('asks the catalog for the system segment as well as its own', async () => {
    mount();
    await screen.findByText('six basic expressions');
    expect(fetchPrompts).toHaveBeenCalledWith('9000', expect.objectContaining({ segment: 'system' }));
  });

  it('renders presets in their own labelled read-only section', async () => {
    mount();
    const section = await screen.findByTestId('prompt-preset-section');
    expect(within(section).getByText('System Presets')).toBeInTheDocument();
    expect(within(section).getByText('Read-only — duplicate one to edit it')).toBeInTheDocument();
    expect(within(section).getByText('six basic expressions')).toBeInTheDocument();
  });

  // The invariant AssetShelf spells out: presets are global and the counts
  // describe the team's own corpus. A preset that bumped "Templates 1" would
  // put a number on screen that the main list never accounts for.
  it('does not let presets inflate the form counts', async () => {
    mount();
    await screen.findByText('six basic expressions');
    expect(screen.getByRole('button', { name: 'Templates 0' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'All 3' })).toBeInTheDocument();
  });

  it('renders no section at all when the scope has no presets', async () => {
    fetchPrompts.mockImplementation((_scope: unknown, opts: { segment?: string }) =>
      Promise.resolve(opts?.segment === 'system' ? emptyPage : page));
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(screen.queryByTestId('prompt-preset-section')).toBeNull();
  });

  // Presets are all templates, so a shelf filtered to Images must not answer
  // with a section full of templates — and must not spend the request either.
  it('skips presets entirely when the form filter excludes templates', async () => {
    render(<MemoryRouter initialEntries={['/resources/assets/prompt?form=image']}><PromptsShelf /></MemoryRouter>);
    await screen.findByText('cheerful woman, oranges');
    expect(screen.queryByTestId('prompt-preset-section')).toBeNull();
    expect(fetchPrompts).not.toHaveBeenCalledWith('9000', expect.objectContaining({ segment: 'system' }));
  });

  // A preset section that ignored the search box would answer a query with
  // rows that do not match it.
  it('narrows presets with the same search the main list uses', async () => {
    render(<MemoryRouter initialEntries={['/resources/assets/prompt?q=bust']}><PromptsShelf /></MemoryRouter>);
    await screen.findByText('six basic expressions');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledWith('9000', expect.objectContaining({ segment: 'system', q: 'bust' })));
  });

  // The presets query is a separate request; its failure must not blank the
  // shelf the user actually came for.
  it('a preset fetch that fails leaves the main list standing', async () => {
    fetchPrompts.mockImplementation((_scope: unknown, opts: { segment?: string }) =>
      opts?.segment === 'system' ? Promise.reject(new Error('nope')) : Promise.resolve(page));
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(screen.queryByTestId('prompt-preset-section')).toBeNull();
  });
});
