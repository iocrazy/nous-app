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
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
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

  it('task failure shows a toast with the error and resets the Generate button', async () => {
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

  it('dispatch-time failure (before a task exists) shows a toast and resets', async () => {
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
