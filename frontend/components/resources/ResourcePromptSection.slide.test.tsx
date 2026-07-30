/**
 * Slide mode — the album per-slide prompt editor that replaced the on-image
 * SlidePromptStrip overlay (2026-07-29). Unlike ResourcePromptSection.test.tsx
 * (which stubs PromptSection to assert prop wiring), this file renders the REAL
 * PromptSection: the invariants worth pinning here — a whole-map merge, and a
 * draft on slide A never landing on slide B — only exist end-to-end, across the
 * editor's local state and the wrapper's write path.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Tag } from '../../types';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string, opts?: Record<string, unknown>) => {
      let out = d ?? _k;
      if (opts) {
        for (const [key, value] of Object.entries(opts)) {
          out = out.split(`{{${key}}}`).join(String(value));
        }
      }
      return out;
    },
  }),
}));

const albumRow = {
  id: 'r1',
  filename: 'album',
  // A downloaded album: raw platform type code + a linked parsed_media, which
  // is what makes the whole-resource caption unavailable.
  file_type: '68',
  mime_type: 'image/jpeg',
  media_id: 'm9',
  gen_prompt: 'ALBUM LEVEL PROMPT',
  gen_prompt_zh: 'ALBUM LEVEL PROMPT ZH',
  gen_prompt_negative: null,
  gen_prompt_negative_zh: null,
  gen_prompt_json: '{"category":"photo","aspect_ratio":"1:1"}',
  slide_prompts: {
    'a.jpg': { en: 'a cat on a couch', neg_en: 'blurry' },
    'c.jpg': { zh: '一只狗' },
    'd.jpg': { en: 'both sides', zh: '两边都有' },
  },
};

const singleMock = vi.fn().mockResolvedValue({ data: albumRow, error: null });
const eqMock = vi.fn(() => ({ single: singleMock }));
const selectMock = vi.fn(() => ({ eq: eqMock }));
const fromMock = vi.fn((..._args: unknown[]) => ({ select: selectMock }));
vi.mock('../../supabaseClient', () => ({
  supabase: { from: (...a: unknown[]) => fromMock(...a) },
}));

const triggerTag: Tag = {
  id: 't2', name: 'AI', color: '#6366f1', icon: null, type: 'user',
  prompt_trigger: true, created_at: '2026-01-01T00:00:00Z',
};

const updateResource = vi.fn().mockResolvedValue({});
const generateSlidePrompt = vi.fn().mockResolvedValue('task-1');
const generateGenPrompt = vi.fn().mockResolvedValue('task-9');
const fetchResourceTags = vi.fn().mockResolvedValue([{ tag: triggerTag }]);
const addResourceTag = vi.fn().mockResolvedValue(undefined);
const translateGenPrompt = vi.fn().mockResolvedValue({});
vi.mock('../../services/resourceService', () => ({
  fetchResourceTags: (...a: unknown[]) => fetchResourceTags(...a),
  addResourceTag: (...a: unknown[]) => addResourceTag(...a),
  updateResource: (...a: unknown[]) => updateResource(...a),
  translateGenPrompt: (...a: unknown[]) => translateGenPrompt(...a),
  generateSlidePrompt: (...a: unknown[]) => generateSlidePrompt(...a),
  generateGenPrompt: (...a: unknown[]) => generateGenPrompt(...a),
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

// Factories are hoisted above the consts above, so this one reads `triggerTag`
// lazily (inside the call) rather than baking it into a resolved value.
vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn(async () => [triggerTag]),
  createTag: vi.fn(),
  updateTag: vi.fn(),
}));

// SendToCanvasModal's module graph loads eagerly with PromptSection.
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../services/projectsService', () => ({
  fetchProjects: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({ useOptionalToast: () => ({ addToast }) }));

const mockTasks = vi.fn<() => UnifiedTask[]>(() => []);
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: mockTasks() }),
}));

import { ResourcePromptSection } from './ResourcePromptSection';

/** Opening the editor also fires the ensure-trigger-tag flow (floating
 *  promise), so the click has to settle inside act. */
const expandVia = async (label: RegExp | string) => {
  const trigger = await screen.findByText(label);
  await act(async () => {
    fireEvent.click(trigger);
  });
};

beforeEach(() => {
  singleMock.mockReset().mockResolvedValue({ data: albumRow, error: null });
  updateResource.mockReset().mockResolvedValue({});
  generateSlidePrompt.mockReset().mockResolvedValue('task-1');
  generateGenPrompt.mockReset().mockResolvedValue('task-9');
  fetchResourceTags.mockReset().mockResolvedValue([{ tag: triggerTag }]);
  addResourceTag.mockReset().mockResolvedValue(undefined);
  mockTasks.mockReset().mockReturnValue([]);
  addToast.mockReset();
  fromMock.mockClear();
});

describe('ResourcePromptSection — slide mode reads the current slide', () => {
  it('shows the slide entry, not the album resource\'s own prompt', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat on a couch');
    expect(screen.queryByText(/ALBUM LEVEL PROMPT/)).toBeNull();
  });

  it('follows the viewer to another slide without refetching the row', async () => {
    const { rerender } = render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat on a couch');
    expect(fromMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      rerender(<ResourcePromptSection resourceId="r1" slideName="c.jpg" />);
    });

    // 'c.jpg' carries only a zh side — that becomes the preview.
    await screen.findByText('一只狗');
    expect(screen.queryByText('a cat on a couch')).toBeNull();
    expect(fromMock).toHaveBeenCalledTimes(1);
  });

  it('labels the block with the current slide position', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="c.jpg" slideIndex={1} slideCount={3} />);
    expect(await screen.findByTestId('slide-prompt-badge')).toHaveTextContent('Slide 2/3');
  });

  it('falls back to the slide filename when the host has no position', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="c.jpg" />);
    expect(await screen.findByTestId('slide-prompt-badge')).toHaveTextContent('c.jpg');
  });

  it('offers no JSON tab and no Translate — a slide entry has neither', async () => {
    // 'd.jpg' has both language sides, which is what would otherwise light up
    // Translate; the album row itself carries a gen_prompt_json.
    render(<ResourcePromptSection resourceId="r1" slideName="d.jpg" />);
    await expandVia(/both sides/);

    expect(screen.queryByText('JSON')).toBeNull();
    expect(screen.queryByText('Translate')).toBeNull();
  });

  it('keeps Translate for the album resource itself (no slide on screen)', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await expandVia(/ALBUM LEVEL PROMPT/);
    expect(screen.getByText('Translate')).toBeTruthy();
  });
});

describe('ResourcePromptSection — slide mode writes one slide', () => {
  it('PATCHes the WHOLE slide_prompts map, leaving other slides untouched', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await expandVia(/a cat on a couch/);

    const pos = screen.getByPlaceholderText(/Paste the AI generation prompt/i) as HTMLTextAreaElement;
    expect(pos.value).toBe('a cat on a couch');
    fireEvent.change(pos, { target: { value: 'a cat on a red couch' } });
    fireEvent.blur(pos);

    await waitFor(() => expect(updateResource).toHaveBeenCalled());
    const [id, patch] = updateResource.mock.calls[0];
    expect(id).toBe('r1');
    // Only slide_prompts is written — never the album's own gen_prompt columns.
    expect(Object.keys(patch)).toEqual(['slide_prompts']);
    expect(patch.slide_prompts['a.jpg']).toEqual({
      en: 'a cat on a red couch',
      neg_en: 'blurry',
    });
    expect(patch.slide_prompts['c.jpg']).toEqual({ zh: '一只狗' });
    expect(patch.slide_prompts['d.jpg']).toEqual({ en: 'both sides', zh: '两边都有' });
  });

  it('writes the negative side to the slide entry\'s neg_en', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await expandVia(/a cat on a couch/);

    const neg = screen.getByPlaceholderText(/Negative prompt/i);
    fireEvent.change(neg, { target: { value: 'lowres, extra fingers' } });
    fireEvent.blur(neg);

    await waitFor(() => expect(updateResource).toHaveBeenCalled());
    const [, patch] = updateResource.mock.calls[0];
    expect(patch.slide_prompts['a.jpg']).toEqual({
      en: 'a cat on a couch',
      neg_en: 'lowres, extra fingers',
    });
  });

  /**
   * The dangerous case is two slides holding the SAME text: keyed on the
   * resource id alone the editor-sync effect wouldn't re-run, so slide A's
   * unsaved draft would still be in the textarea when B arrives and the next
   * blur would commit it onto B. `editorScopeKey` is what closes that.
   */
  it('drops an unsaved draft on slide change instead of writing it to the next slide', async () => {
    singleMock.mockResolvedValue({
      data: {
        ...albumRow,
        slide_prompts: { 'a.jpg': { en: 'same text' }, 'b.jpg': { en: 'same text' } },
      },
      error: null,
    });

    const { rerender } = render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await expandVia(/same text/);

    const pos = screen.getByPlaceholderText(/Paste the AI generation prompt/i) as HTMLTextAreaElement;
    fireEvent.change(pos, { target: { value: 'DRAFT FOR SLIDE A' } });

    // The user browses on WITHOUT blurring first (a swipe, a dot click).
    await act(async () => {
      rerender(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    });

    expect((screen.getByPlaceholderText(/Paste the AI generation prompt/i) as HTMLTextAreaElement).value)
      .toBe('same text');

    fireEvent.blur(screen.getByPlaceholderText(/Paste the AI generation prompt/i));
    await act(async () => { await Promise.resolve(); });
    expect(updateResource).not.toHaveBeenCalled();
  });
});

describe('ResourcePromptSection — slide mode Generate', () => {
  it('dispatches the per-slide endpoint for the visible slide', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    await expandVia(/a cat on a couch/);

    await act(async () => { fireEvent.click(screen.getByText('Generate')); });

    expect(generateSlidePrompt).toHaveBeenCalledWith('r1', 'a.jpg');
    expect(generateGenPrompt).not.toHaveBeenCalled();
    expect(screen.getByText('Analyzing...')).toBeTruthy();
  });

  // A slide with nothing on it is exactly where reverse-engineering is the
  // answer, so Generate must be reachable without first opening the editor.
  it('offers Generate on an empty slide without expanding first', async () => {
    render(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    const button = await screen.findByText('Generate');
    expect(screen.getByText(/\+ Add Prompt/)).toBeTruthy();

    await act(async () => { fireEvent.click(button); });
    expect(generateSlidePrompt).toHaveBeenCalledWith('r1', 'b.jpg');
    expect(screen.getByText('Analyzing...')).toBeTruthy();
  });

  it('keeps an in-flight run pinned to its own slide', async () => {
    const { rerender } = render(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    const button = await screen.findByText('Generate');
    await act(async () => { fireEvent.click(button); });
    expect(screen.getByText('Analyzing...')).toBeTruthy();

    await act(async () => {
      rerender(<ResourcePromptSection resourceId="r1" slideName="a.jpg" />);
    });

    // Slide a must not wear slide b's spinner.
    expect(screen.queryByText('Analyzing...')).toBeNull();
    expect(screen.getByText('a cat on a couch')).toBeTruthy();
  });

  it('re-reads the map when the run completes, so the new entry shows up', async () => {
    const { rerender } = render(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    const button = await screen.findByText('Generate');
    await act(async () => { fireEvent.click(button); });

    singleMock.mockResolvedValue({
      data: {
        ...albumRow,
        slide_prompts: { ...albumRow.slide_prompts, 'b.jpg': { en: 'a generated caption' } },
      },
      error: null,
    });
    mockTasks.mockReturnValue([{
      id: 'task-1', user_id: 'u1', task_type: 'prompt_caption_slide', status: 'completed',
      title: 'Prompt b.jpg', progress: 100, metadata: {}, created_at: '2026-07-29T00:00:00Z',
    } as UnifiedTask]);
    await act(async () => {
      rerender(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    });

    await waitFor(() => expect(screen.getByText('a generated caption')).toBeTruthy());
  });

  it('shows the endpoint\'s own reason when the dispatch is rejected', async () => {
    generateSlidePrompt.mockRejectedValue(new Error('Only image slides can be reverse-engineered'));
    render(<ResourcePromptSection resourceId="r1" slideName="b.jpg" />);
    const button = await screen.findByText('Generate');
    await act(async () => { fireEvent.click(button); });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Only image slides can be reverse-engineered');
  });

  it('a gallery with no slide on screen still points at where Generate lives', async () => {
    render(<ResourcePromptSection resourceId="r1" />);
    await expandVia(/ALBUM LEVEL PROMPT/);
    expect(screen.getByTestId('generate-unavailable-hint')).toBeTruthy();
    expect(screen.queryByText('Generate')).toBeNull();
  });
});
