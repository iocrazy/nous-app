/**
 * WorkspaceCanvas — the workspace's Canvas module (replaces the last
 * "coming soon" placeholder; glue between the projects-workspace epic and
 * the Infinite-Canvas parity epic).
 *
 * Pins: cards render from listCanvases(projectId); clicking one navigates
 * into the canvas editor (team-scoped when teamId is present, bare
 * /canvas/:id otherwise); New Canvas creates then navigates; empty state.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';

import { WorkspaceCanvas } from './WorkspaceCanvas';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  // CanvasCardMenu reads team/project scope for its create-issue payload.
  useParams: () => ({}),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));
// relativeTime pulls in the real i18n module chain (HTTP backend init) —
// stub it so the test env stays hermetic.
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '3d ago',
}));

const mockService = vi.hoisted(() => ({
  listCanvases: vi.fn(),
  createCanvas: vi.fn(),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => mockService);

const CANVASES = [
  { id: 'c1', name: 'Hero Canvas', kind: 'smart', updated_at: '2026-07-01T00:00:00Z' },
  { id: 'c2', name: 'Board', kind: 'character', updated_at: '2026-07-02T00:00:00Z' },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkspaceCanvas', () => {
  it('lists the project canvases and opens the editor team-scoped', async () => {
    mockService.listCanvases.mockResolvedValue(CANVASES);
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByText('Hero Canvas');
    expect(mockService.listCanvases).toHaveBeenCalledWith('p1');

    fireEvent.click(screen.getByText('Hero Canvas'));
    expect(mockNavigate).toHaveBeenCalledWith('/team/t1/canvas/c1');
  });

  it('each card carries its own kind, so a caller can pick by kind not by position', async () => {
    // The list is unfiltered and ordered newest-edited first
    // (`canvas_repository.list_for_project`), so it mixes kinds — the
    // episode's system storyboard canvas included, and that one sorts first
    // here. `e2e-prod/walkthrough.spec.ts` picks a smart-family card out of
    // this list by `data-canvas-kind`; taking whichever card sorted first
    // would open a canvas the Library panel does not mount on and turn a
    // healthy build red.
    mockService.listCanvases.mockResolvedValue([
      { id: 'c0', name: 'Episode Board', kind: 'storyboard', updated_at: '2026-07-03T00:00:00Z' },
      ...CANVASES,
    ]);
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByText('Hero Canvas');

    // Each card's own kind, in list order — not merely "the attribute exists".
    const cards = screen.getAllByTestId('workspace-canvas-card');
    expect(cards.map((el) => el.getAttribute('data-canvas-kind'))).toEqual([
      'storyboard',
      'smart',
      'character',
    ]);
    // And the kind-filtered selector shape the walkthrough uses skips the
    // storyboard board while keeping both smart-family ones.
    expect(
      document.querySelectorAll(
        '[data-testid="workspace-canvas-card"][data-canvas-kind="smart"], ' +
          '[data-testid="workspace-canvas-card"][data-canvas-kind="character"]',
      ).length,
    ).toBe(2);
  });

  it('falls back to the bare canvas route without a teamId', async () => {
    mockService.listCanvases.mockResolvedValue(CANVASES);
    render(<WorkspaceCanvas projectId="p1" />);
    await screen.findByText('Hero Canvas');
    fireEvent.click(screen.getByText('Hero Canvas'));
    expect(mockNavigate).toHaveBeenCalledWith('/canvas/c1');
  });

  it('New Canvas opens the name dialog, then creates and drops into the editor', async () => {
    mockService.listCanvases.mockResolvedValue([]);
    mockService.createCanvas.mockResolvedValue({ id: 'c9' });
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByRole('button', { name: /New Canvas/ });

    // The tile opens the IC-style dialog; Create submits name + kind
    // (Standard = kind 'smart' is the default toggle option).
    fireEvent.click(screen.getByRole('button', { name: /New Canvas/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => {
      expect(mockService.createCanvas).toHaveBeenCalledWith('p1', {
        name: 'Untitled Canvas',
        kind: 'smart',
      });
      expect(mockNavigate).toHaveBeenCalledWith('/team/t1/canvas/c9');
    });
  });

  it('picking the Smart option creates a lite canvas', async () => {
    mockService.listCanvases.mockResolvedValue([]);
    mockService.createCanvas.mockResolvedValue({ id: 'c10' });
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByRole('button', { name: /New Canvas/ });

    fireEvent.click(screen.getByRole('button', { name: /New Canvas/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Smart' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => {
      expect(mockService.createCanvas).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ kind: 'lite' }),
      );
    });
  });

  it('shows the empty state when the project has no canvases', async () => {
    mockService.listCanvases.mockResolvedValue([]);
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByTestId('workspace-canvas-empty');
  });
});
