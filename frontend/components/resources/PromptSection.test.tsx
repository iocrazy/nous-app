import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PromptSection } from './PromptSection';
import { ToastProvider } from '../Toast';
import type { Resource } from '../../types';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

// SendToCanvasModal only mounts once the button is clicked, but its module
// (and its transitive imports — react-router-dom, projectsService,
// canvasService, resourceService) load eagerly with PromptSection. Stub
// them so the un-clicked tests below stay hermetic and the clicked test
// gets a deterministic, empty project list.
const fetchProjects = vi.fn().mockResolvedValue([]);
const listCanvases = vi.fn().mockResolvedValue([]);
// A shared mock (not a fresh vi.fn() per render) so tests can assert on
// the actual navigate call SendToCanvasModal fires — Generate Similar's
// autoRun/ratio wiring is only observable end-to-end through it.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
vi.mock('../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: (...a: unknown[]) => listCanvases(...a),
}));

const generateGenPrompt = vi.fn();
vi.mock('../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
  generateGenPrompt: (...a: unknown[]) => generateGenPrompt(...a),
}));

// Mock useTaskManager — PromptSection's self-managed Generate flow watches
// a single task via useTaskCompletion, which only reads `.tasks`.
const mockTasks = vi.fn<() => UnifiedTask[]>(() => []);
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: mockTasks() }),
}));

function makeTask(id: string, status: UnifiedTask['status'], extra: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id,
    user_id: 'u1',
    task_type: 'ai_extract',
    status,
    title: 'Test',
    progress: 0,
    metadata: {},
    created_at: '2026-07-28T00:00:00Z',
    ...extra,
  } as UnifiedTask;
}

const base = (over: Partial<Resource> = {}): Resource =>
  ({
    id: 'r1', filename: 'a.png', file_type: 'image',
    gen_prompt: null, gen_prompt_zh: null,
    gen_prompt_negative: null, gen_prompt_negative_zh: null,
    gen_prompt_json: null,
    ...over,
  }) as unknown as Resource;

const noop = () => {};
const props = (over: Partial<Parameters<typeof PromptSection>[0]> = {}) => ({
  resource: base(), onPatch: noop,
  onEnsureTriggerTag: vi.fn().mockResolvedValue(undefined),
  canGenerate: true, onGenerated: vi.fn(),
  translating: false, onTranslate: noop,
  ...over,
});

beforeEach(() => {
  mockTasks.mockReset().mockReturnValue([]);
  generateGenPrompt.mockReset();
  fetchProjects.mockReset().mockResolvedValue([]);
  listCanvases.mockReset().mockResolvedValue([]);
  mockNavigate.mockReset();
});

describe('PromptSection', () => {
  it('shows collapsed preview card when prompt data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'masterpiece, 1girl' }) })} />);
    // Section header (Sparkles + "Prompt" caption) is always present.
    expect(screen.getByText('Prompt')).toBeTruthy();
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull(); // not expanded yet
  });

  it('shows section header + Add Prompt pill when no data and no trigger tag', () => {
    render(<PromptSection {...props()} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
    const pill = screen.getByText(/\+ Add Prompt/).closest('button');
    expect(pill).not.toBeNull();
    expect(pill?.className).toContain('border-dashed');
  });

  it('collapsed preview card shows only the negative row when only negative data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt_negative: 'lowres, bad hands' }) })} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
    expect(screen.getByText(/lowres, bad hands/)).toBeTruthy();
    // No positive text was supplied, so no font-mono positive line renders.
    expect(document.querySelector('.font-mono.text-\\[11px\\]')).toBeNull();
  });

  it('expanding calls onEnsureTriggerTag and reveals both textareas', async () => {
    const p = props({ resource: base({ gen_prompt: 'pos text' }) });
    render(<PromptSection {...p} />);
    fireEvent.click(screen.getByText(/pos text/));
    expect(p.onEnsureTriggerTag).toHaveBeenCalledOnce();
    expect(await screen.findByDisplayValue('pos text')).toBeTruthy();
    expect(screen.getByPlaceholderText(/negative/i)).toBeTruthy();
  });

  it('negative blur patches gen_prompt_negative', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: 'lowres, bad hands' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative: 'lowres, bad hands' });
  });

  it('zh lang patches the _zh columns', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    fireEvent.click(screen.getByText('中'));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: '低分辨率' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative_zh: '低分辨率' });
  });

  it('defaults to zh when only gen_prompt_zh has content', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt_zh: '杰作, 1girl' }) })} />);
    fireEvent.click(screen.getByText(/杰作, 1girl/));
    expect(await screen.findByDisplayValue('杰作, 1girl')).toBeTruthy();
  });

  it('shows the Send to Canvas button only when the expanded view has prompt data', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }) })} />);
    fireEvent.click(screen.getByText(/^p$/));
    expect(await screen.findByText('Send to Canvas')).toBeTruthy();
  });

  it('omits Send to Canvas when expanded with no prompt data yet (trigger-tag row)', () => {
    render(<PromptSection {...props()} />);
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    expect(screen.queryByText('Send to Canvas')).toBeNull();
  });

  it('clicking Send to Canvas opens the modal with the current lang side as positive', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'a hero shot' }) })} />);
    fireEvent.click(screen.getByText(/a hero shot/));
    fireEvent.click(await screen.findByText('Send to Canvas'));

    expect(await screen.findByText(/No projects yet/)).toBeTruthy();
    expect(fetchProjects).toHaveBeenCalled();
  });

  // ─── ⚡ Generate Similar (spec 2026-07-28-prompt-dataline, Task 5) ────

  const jsonResourceForSimilar = () =>
    base({
      gen_prompt: 'a cat, anime style',
      gen_prompt_json: JSON.stringify({
        subject: 'a cat', style: 'anime', composition: 'centered',
        lighting: 'soft', color: 'pastel', aspect_ratio: '16:9', category: 'portrait',
      }),
    });

  it('shows Generate Similar only when the analysis JSON result is present', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }) })} />);
    fireEvent.click(screen.getByText(/^p$/));
    expect(screen.queryByText('Generate Similar')).toBeNull();
  });

  it('shows Generate Similar next to Send to Canvas once gen_prompt_json exists', async () => {
    render(<PromptSection {...props({ resource: jsonResourceForSimilar() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    expect(await screen.findByText('Generate Similar')).toBeTruthy();
    expect(screen.getByText('Send to Canvas')).toBeTruthy();
  });

  // M3: Generate Similar must not fire with an empty positive prompt for
  // the current lang side — an empty prompt reaches the canvas as an empty
  // node and autoRun 422s the backend silently.
  it('disables Generate Similar when the current lang side has no positive prompt (M3)', async () => {
    const resource = base({
      gen_prompt: '',
      gen_prompt_zh: '',
      gen_prompt_negative: 'blurry',
      gen_prompt_json: JSON.stringify({ subject: 'x', aspect_ratio: '16:9' }),
    });
    render(<PromptSection {...props({ resource })} />);
    fireEvent.click(screen.getByText(/blurry/));
    const btn = await screen.findByText('Generate Similar');
    expect(btn.closest('button')).toBeDisabled();
  });

  it('enables Generate Similar once the current lang side has a positive prompt (M3)', async () => {
    render(<PromptSection {...props({ resource: jsonResourceForSimilar() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    const btn = await screen.findByText('Generate Similar');
    expect(btn.closest('button')).not.toBeDisabled();
  });

  it('clicking Generate Similar sends autoRun + the analysis aspect_ratio through to the canvas navigate call', async () => {
    fetchProjects.mockResolvedValue([{ id: 'p1', name: 'Proj', team_id: null }]);
    listCanvases.mockResolvedValue([{ id: 'c1', name: 'Board' }]);

    render(<PromptSection {...props({ resource: jsonResourceForSimilar() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    fireEvent.click(await screen.findByText('Generate Similar'));

    fireEvent.click(await screen.findByText('Proj'));
    fireEvent.click(await screen.findByText('Board'));

    expect(mockNavigate).toHaveBeenCalledWith(
      '/canvas/c1',
      expect.objectContaining({
        state: {
          promptInsert: expect.objectContaining({ autoRun: true, ratio: '16:9' }),
        },
      }),
    );
  });

  it('clicking the plain Send to Canvas action (not Generate Similar) omits autoRun/ratio', async () => {
    fetchProjects.mockResolvedValue([{ id: 'p1', name: 'Proj', team_id: null }]);
    listCanvases.mockResolvedValue([{ id: 'c1', name: 'Board' }]);

    render(<PromptSection {...props({ resource: jsonResourceForSimilar() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    fireEvent.click(await screen.findByText('Send to Canvas'));

    fireEvent.click(await screen.findByText('Proj'));
    fireEvent.click(await screen.findByText('Board'));

    const [, options] = mockNavigate.mock.calls[0] as [
      string,
      { state: { promptInsert: Record<string, unknown> } },
    ];
    expect(options.state.promptInsert).not.toHaveProperty('autoRun');
    expect(options.state.promptInsert).not.toHaveProperty('ratio');
  });

  // ─── generateSimilar deep link (Task 1, spec 2026-07-28-extension-prompt-analyze) ───

  it('autoOpenGenerateSimilar expands and opens the picker immediately when analysis JSON + a positive prompt already exist', async () => {
    fetchProjects.mockResolvedValue([]);
    const p = props({ resource: jsonResourceForSimilar(), autoOpenGenerateSimilar: true });
    render(<PromptSection {...p} />);

    // Auto-expanded (no click needed) and the trigger-tag hook fired, same as manual expand().
    expect(await screen.findByDisplayValue('a cat, anime style')).toBeTruthy();
    expect(p.onEnsureTriggerTag).toHaveBeenCalledOnce();
    // Picker opened automatically — its content loads via fetchProjects.
    expect(await screen.findByText(/No projects yet/)).toBeTruthy();
    expect(fetchProjects).toHaveBeenCalled();
  });

  it('autoOpenGenerateSimilar just expands + toasts when no analysis JSON exists yet (no picker)', async () => {
    render(
      <ToastProvider>
        <PromptSection {...props({ resource: base({ gen_prompt: 'p' }), autoOpenGenerateSimilar: true })} />
      </ToastProvider>,
    );

    expect(await screen.findByDisplayValue('p')).toBeTruthy(); // expanded without a click
    expect(await screen.findByText('Generate a prompt first, then try Generate Similar')).toBeTruthy();
    expect(screen.queryByText(/No projects yet/)).toBeNull();
    expect(fetchProjects).not.toHaveBeenCalled();
  });

  it('autoOpenGenerateSimilar with JSON but an empty positive prompt (M3) falls back to the toast, not the picker', async () => {
    const resource = base({
      gen_prompt: '',
      gen_prompt_zh: '',
      gen_prompt_negative: 'blurry',
      gen_prompt_json: JSON.stringify({ subject: 'x', aspect_ratio: '16:9' }),
    });
    render(
      <ToastProvider>
        <PromptSection {...props({ resource, autoOpenGenerateSimilar: true })} />
      </ToastProvider>,
    );

    expect(await screen.findByDisplayValue('blurry')).toBeTruthy(); // expanded
    expect(await screen.findByText('Generate a prompt first, then try Generate Similar')).toBeTruthy();
    expect(fetchProjects).not.toHaveBeenCalled();
  });

  it('does not re-trigger the auto-open flow across rerenders once already fired (StrictMode double-invoke safe)', async () => {
    fetchProjects.mockResolvedValue([]);
    const p = props({ resource: jsonResourceForSimilar(), autoOpenGenerateSimilar: true });
    const { rerender } = render(<PromptSection {...p} />);
    await screen.findByText(/No projects yet/);
    expect(fetchProjects).toHaveBeenCalledOnce();

    rerender(<PromptSection {...p} />);
    rerender(<PromptSection {...p} />);
    expect(fetchProjects).toHaveBeenCalledOnce();
  });

  it('omits the auto-open behavior entirely when the prop is absent (default host usage)', () => {
    render(<PromptSection {...props({ resource: jsonResourceForSimilar() })} />);
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull(); // still collapsed
    expect(fetchProjects).not.toHaveBeenCalled();
  });

  // ─── Generate — self-managed dispatch + realtime progress ───────

  it('clicking Generate dispatches generateGenPrompt(resource.id) and shows the analyzing card', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    render(<PromptSection {...props()} />);
    fireEvent.click(screen.getByText(/\+ Add Prompt/));

    fireEvent.click(screen.getByText('Generate'));
    expect(generateGenPrompt).toHaveBeenCalledWith('r1');
    expect(await screen.findByText('Analyzing...')).toBeTruthy();
    // No editors while analyzing.
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull();
  });

  it('renders live progress % and subtitle once the task shows up in useTaskManager', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    const { rerender } = render(<PromptSection {...props()} />);
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));
    await screen.findByText('Analyzing...');

    mockTasks.mockReturnValue([makeTask('task-1', 'processing', { progress: 30, subtitle: 'Analyzing image...' })]);
    rerender(<PromptSection {...props()} />);

    expect(await screen.findByText('30%')).toBeTruthy();
    expect(screen.getByText('Analyzing image...')).toBeTruthy();
  });

  it('task completion calls onGenerated and clears the analyzing card', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    const onGenerated = vi.fn();
    const { rerender } = render(<PromptSection {...props({ onGenerated })} />);
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));
    await screen.findByText('Analyzing...');

    mockTasks.mockReturnValue([makeTask('task-1', 'processing', { progress: 85 })]);
    rerender(<PromptSection {...props({ onGenerated })} />);
    await screen.findByText('85%');

    mockTasks.mockReturnValue([makeTask('task-1', 'completed', { progress: 100 })]);
    rerender(<PromptSection {...props({ onGenerated })} />);

    await waitFor(() => expect(onGenerated).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByText('Analyzing...')).toBeNull());
  });

  // The failure surface is an in-section card, not a toast: the point of the
  // error catalog is that the user can act on the message, and a notification
  // that disappears after a few seconds can't be re-read. See
  // utils/errorCatalog.ts.
  it('task failure without an error_code shows the raw error and resets the Generate button', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    const { rerender } = render(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));
    await screen.findByText('Analyzing...');

    mockTasks.mockReturnValue([makeTask('task-1', 'failed', { error_msg: 'provider unreachable' })]);
    rerender(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );

    expect(await screen.findByText('provider unreachable')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('Analyzing...')).toBeNull());
    // Generate is clickable again (not stuck disabled/spinning).
    const generateBtn = screen.getByText('Generate').closest('button');
    expect(generateBtn).not.toBeDisabled();
  });

  it('task failure with an error_code shows actionable copy instead of the DBOS retry string', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    const raw =
      'DBOSMaxStepRetriesExceeded: Step ai_caption_via_provider has exceeded its maximum of 3 retries';
    const { rerender } = render(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));
    await screen.findByText('Analyzing...');

    mockTasks.mockReturnValue([
      makeTask('task-1', 'failed', {
        error_msg: raw,
        metadata: { error_code: 'PROVIDER_AUTH' },
      }),
    ]);
    rerender(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );

    const card = await screen.findByRole('alert');
    expect(card.textContent).toContain('The AI provider rejected the credentials');
    expect(card.textContent).toContain('Settings → AI');
    // The engine string is kept, but tucked behind the Details disclosure so
    // it isn't the first thing the user reads. (jsdom doesn't collapse
    // <details>, so assert on the element's placement rather than visibility.)
    expect(screen.getByText('Details')).toBeTruthy();
    expect(screen.getByText(raw).closest('details')).not.toBeNull();
  });

  it('the failure card is dismissible and clears on the next Generate', async () => {
    generateGenPrompt.mockResolvedValue('task-1');
    const { rerender } = render(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));
    await screen.findByText('Analyzing...');

    mockTasks.mockReturnValue([
      makeTask('task-1', 'failed', { error_msg: 'x', metadata: { error_code: 'TASK_TIMEOUT' } }),
    ]);
    rerender(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );
    await screen.findByRole('alert');

    fireEvent.click(screen.getByLabelText('Dismiss'));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('dispatch-time failure (before a task exists) shows the error and resets', async () => {
    generateGenPrompt.mockRejectedValue(new Error('network down'));
    render(
      <ToastProvider>
        <PromptSection {...props()} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    fireEvent.click(screen.getByText('Generate'));

    expect(await screen.findByText('network down')).toBeTruthy();
    expect(screen.queryByText('Analyzing...')).toBeNull();
  });

  // I6: manager.create() on the backend is warn-and-continue — if the
  // task_tracking row never lands, useTaskCompletion never sees a matching
  // task and the analyzing card would spin forever with Generate locked.
  // A ~90s fallback timer is the escape hatch.
  it('resets state and shows a toast if the task never completes within the fallback timeout (I6)', async () => {
    vi.useFakeTimers();
    try {
      generateGenPrompt.mockResolvedValue('task-1');
      render(
        <ToastProvider>
          <PromptSection {...props()} />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByText(/\+ Add Prompt/));

      await act(async () => {
        fireEvent.click(screen.getByText('Generate'));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText('Analyzing...')).toBeTruthy();

      // mockTasks stays [] the whole time — the task row never showed up.
      act(() => {
        vi.advanceTimersByTime(90000);
      });

      expect(screen.queryByText('Analyzing...')).toBeNull();
      expect(screen.getByText('Still generating — check Task Center')).toBeTruthy();
      // Generate is clickable again, not stuck.
      const generateBtn = screen.getByText('Generate').closest('button');
      expect(generateBtn).not.toBeDisabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not fire the fallback timeout once the task completes first (I6)', async () => {
    vi.useFakeTimers();
    try {
      generateGenPrompt.mockResolvedValue('task-1');
      const onGenerated = vi.fn();
      const { rerender } = render(<PromptSection {...props({ onGenerated })} />);
      fireEvent.click(screen.getByText(/\+ Add Prompt/));

      await act(async () => {
        fireEvent.click(screen.getByText('Generate'));
        await Promise.resolve();
        await Promise.resolve();
      });

      mockTasks.mockReturnValue([makeTask('task-1', 'completed', { progress: 100 })]);
      act(() => {
        rerender(<PromptSection {...props({ onGenerated })} />);
      });
      expect(onGenerated).toHaveBeenCalledOnce();

      // Advancing past the fallback window must not re-toast/reset anything
      // now that the task already completed and the timer was cleared.
      act(() => {
        vi.advanceTimersByTime(90000);
      });
      expect(screen.queryByText('Still generating — check Task Center')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('omits the Generate button when canGenerate is false', () => {
    render(<PromptSection {...props({ canGenerate: false, resource: base({ gen_prompt: 'p' }) })} />);
    fireEvent.click(screen.getByText(/^p$/));
    expect(screen.queryByText('Generate')).toBeNull();
  });

  // ─── Result card — 中文/EN/JSON tabs ──────────────────────────

  const jsonResource = () =>
    base({
      gen_prompt: 'a cat, anime style',
      gen_prompt_zh: '一只猫，动漫风格',
      gen_prompt_json: JSON.stringify({
        subject: 'a cat', style: 'anime', composition: 'centered',
        lighting: 'soft', color: 'pastel', aspect_ratio: '16:9', category: 'portrait',
      }),
    });

  it('shows 中文/EN/JSON tabs (not the two-way toggle) when gen_prompt_json is present', async () => {
    render(<PromptSection {...props({ resource: jsonResource() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    expect(await screen.findByText('中文')).toBeTruthy();
    expect(screen.getByText('EN')).toBeTruthy();
    expect(screen.getByText('JSON')).toBeTruthy();
  });

  it('keeps the plain EN/中 toggle when gen_prompt_json is absent', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }) })} />);
    fireEvent.click(screen.getByText(/^p$/));
    expect(screen.queryByText('中文')).toBeNull();
    expect(screen.queryByText('JSON')).toBeNull();
    expect(screen.getByText('中')).toBeTruthy();
  });

  it('JSON tab shows pretty-printed JSON with category/aspect_ratio chips, hides negative editor', async () => {
    render(<PromptSection {...props({ resource: jsonResource() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    fireEvent.click(await screen.findByText('JSON'));

    expect(screen.getByText('portrait')).toBeTruthy();
    expect(screen.getByText('16:9')).toBeTruthy();
    expect(screen.getByText(/"subject": "a cat"/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull();
  });

  it('Copy in the JSON tab copies the raw JSON string, not the positive text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    const resource = jsonResource();
    render(<PromptSection {...props({ resource })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    fireEvent.click(await screen.findByText('JSON'));

    fireEvent.click(screen.getByText('Copy'));
    expect(writeText).toHaveBeenCalledWith(resource.gen_prompt_json);
  });

  it('Copy in the EN tab still copies the positive text (unchanged behavior)', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<PromptSection {...props({ resource: jsonResource() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));

    fireEvent.click(screen.getByText('Copy'));
    expect(writeText).toHaveBeenCalledWith('a cat, anime style');
  });

  it('switching back to 中文/EN from JSON restores the editors', async () => {
    render(<PromptSection {...props({ resource: jsonResource() })} />);
    fireEvent.click(screen.getByText(/a cat, anime style/));
    fireEvent.click(await screen.findByText('JSON'));
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull();

    fireEvent.click(screen.getByText('EN'));
    expect(await screen.findByPlaceholderText(/negative/i)).toBeTruthy();
    expect(screen.queryByText(/"subject": "a cat"/)).toBeNull();
  });
});
