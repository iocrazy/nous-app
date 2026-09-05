// The model's explanation must be VISIBLE, not folded behind a disclosure.
//
// It is the only actionable thing in a content refusal — it names what was
// objected to and hands back a rewrite that works — and burying it under a
// collapsed <details> next to the headline "Processing failed" is what the
// user actually saw on 2026-09-04.

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TaskDetailModal } from './TaskDetailModal';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('../../services/aiService', () => ({
  getSummaryByResource: vi.fn().mockResolvedValue(null),
  getTranscriptByResource: vi.fn().mockResolvedValue(null),
  getVisualAnalysisByResource: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../services/resourceService', () => ({
  fetchResourceById: vi.fn().mockResolvedValue(null),
}));

afterEach(cleanup);

const MODEL_WORDS =
  '抱歉，我不能帮助生成带有明显性化服饰与姿势的写实人物图像。' +
  '你也可以直接用这条更安全的提示词：成年年轻女性，黑色时尚连体服与长靴，半蹲姿';

// Verbatim shape of the production canvas_gen row, with the metadata the
// fixed backend now writes alongside it.
const refusedGen = (over: Partial<UnifiedTask> = {}): UnifiedTask =>
  ({
    id: 'f33deff6-9ecf-400d-a6b2-8250421c1d5e',
    dbos_workflow_id: 'f33deff6-9ecf-400d-a6b2-8250421c1d5e',
    task_type: 'canvas_gen',
    title: 'Generate image',
    status: 'failed',
    phase: 'failed',
    progress: 0,
    subtitle: '年轻美丽的女子，黑色长发',
    // As STORED: dbos_error_to_text prepends the class name. Verified against
    // the live function on nous-db (2026-09-04) — the line survives whole.
    error_msg:
      'RuntimeError: [content_refused] The image model declined this prompt and answered ' +
      "with an explanation instead of an image. Open this task's details to read its own " +
      'wording and the rewrite it suggests.',
    metadata: {
      kind: 'image',
      canvas_id: '337004651010097',
      failure: { code: 'content_refused', detail: MODEL_WORDS },
    },
    ...over,
  }) as unknown as UnifiedTask;

describe('TaskDetailModal — content refusal', () => {
  it("shows the model's own words without the user opening anything", () => {
    render(<TaskDetailModal task={refusedGen()} onClose={vi.fn()} onOpenResource={vi.fn()} />);
    const panel = screen.getByTestId('task-error-detail');
    expect(panel.textContent).toContain('我不能帮助生成');
    expect(panel.textContent).toContain('更安全的提示词');
    // Not inside a <details>: that is exactly the burial being fixed.
    expect(panel.closest('details')).toBeNull();
  });

  // Asserted on the headline element specifically: the raw string still sits
  // under Details (nothing the engine said is hidden), and it happens to
  // contain the same words, so a text query would match either one.
  it('says the model declined rather than that processing failed', () => {
    render(<TaskDetailModal task={refusedGen()} onClose={vi.fn()} onOpenResource={vi.fn()} />);
    expect(screen.getByTestId('task-error-message').textContent).toMatch(/declined this prompt/i);
    expect(screen.queryByText(/Processing failed/i)).toBeNull();
  });

  it('renders no explanation panel when the daemon sent no detail (0.4.0)', () => {
    render(
      <TaskDetailModal
        task={refusedGen({ metadata: { kind: 'image' } } as Partial<UnifiedTask>)}
        onClose={vi.fn()}
        onOpenResource={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('task-error-detail')).toBeNull();
    // The headline still names the refusal — that comes from error_msg alone.
    expect(screen.getByTestId('task-error-message').textContent).toMatch(/declined this prompt/i);
  });
});

// The expanded row is the OTHER place a task failure faces the user. Both
// read the same taskErrorCopy, so a fix applied to one and not the other is
// exactly the split PromptNodeView was already in.
//
// Only the row's action verbs come from the task manager; the error block is
// pure render off the task prop, so a stub context keeps this test about the
// copy rather than about wiring a provider.
vi.mock('../../contexts/TaskManagerContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTaskManager: () => ({ cancelTask: vi.fn(), retryTask: vi.fn(), deleteTask: vi.fn() }),
}));

import { TaskRowExpanded } from './TaskRowExpanded';

describe('TaskRowExpanded — content refusal', () => {
  it("shows the model's own words in the expanded row too", () => {
    render(<TaskRowExpanded task={refusedGen()} />);
    expect(screen.getByTestId('task-error-detail').textContent).toContain('我不能帮助生成');
  });
});

// ── the refusal modal reads like an answer, not a stack dump (2026-09-05) ────
describe('TaskDetailModal — refusal presentation', () => {
  // `screen`, not `container`: the modal portals to document.body, so a
  // container query finds nothing and passes for the wrong reason.
  it('does not also offer a raw Details disclosure that only repeats the headline', () => {
    render(<TaskDetailModal task={refusedGen()} onClose={vi.fn()} onOpenResource={vi.fn()} />);
    expect(screen.queryByText(/^Details$/i)).toBeNull();
  });

  it('renders the model markdown emphasis as text, not literal asterisks', () => {
    const words = '可以改为**黑色挂脖皮革上衣**，保留橙色玫瑰。';
    render(
      <TaskDetailModal
        task={refusedGen({ metadata: { kind: 'image', failure: { code: 'content_refused', detail: words } } } as Partial<UnifiedTask>)}
        onClose={vi.fn()} onOpenResource={vi.fn()}
      />,
    );
    const panel = screen.getByTestId('task-error-detail');
    expect(panel.textContent).toContain('黑色挂脖皮革上衣');
    expect(panel.textContent).not.toContain('**');
  });

  it('labels the explanation so it reads as the model speaking', () => {
    render(<TaskDetailModal task={refusedGen()} onClose={vi.fn()} onOpenResource={vi.fn()} />);
    expect(screen.getByText(/what the model said/i)).toBeInTheDocument();
  });

  it('does not promise a result for a run that already failed', () => {
    render(<TaskDetailModal task={refusedGen()} onClose={vi.fn()} onOpenResource={vi.fn()} />);
    expect(screen.queryByText(/Result appears here when the generation finishes/i)).toBeNull();
    expect(screen.getByText(/no image was produced/i)).toBeInTheDocument();
  });
});
