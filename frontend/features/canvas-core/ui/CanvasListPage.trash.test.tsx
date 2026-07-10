// frontend/features/canvas-core/ui/CanvasListPage.trash.test.tsx
// Canvas trash (Infinite parity G9): a card's Delete moves it to the trash;
// the Trash section lazy-loads on expand, restores back into the live list,
// and Delete Forever needs a second confirming click.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mockNavigate = vi.fn();
const mockAddToast = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ teamId: 'team-1' }),
  Navigate: ({ to }: { to: string }) => <div data-testid="redirect">{to}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));
vi.mock('../../../components/Toast', () => ({
  useToast: () => ({ addToast: mockAddToast }),
}));

const listTeamCanvases = vi.fn();
const createCanvas = vi.fn();
const deleteCanvas = vi.fn();
const listTeamCanvasTrash = vi.fn();
const restoreCanvas = vi.fn();
const purgeCanvas = vi.fn();
vi.mock('../services/canvasService', () => ({
  listTeamCanvases: (...args: unknown[]) => listTeamCanvases(...args),
  createCanvas: (...args: unknown[]) => createCanvas(...args),
  deleteCanvas: (...args: unknown[]) => deleteCanvas(...args),
  listTeamCanvasTrash: (...args: unknown[]) => listTeamCanvasTrash(...args),
  restoreCanvas: (...args: unknown[]) => restoreCanvas(...args),
  purgeCanvas: (...args: unknown[]) => purgeCanvas(...args),
}));

const CANVAS = {
  id: 'c1',
  name: 'Hero Canvas',
  kind: 'smart',
  updated_at: '2026-07-01T00:00:00Z',
};
const TREE = [{ project_id: 'p1', project_name: 'Demo Project', canvases: [CANVAS] }];
const TRASHED = [
  {
    id: 'c9',
    name: 'Old Canvas',
    kind: 'smart',
    updated_at: '2026-07-01T00:00:00Z',
    deleted_at: '2026-07-02T00:00:00Z',
    project_id: 'p1',
    project_name: 'Demo Project',
  },
];

async function renderPage() {
  vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
  const mod = await import('./CanvasListPage');
  const CanvasListPage = mod.default;
  render(<CanvasListPage />);
  await screen.findByText('Hero Canvas');
}

describe('CanvasListPage — trash (G9)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('card Delete soft-deletes and removes it from the live list', async () => {
    listTeamCanvases.mockResolvedValue(TREE);
    deleteCanvas.mockResolvedValue(undefined);
    await renderPage();

    fireEvent.click(screen.getByRole('button', { name: 'Move to trash' }));
    await waitFor(() => {
      expect(deleteCanvas).toHaveBeenCalledWith('c1');
      expect(screen.queryByText('Hero Canvas')).toBeNull();
    });
  });

  it('Trash section lazy-loads on expand and lists trashed canvases', async () => {
    listTeamCanvases.mockResolvedValue(TREE);
    listTeamCanvasTrash.mockResolvedValue(TRASHED);
    await renderPage();

    expect(listTeamCanvasTrash).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Trash/ }));
    await screen.findByText('Old Canvas');
    expect(listTeamCanvasTrash).toHaveBeenCalledWith('team-1');
  });

  it('Restore returns the canvas to the live list', async () => {
    listTeamCanvases.mockResolvedValue(TREE);
    listTeamCanvasTrash.mockResolvedValue(TRASHED);
    restoreCanvas.mockResolvedValue(undefined);
    await renderPage();

    fireEvent.click(screen.getByRole('button', { name: /Trash/ }));
    await screen.findByText('Old Canvas');
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));

    await waitFor(() => {
      expect(restoreCanvas).toHaveBeenCalledWith('c9');
      // Live tree refetched after restore.
      expect(listTeamCanvases).toHaveBeenCalledTimes(2);
      expect(screen.queryByText('Old Canvas')).toBeNull();
    });
  });

  it('Delete Forever arms on first click and purges on the second', async () => {
    listTeamCanvases.mockResolvedValue(TREE);
    listTeamCanvasTrash.mockResolvedValue(TRASHED);
    purgeCanvas.mockResolvedValue(undefined);
    await renderPage();

    fireEvent.click(screen.getByRole('button', { name: /Trash/ }));
    await screen.findByText('Old Canvas');

    fireEvent.click(screen.getByRole('button', { name: 'Delete Forever' }));
    expect(purgeCanvas).not.toHaveBeenCalled(); // armed, not fired

    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => {
      expect(purgeCanvas).toHaveBeenCalledWith('c9');
      expect(screen.queryByText('Old Canvas')).toBeNull();
    });
  });
});
