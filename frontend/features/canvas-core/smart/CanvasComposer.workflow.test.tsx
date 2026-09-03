// features/canvas-core/smart/CanvasComposer.workflow.test.tsx
// Composer Export/Import buttons (G5): Export downloads the selected
// subgraph as JSON; Import appends a parsed file with fresh ids and
// selects the inserted nodes; kind mismatch and junk files surface errors.

import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
// CanvasComposer consumes router state for the Send to Canvas hand-off
// (Phase 2 Task 4) — none of these tests exercise that path, so a static
// no-state location keeps the rest of the suite unaffected.
vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: '/test', state: null }),
  useNavigate: () => vi.fn(),
}));
vi.mock('./workflowLibrary', () => ({
  saveWorkflowToLibrary: vi.fn(),
  fetchWorkflowText: vi.fn(),
}));
vi.mock('../../../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({
    data: { results: [{ id: '88', name: 'wf-a.json', kind: 'doc' }] },
    loading: false,
  }),
}));

import { CanvasComposer } from './CanvasComposer';
import { WORKFLOW_FORMAT } from './workflowIO';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

function seed(selection: string[] = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [
      { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'x' } },
    ] as CanvasNode[],
    connections: [],
    selection,
  });
}

function importFile(contents: string): void {
  const input = screen.getByTestId('workflow-import-input') as HTMLInputElement;
  const file = new File([contents], 'wf.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => contents });
  fireEvent.change(input, { target: { files: [file] } });
}

describe('CanvasComposer — workflow export/import', () => {
  it('Export is disabled without a selection and downloads JSON with one', () => {
    seed(['p1']);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:wf');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    render(<CanvasComposer />);

    fireEvent.click(screen.getByRole('button', { name: 'Export' }));
    expect(createSpy).toHaveBeenCalledTimes(1);
    const blob = createSpy.mock.calls[0][0] as Blob;
    expect(blob.type).toContain('application/json');
  });

  it('Export with empty selection is disabled', () => {
    seed([]);
    render(<CanvasComposer />);
    expect(
      (screen.getByRole('button', { name: 'Export' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('Import appends re-id nodes and selects them', async () => {
    seed();
    render(<CanvasComposer />);
    importFile(
      JSON.stringify({
        format: WORKFLOW_FORMAT,
        version: 1,
        kind: 'smart',
        nodes: [
          { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'imported' } },
          { id: 'out1', type: 'output', position: { x: 320, y: 0 }, data: { kind: 'image' } },
        ],
        connections: [
          { id: 'e1', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
        ],
      }),
    );

    await waitFor(() => {
      const s = useCanvasCoreStore.getState();
      expect(s.nodes).toHaveLength(3);
      // Imported prompt was re-id'd (p1 already exists).
      const ids = s.nodes.map((n) => (n as { id: string }).id);
      expect(new Set(ids).size).toBe(3);
      expect(s.connections).toHaveLength(1);
      expect(s.selection).toHaveLength(2);
    });
  });

  it('Import of a mismatched kind surfaces an error and adds nothing', async () => {
    seed();
    render(<CanvasComposer />);
    importFile(
      JSON.stringify({
        format: WORKFLOW_FORMAT,
        version: 1,
        kind: 'character',
        nodes: [{ id: 'n1', position: { x: 0, y: 0 } }],
        connections: [],
      }),
    );
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/kind|canvas/i);
    });
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('Import of junk surfaces a parse error', async () => {
    seed();
    render(<CanvasComposer />);
    importFile('definitely not json');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
  });
});

describe('CanvasComposer — library glue (②-4)', () => {
  it('Save uploads the selected subgraph into the team scope', async () => {
    const { saveWorkflowToLibrary } = await import('./workflowLibrary');
    (saveWorkflowToLibrary as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: '77',
      filename: 'workflow-1nodes.json',
    });
    seed(['p1']);
    render(<CanvasComposer teamId="team-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(saveWorkflowToLibrary).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'smart' }),
        'team-1',
      );
      expect(screen.getByRole('status').textContent).toMatch(/Saved as workflow/);
    });
  });

  it('Library pick fetches the file and imports it', async () => {
    const { fetchWorkflowText } = await import('./workflowLibrary');
    (fetchWorkflowText as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      JSON.stringify({
        format: WORKFLOW_FORMAT,
        version: 1,
        kind: 'smart',
        nodes: [{ id: 'n1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'lib' } }],
        connections: [],
      }),
    );
    seed([]);
    render(<CanvasComposer teamId="team-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Workflows' }));
    fireEvent.click(await screen.findByText('wf-a.json'));
    await waitFor(() => {
      expect(fetchWorkflowText).toHaveBeenCalledWith('88');
      expect(useCanvasCoreStore.getState().nodes).toHaveLength(2);
    });
  });

  it('no teamId → no library buttons', () => {
    seed([]);
    render(<CanvasComposer />);
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Workflows' })).toBeNull();
  });
});
