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
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

const mockService = vi.hoisted(() => ({
  listCanvases: vi.fn(),
  createCanvas: vi.fn(),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => mockService);

const CANVASES = [
  { id: 'c1', name: 'Hero Canvas', kind: 'smart', updated_at: '2026-07-01T00:00:00Z' },
  { id: 'c2', name: 'Board', kind: 'classic', updated_at: '2026-07-02T00:00:00Z' },
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

  it('falls back to the bare canvas route without a teamId', async () => {
    mockService.listCanvases.mockResolvedValue(CANVASES);
    render(<WorkspaceCanvas projectId="p1" />);
    await screen.findByText('Hero Canvas');
    fireEvent.click(screen.getByText('Hero Canvas'));
    expect(mockNavigate).toHaveBeenCalledWith('/canvas/c1');
  });

  it('New Canvas creates and drops into the editor', async () => {
    mockService.listCanvases.mockResolvedValue([]);
    mockService.createCanvas.mockResolvedValue({ id: 'c9' });
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByRole('button', { name: /New Canvas/ });

    fireEvent.click(screen.getByRole('button', { name: /New Canvas/ }));
    await waitFor(() => {
      expect(mockService.createCanvas).toHaveBeenCalledWith('p1', expect.anything());
      expect(mockNavigate).toHaveBeenCalledWith('/team/t1/canvas/c9');
    });
  });

  it('shows the empty state when the project has no canvases', async () => {
    mockService.listCanvases.mockResolvedValue([]);
    render(<WorkspaceCanvas projectId="p1" teamId="t1" />);
    await screen.findByTestId('workspace-canvas-empty');
  });
});
