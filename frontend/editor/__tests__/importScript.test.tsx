/**
 * Import Script UI tests (PR-I2):
 *   1. detectImportMode — exhaustive pure-function cases.
 *   2. ImportScriptModal submit chain — dispatch → poll → navigate.
 *   3. Task-completion navigation to the new script.
 *   4. Upload size + type guards.
 */
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: { count?: number }) =>
      opts?.count != null ? `${k}:${opts.count}` : k,
  }),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ teamId: 'team-9' }),
}));

const addToast = vi.fn();
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast }),
  useOptionalToast: () => ({ addToast }),
}));

// Capture the latest useTaskCompletion args so a test can fire the terminal
// callback manually (no Realtime in jsdom).
let taskId: string | null = null;
let taskOpts: { onComplete?: (t: unknown) => void; onError?: (t: unknown) => void } = {};
vi.mock('../../hooks/useTaskCompletion', () => ({
  useTaskCompletion: (id: string | null, opts: typeof taskOpts) => {
    taskId = id;
    taskOpts = opts;
    return {};
  },
}));

const svc = vi.hoisted(() => ({ importScreenplay: vi.fn() }));
vi.mock('../importService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../importService')>();
  return { ...actual, importScreenplay: svc.importScreenplay };
});

import { detectImportMode, MAX_IMPORT_CHARS } from '../importService';
import { ImportScriptModal } from '../components/ImportScriptModal';

afterEach(() => {
  cleanup();
  navigate.mockReset();
  addToast.mockReset();
  svc.importScreenplay.mockReset();
  taskId = null;
  taskOpts = {};
});

// --------------------------------------------------------------------------- //
// detectImportMode
// --------------------------------------------------------------------------- //

describe('detectImportMode', () => {
  it('flags two or more scene headings as fountain', () => {
    expect(detectImportMode('INT. ROOM - DAY\n\nHi.\n\nEXT. STREET - NIGHT\n\nBye.')).toBe(
      'fountain',
    );
  });

  it('treats a single heading as prose (below threshold)', () => {
    expect(detectImportMode('INT. ROOM - DAY\n\nJust one scene.')).toBe('prose');
  });

  it('treats heading-free text as prose', () => {
    expect(detectImportMode('Once upon a time there was a writer.')).toBe('prose');
  });

  it('is case-insensitive', () => {
    expect(detectImportMode('int. a - day\n\nx\n\next. b - night\n\ny')).toBe('fountain');
  });

  it('recognizes EST / INT-EXT / I/E / space-delimited prefixes', () => {
    expect(detectImportMode('EST. HILLS - DAWN\n\nEXT PARK - DAY')).toBe('fountain');
    expect(detectImportMode('INT/EXT CAR - DAY\n\nI/E. TRAIN - NIGHT')).toBe('fountain');
  });

  it('ignores headings mid-line (must be line-start)', () => {
    expect(detectImportMode('We go INT. ROOM then EXT. STREET quickly.')).toBe('prose');
  });

  it('allows leading whitespace before a heading', () => {
    expect(detectImportMode('   INT. A - DAY\n\n\tEXT. B - NIGHT')).toBe('fountain');
  });

  it('returns prose for empty text', () => {
    expect(detectImportMode('')).toBe('prose');
  });
});

// --------------------------------------------------------------------------- //
// ImportScriptModal — submit chain + navigation
// --------------------------------------------------------------------------- //

function fillName(value: string) {
  fireEvent.change(screen.getByPlaceholderText('editor.importNamePlaceholder'), {
    target: { value },
  });
}

function pasteContent(value: string) {
  fireEvent.change(screen.getByPlaceholderText('editor.importPastePlaceholder'), {
    target: { value },
  });
}

describe('ImportScriptModal submit chain', () => {
  it('dispatches import with the auto-detected mode, then navigates on completion', async () => {
    svc.importScreenplay.mockResolvedValue({
      success: true,
      script_id: 'new-1',
      task_id: 'task-1',
    });
    const onClose = vi.fn();
    render(<ImportScriptModal projectId="proj-1" onClose={onClose} />);

    fillName('My Script');
    pasteContent('INT. ROOM - DAY\n\nHi.\n\nEXT. STREET - NIGHT\n\nBye.');

    fireEvent.click(screen.getByRole('button', { name: 'editor.importSubmit' }));

    await waitFor(() =>
      expect(svc.importScreenplay).toHaveBeenCalledWith({
        project_id: 'proj-1',
        name: 'My Script',
        mode: 'fountain',
        content: 'INT. ROOM - DAY\n\nHi.\n\nEXT. STREET - NIGHT\n\nBye.',
      }),
    );

    // The dispatched task_id is now being watched.
    await waitFor(() => expect(taskId).toBe('task-1'));

    // Simulate the workflow completing → toast + close + navigate to new script.
    act(() => taskOpts.onComplete?.({ id: 'task-1', status: 'completed' }));
    expect(addToast).toHaveBeenCalledWith('editor.importSuccess', 'success');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith('/team/team-9/projects/proj-1/scripts/new-1');
  });

  it('surfaces a task failure as an error toast and re-enables submit', async () => {
    svc.importScreenplay.mockResolvedValue({
      success: true,
      script_id: 'new-2',
      task_id: 'task-2',
    });
    render(<ImportScriptModal projectId="proj-1" onClose={vi.fn()} />);
    fillName('Doomed');
    pasteContent('Some prose without headings.');
    fireEvent.click(screen.getByRole('button', { name: 'editor.importSubmit' }));

    await waitFor(() => expect(taskId).toBe('task-2'));
    act(() => taskOpts.onError?.({ id: 'task-2', status: 'failed', error_msg: 'boom' }));

    expect(addToast).toHaveBeenCalledWith('boom', 'error');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('keeps the prose hint for prose text and drops it for fountain', () => {
    render(<ImportScriptModal projectId="proj-1" onClose={vi.fn()} />);
    // Default (empty) → prose → hint visible.
    expect(screen.getByText('editor.importProseHint')).toBeInTheDocument();
    pasteContent('INT. A - DAY\n\nx\n\nEXT. B - NIGHT\n\ny');
    expect(screen.queryByText('editor.importProseHint')).toBeNull();
    pasteContent('back to plain prose');
    expect(screen.getByText('editor.importProseHint')).toBeInTheDocument();
  });

  it('lets the user override the auto-detected mode', async () => {
    svc.importScreenplay.mockResolvedValue({ success: true, script_id: 'n', task_id: 't' });
    render(<ImportScriptModal projectId="proj-1" onClose={vi.fn()} />);
    fillName('Override');
    pasteContent('INT. A - DAY\n\nx\n\nEXT. B - NIGHT\n\ny'); // auto → fountain
    fireEvent.click(screen.getByRole('button', { name: 'editor.importModeProse' })); // pin prose
    fireEvent.click(screen.getByRole('button', { name: 'editor.importSubmit' }));
    await waitFor(() =>
      expect(svc.importScreenplay).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'prose' }),
      ),
    );
  });
});

// --------------------------------------------------------------------------- //
// ImportScriptModal — upload guards
// --------------------------------------------------------------------------- //

describe('ImportScriptModal upload guards', () => {
  it('rejects a file over the size cap without dispatching', async () => {
    render(<ImportScriptModal projectId="proj-1" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.importTabUpload' }));

    const big = new File(['x'.repeat(MAX_IMPORT_CHARS + 1)], 'big.fountain', {
      type: 'text/plain',
    });
    fireEvent.change(screen.getByTestId('import-file-input'), { target: { files: [big] } });

    await waitFor(() =>
      expect(screen.getByText('editor.importErrorTooLarge')).toBeInTheDocument(),
    );
    expect(svc.importScreenplay).not.toHaveBeenCalled();
  });

  it('rejects an unsupported file type', async () => {
    render(<ImportScriptModal projectId="proj-1" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.importTabUpload' }));

    const pdf = new File(['data'], 'script.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByTestId('import-file-input'), { target: { files: [pdf] } });

    await waitFor(() =>
      expect(screen.getByText('editor.importErrorType')).toBeInTheDocument(),
    );
    expect(svc.importScreenplay).not.toHaveBeenCalled();
  });
});
