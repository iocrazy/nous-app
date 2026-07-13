/**
 * EntityLibrary — location/prop bible cards (SP3): load, extract gating
 * (locations only), edit persistence, Open in Canvas reuse-or-create.
 */

import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listLibEntities = vi.fn();
const createLibEntity = vi.fn();
const updateLibEntity = vi.fn();
const extractLibEntitiesFromScript = vi.fn();
vi.mock('../../services/libEntitiesService', () => ({
  listLibEntities: (...a: unknown[]) => listLibEntities(...a),
  createLibEntity: (...a: unknown[]) => createLibEntity(...a),
  updateLibEntity: (...a: unknown[]) => updateLibEntity(...a),
  extractLibEntitiesFromScript: (...a: unknown[]) => extractLibEntitiesFromScript(...a),
}));

const listCanvases = vi.fn();
const createCanvas = vi.fn();
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: (...a: unknown[]) => listCanvases(...a),
  createCanvas: (...a: unknown[]) => createCanvas(...a),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ teamId: 't1' }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { EntityLibrary } from './EntityLibrary';

const ROW = {
  id: '9',
  project_id: '777',
  entity_type: 'location' as const,
  name: 'Radio Booth',
  badge_tag: 'interior',
  description: 'Cramped, smoke-stained.',
  tags: { mood: ['noir'] },
  cover_url: null,
  source: 'manual' as const,
  sort_order: 0,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe('EntityLibrary', () => {
  it('renders cards with name, badge and tag chips', async () => {
    listLibEntities.mockResolvedValue([ROW]);
    render(<EntityLibrary entityType="location" projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('entity-card')).toBeInTheDocument());
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Radio Booth');
    expect(screen.getByText('interior')).toBeInTheDocument();
    expect(screen.getByText('noir')).toBeInTheDocument();
  });

  it('locations empty state offers Extract; props do not', async () => {
    listLibEntities.mockResolvedValue([]);
    const { unmount } = render(<EntityLibrary entityType="location" projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('entity-library-empty')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Extract from script/ })).toBeInTheDocument();
    unmount();

    render(<EntityLibrary entityType="prop" projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('entity-library-empty')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Extract from script/ })).toBeNull();
  });

  it('description edits persist on blur', async () => {
    listLibEntities.mockResolvedValue([ROW]);
    updateLibEntity.mockResolvedValue({ ...ROW, description: 'New' });
    render(<EntityLibrary entityType="location" projectId="777" />);
    await waitFor(() => expect(screen.getByLabelText('Description')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'New' } });
    fireEvent.blur(screen.getByLabelText('Description'));
    await waitFor(() =>
      expect(updateLibEntity).toHaveBeenCalledWith('777', 'location', '9', {
        description: 'New',
      }),
    );
  });

  it('Open in Canvas creates a kind-matched canvas and seeds via query', async () => {
    listLibEntities.mockResolvedValue([{ ...ROW, entity_type: 'prop' as const, name: 'Revolver' }]);
    listCanvases.mockResolvedValue([]);
    createCanvas.mockResolvedValue({ id: 'c-new', kind: 'prop', name: 'Revolver · Prop' });
    render(<EntityLibrary entityType="prop" projectId="777" />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Open in Canvas/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /Open in Canvas/ }));
    await waitFor(() =>
      expect(createCanvas).toHaveBeenCalledWith('777', {
        name: 'Revolver · Prop',
        kind: 'prop',
      }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    const url = navigate.mock.calls[0][0] as string;
    expect(url).toContain('/team/t1/canvas/c-new');
    expect(url).toContain('entityId=9');
  });
});
