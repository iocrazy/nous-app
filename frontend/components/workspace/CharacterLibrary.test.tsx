/**
 * CharacterLibrary — bible card wall (PR-CC4): load, empty-state extract,
 * inline edit persistence, Open in Canvas reuse-or-create with the seeding
 * query params. Plus the script-cast import hint (feat/characters-import-hint):
 * the diff bar shown when scripts name characters the library has no card for.
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

const fetchProjectEntities = vi.fn();
vi.mock('../../services/projectsService', () => ({
  fetchProjectEntities: (...a: unknown[]) => fetchProjectEntities(...a),
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
// `t` mirrors the i18next call shapes the component uses: (key, defaultString)
// and (key, {count, defaultValue_one/_other}) — plural selection + {{count}}
// interpolation, so the hint's wording and number are assertable without
// loading the real locale bundles.
type TOpts = {
  count?: number;
  defaultValue?: string;
  defaultValue_one?: string;
  defaultValue_other?: string;
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, second?: string | TOpts) => {
      if (typeof second === 'string' || second === undefined) return second ?? key;
      const plural = second.count === 1 ? second.defaultValue_one : second.defaultValue_other;
      const text = plural ?? second.defaultValue ?? key;
      return second.count === undefined
        ? text
        : text.replace(/\{\{count\}\}/g, String(second.count));
    },
  }),
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

/** Script-derived cast row shape from GET /projects/{id}/entities. */
const cast = (...names: string[]) => ({
  characters: names.map((name) => ({ name, cue_count: 1, episode_ids: ['e1'] })),
  locations: [],
});

beforeEach(() => {
  vi.clearAllMocks();
  // Default: scripts name exactly the library's only character, so no hint —
  // the pre-existing suites assert behavior without the diff bar in the way.
  fetchProjectEntities.mockResolvedValue(cast('Cole'));
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

describe('CharacterLibrary script-cast import hint', () => {
  it('shows the hint with the count of script characters missing from the library', async () => {
    listCharacters.mockResolvedValue([ROW]);
    fetchProjectEntities.mockResolvedValue(cast('Cole', 'Ada', 'Bram'));
    render(<CharacterLibrary projectId="777" />);
    const hint = await screen.findByTestId('character-import-hint');
    expect(hint).toHaveTextContent(
      '2 characters in your scripts are not in this library yet',
    );
    // The missing names are named, so the user can tell what would land.
    expect(hint).toHaveTextContent('Ada');
    expect(hint).toHaveTextContent('Bram');
    expect(hint).not.toHaveTextContent('Cole');
  });

  it('stays hidden when every script character already has a card', async () => {
    listCharacters.mockResolvedValue([ROW]);
    fetchProjectEntities.mockResolvedValue(cast('Cole'));
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('character-card')).toBeInTheDocument());
    await waitFor(() => expect(fetchProjectEntities).toHaveBeenCalledWith('777'));
    expect(screen.queryByTestId('character-import-hint')).not.toBeInTheDocument();
  });

  it('treats trim / case / fullwidth variants as already imported', async () => {
    listCharacters.mockResolvedValue([
      { ...ROW, name: 'cole' },
      { ...ROW, id: '43', name: '  Ada Byron ' },
    ]);
    // '  COLE ' matches 'cole' (trim+case); 'Ａda　Byron' matches after
    // fullwidth→halfwidth + whitespace collapse. Only Bram is genuinely new.
    fetchProjectEntities.mockResolvedValue(cast('  COLE ', 'Ａda　Byron', 'Bram'));
    render(<CharacterLibrary projectId="777" />);
    const hint = await screen.findByTestId('character-import-hint');
    expect(hint).toHaveTextContent(
      '1 character in your scripts is not in this library yet',
    );
    expect(hint).toHaveTextContent('Bram');
  });

  it('stays hidden when the script cast fetch fails (no noise on error)', async () => {
    listCharacters.mockResolvedValue([ROW]);
    fetchProjectEntities.mockRejectedValue(new Error('boom'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<CharacterLibrary projectId="777" />);
    await waitFor(() => expect(screen.getByTestId('character-card')).toBeInTheDocument());
    expect(screen.queryByTestId('character-import-hint')).not.toBeInTheDocument();
  });

  it('one-click import calls extract and echoes how many rows landed', async () => {
    listCharacters.mockResolvedValue([ROW]);
    fetchProjectEntities.mockResolvedValue(cast('Cole', 'Ada', 'Bram'));
    extractCharactersFromScript.mockResolvedValue([
      ROW,
      { ...ROW, id: '43', name: 'Ada', source: 'script' as const },
      { ...ROW, id: '44', name: 'Bram', source: 'script' as const },
    ]);
    render(<CharacterLibrary projectId="777" />);
    fireEvent.click(await screen.findByRole('button', { name: /Import them/ }));
    await waitFor(() => expect(extractCharactersFromScript).toHaveBeenCalledWith('777'));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'Imported 2 characters from your scripts',
        'success',
      ),
    );
    // The user's curated row survives — extract only adds what was missing.
    await waitFor(() => expect(screen.getAllByTestId('character-card')).toHaveLength(3));
    expect(screen.queryByTestId('character-import-hint')).not.toBeInTheDocument();
  });

  it('one-click import surfaces a typed failure toast', async () => {
    listCharacters.mockResolvedValue([ROW]);
    fetchProjectEntities.mockResolvedValue(cast('Cole', 'Ada'));
    extractCharactersFromScript.mockRejectedValue(new Error('500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<CharacterLibrary projectId="777" />);
    fireEvent.click(await screen.findByRole('button', { name: /Import them/ }));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Failed to extract characters', 'error'),
    );
    // Failed import must not wipe what is on screen.
    expect(screen.getByTestId('character-card')).toBeInTheDocument();
  });
});
