// frontend/features/canvas-core/ui/CanvasListPage.test.tsx
// Phase 0 (Infinite-Canvas parity, G11): top-level canvas landing page.
// The VITE_FEATURE_CANVAS_NAV flag is read at module scope, so every test
// stubs the env first and dynamic-imports a fresh module copy.

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
vi.mock('../services/canvasService', () => ({
  listTeamCanvases: (...args: unknown[]) => listTeamCanvases(...args),
  createCanvas: (...args: unknown[]) => createCanvas(...args),
}));

const CANVAS = {
  id: 'c1',
  name: 'Hero Canvas',
  kind: 'smart',
  updated_at: '2026-07-01T00:00:00Z',
};
const TREE = [{ project_id: 'p1', project_name: 'Demo Project', canvases: [CANVAS] }];

async function loadPage() {
  const mod = await import('./CanvasListPage');
  return mod.default;
}

describe('CanvasListPage', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('redirects to the team root when the flag is off', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'false');
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);
    expect(screen.getByTestId('redirect').textContent).toBe('/team/team-1');
  });

  it('lists canvases grouped by project when the flag is on', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue(TREE);
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    await waitFor(() => {
      expect(screen.getByText('Demo Project')).toBeTruthy();
      expect(screen.getByText('Hero Canvas')).toBeTruthy();
    });
    expect(listTeamCanvases).toHaveBeenCalledWith('team-1');
  });

  it('navigates into the editor when a canvas card is clicked', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue(TREE);
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    const card = await screen.findByText('Hero Canvas');
    fireEvent.click(card);
    expect(mockNavigate).toHaveBeenCalledWith('/team/team-1/canvas/c1');
  });

  it('creates a canvas and navigates into it', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue([{ project_id: 'p1', project_name: 'Demo Project', canvases: [] }]);
    createCanvas.mockResolvedValue({ ...CANVAS, id: 'c-new' });
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    const newButton = await screen.findByText('New Canvas');
    fireEvent.click(newButton);

    await waitFor(() => {
      expect(createCanvas).toHaveBeenCalledWith('p1', { name: 'Untitled Canvas' });
      expect(mockNavigate).toHaveBeenCalledWith('/team/team-1/canvas/c-new');
    });
  });

  it('surfaces a toast when canvas creation fails', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue([{ project_id: 'p1', project_name: 'Demo Project', canvases: [] }]);
    createCanvas.mockRejectedValue(new Error('boom'));
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    fireEvent.click(await screen.findByText('New Canvas'));

    await waitFor(() => {
      expect(mockAddToast).toHaveBeenCalledWith('Failed to create canvas', 'error');
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('sorts canvases newest-first within a project group', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    // The endpoint orders canvases newest-first; the page renders as-is.
    listTeamCanvases.mockResolvedValue([
      {
        project_id: 'p1',
        project_name: 'Demo Project',
        canvases: [
          { ...CANVAS, id: 'c-new', name: 'New Board', updated_at: '2026-06-01T00:00:00Z' },
          { ...CANVAS, id: 'c-old', name: 'Old Canvas', updated_at: '2026-01-01T00:00:00Z' },
        ],
      },
    ]);
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    await screen.findByText('Old Canvas');
    const cards = screen.getAllByRole('button').map((el) => el.textContent ?? '');
    const newIndex = cards.findIndex((tx) => tx.includes('New Board'));
    const oldIndex = cards.findIndex((tx) => tx.includes('Old Canvas'));
    expect(newIndex).toBeGreaterThanOrEqual(0);
    expect(newIndex).toBeLessThan(oldIndex);
  });

  it('ignores a second click while a create is already in flight', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue([{ project_id: 'p1', project_name: 'Demo Project', canvases: [] }]);
    let resolveCreate: (value: unknown) => void = () => {};
    createCanvas.mockImplementation(
      () => new Promise((resolve) => { resolveCreate = resolve; }),
    );
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    const newButton = await screen.findByText('New Canvas');
    fireEvent.click(newButton);
    fireEvent.click(newButton);
    expect(createCanvas).toHaveBeenCalledTimes(1);

    resolveCreate({ ...CANVAS, id: 'c-new' });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/team/team-1/canvas/c-new');
    });
  });

  it('shows an empty state when there are no projects', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockResolvedValue([]);
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    await waitFor(() => {
      expect(
        screen.getByText('No projects yet — create a project to start a canvas.'),
      ).toBeTruthy();
    });
  });

  it('shows an error state when loading fails', async () => {
    vi.stubEnv('VITE_FEATURE_CANVAS_NAV', 'true');
    listTeamCanvases.mockRejectedValue(new Error('network down'));
    const CanvasListPage = await loadPage();
    render(<CanvasListPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load canvases')).toBeTruthy();
    });
  });
});
