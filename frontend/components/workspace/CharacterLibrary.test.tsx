/**
 * CharacterLibrary — bible card wall (PR-CC4): load, empty-state extract,
 * inline edit persistence, Open in Canvas reuse-or-create with the seeding
 * query params.
 */

import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listCharacters = vi.fn();
const createCharacter = vi.fn();
const updateCharacter = vi.fn();
const extractCharactersFromScript = vi.fn();
vi.mock('../../services/charactersService', () => ({
  listCharacters: (...a: unknown[]) => listCharacters(...a),
  createCharacter: (...a: unknown[]) => createCharacter(...a),
  updateCharacter: (...a: unknown[]) => updateCharacter(...a),
  extractCharactersFromScript: (...a: unknown[]) => extractCharactersFromScript(...a),
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
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

import { CharacterLibrary } from './CharacterLibrary';

const ROW = {
  id: '42',
  project_id: '777',
  name: 'Cole',
  role_tag: 'lead' as const,
  description: 'A retired cavalry officer.',
  tags: { personality: ['stoic'] },
  portrait_url: null,
  source: 'manual' as const,
  sort_order: 0,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => cleanup());

describe('CharacterLibrary', () => {
  it('renders bible cards with name, role, bio and tag chips', async () => {
    listCharacters.mockResolvedValue([ROW]);
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('character-card')).toBeInTheDocument());
    expect((screen.getByLabelText('Character name') as HTMLInputElement).value).toBe('Cole');
    expect(screen.getByText('lead')).toBeInTheDocument();
    expect(screen.getByText('stoic')).toBeInTheDocument();
  });

  it('empty state extracts the cast from scripts', async () => {
    listCharacters.mockResolvedValue([]);
    extractCharactersFromScript.mockResolvedValue([ROW]);
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() =>
      expect(screen.getByTestId('character-library-empty')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /Extract from script/ }));
    await waitFor(() => expect(extractCharactersFromScript).toHaveBeenCalledWith('777'));
    await waitFor(() => expect(screen.getByTestId('character-card')).toBeInTheDocument());
  });

  it('bio edits persist on blur via PATCH', async () => {
    listCharacters.mockResolvedValue([ROW]);
    updateCharacter.mockResolvedValue({ ...ROW, description: 'New bio' });
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByLabelText('Character bio')).toBeInTheDocument());
    const bio = screen.getByLabelText('Character bio');
    fireEvent.change(bio, { target: { value: 'New bio' } });
    fireEvent.blur(bio);
    await waitFor(() =>
      expect(updateCharacter).toHaveBeenCalledWith('777', '42', { description: 'New bio' }),
    );
  });

  it('Open in Canvas reuses an existing character canvas by name', async () => {
    listCharacters.mockResolvedValue([ROW]);
    listCanvases.mockResolvedValue([
      { id: 'c9', kind: 'character', name: 'Cole · Character' },
    ]);
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Open in Canvas/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Open in Canvas/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(createCanvas).not.toHaveBeenCalled();
    const url = navigate.mock.calls[0][0] as string;
    expect(url).toContain('/team/t1/canvas/c9');
    expect(url).toContain('characterId=42');
  });

  it('Open in Canvas creates a kind=character canvas when none exists', async () => {
    listCharacters.mockResolvedValue([ROW]);
    listCanvases.mockResolvedValue([]);
    createCanvas.mockResolvedValue({ id: 'c-new', kind: 'character', name: 'Cole · Character' });
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Open in Canvas/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Open in Canvas/ }));
    await waitFor(() =>
      expect(createCanvas).toHaveBeenCalledWith('777', {
        name: 'Cole · Character',
        kind: 'character',
      }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toContain('/team/t1/canvas/c-new');
  });
});
