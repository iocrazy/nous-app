/**
 * LibrarySearchCard — the chat tool card for the agent's LibrarySearch call
 * (vector layers spec §4.5, PR 5). Rows come from the tool result verbatim
 * (backend `library_search_tool`), so the fixtures use the real wire shape:
 * ids are STRINGS, `shot` is null until shot indexing exists.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import type { ChatToolCall } from '../../types/api';
import { LibrarySearchCard } from './LibrarySearchCard';
import { summarizeToolCall, SubTaskList } from './SubTaskCard';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown, arg3?: unknown) => {
      const def = typeof arg2 === 'string' ? arg2 : key;
      const vars = (typeof arg2 === 'object' ? arg2 : arg3) as Record<string, unknown> | undefined;
      if (!vars) return def;
      return def.replace(/\{\{(\w+)\}\}/g, (_, k: string) => String(vars[k]));
    },
  }),
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => navigateMock,
}));

let teamId: string | null = null;
vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ selectedTeamId: teamId }),
}));

// SubTaskCard pulls in supabase for Delegate live state; not under test here.
vi.mock('../../supabaseClient', () => ({ getSupabaseClient: () => null }));

const RESULT = {
  query: 'handheld tracking shot',
  hits: [
    {
      resource_id: '1905000000000000001',
      media_id: '9007199254740993',
      platform_id: 'p1',
      title: 'Night Market Walk',
      description: null,
      layer: 'text',
      score: 1,
      author: 'someone',
      created_at: '2026-09-01T00:00:00Z',
      shot: null,
    },
    {
      resource_id: null,
      media_id: '7',
      platform_id: 'p7',
      title: 'Orphan Hit',
      description: null,
      layer: 'semantic',
      score: 0.6123,
      author: null,
      created_at: null,
      shot: null,
    },
  ],
  legs: { text: 1, semantic: 1 },
  vector_leg: 'ok',
  reranked: false,
  total: 2,
};

function call(result: Record<string, unknown>): ChatToolCall {
  return {
    name: 'LibrarySearch',
    iteration: 1,
    args: { query: 'handheld tracking shot' },
    result,
  };
}

beforeEach(() => {
  navigateMock.mockReset();
  teamId = null;
});

describe('LibrarySearchCard', () => {
  it('renders one row per hit: time code, title, layer chip, score', () => {
    render(<LibrarySearchCard call={call(RESULT)} />);
    const rows = screen.getAllByTestId('library-search-hit');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('—');
    expect(rows[0].textContent).toContain('Night Market Walk');
    expect(rows[0].textContent).toContain('Text');
    expect(rows[0].textContent).toContain('1.00');
    expect(rows[1].textContent).toContain('Semantic');
    expect(rows[1].textContent).toContain('0.61');
  });

  it('opens the detail page with the search hit in router state', () => {
    teamId = '42';
    render(<LibrarySearchCard call={call(RESULT)} />);
    fireEvent.click(screen.getAllByTestId('library-search-hit')[0]);
    expect(navigateMock).toHaveBeenCalledWith('/team/42/resources/file/1905000000000000001', {
      state: { searchHit: { layer: 'text', score: 1 } },
    });
  });

  it('uses the personal path when no team is selected', () => {
    render(<LibrarySearchCard call={call(RESULT)} />);
    fireEvent.click(screen.getAllByTestId('library-search-hit')[0]);
    expect(navigateMock.mock.calls[0][0]).toBe('/resources/file/1905000000000000001');
  });

  it('a hit without a resource row is not clickable', () => {
    render(<LibrarySearchCard call={call(RESULT)} />);
    const orphan = screen.getAllByTestId('library-search-hit')[1];
    expect(orphan).toBeDisabled();
    fireEvent.click(orphan);
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('passes the shot start through when the hit has one', () => {
    const withShot = {
      ...RESULT,
      hits: [{ ...RESULT.hits[0], layer: 'visual', shot: { start_ms: 83000, end_ms: 90000 } }],
    };
    render(<LibrarySearchCard call={call(withShot)} />);
    const row = screen.getByTestId('library-search-hit');
    expect(row.textContent).toContain('1:23');
    fireEvent.click(row);
    expect(navigateMock.mock.calls[0][1]).toEqual({
      state: { searchHit: { layer: 'visual', score: 1, startMs: 83000 } },
    });
  });

  it('shows the legs line and warns when the vector leg did not run', () => {
    render(<LibrarySearchCard call={call({ ...RESULT, vector_leg: 'timeout' })} />);
    expect(screen.getByTestId('library-search-legs').textContent).toContain('Text 1');
    expect(screen.getByTestId('library-search-vector-leg').textContent).toContain('timeout');
  });

  it('no warning when the vector leg is ok', () => {
    render(<LibrarySearchCard call={call(RESULT)} />);
    expect(screen.queryByTestId('library-search-vector-leg')).toBeNull();
  });

  it('an error result is one danger line, no rows', () => {
    render(<LibrarySearchCard call={call({ error: 'library search failed: RuntimeError' })} />);
    const err = screen.getByText('library search failed: RuntimeError');
    expect(err.className).toContain('text-danger');
    expect(screen.queryAllByTestId('library-search-hit')).toHaveLength(0);
  });

  it('an empty result says so', () => {
    render(<LibrarySearchCard call={call({ ...RESULT, hits: [], total: 0 })} />);
    expect(screen.getByText('No matches in your library')).toBeTruthy();
  });
});

describe('SubTaskList routing', () => {
  it('renders LibrarySearch calls as the dedicated card', () => {
    render(<SubTaskList calls={[call(RESULT)]} />);
    expect(screen.getAllByTestId('library-search-hit')).toHaveLength(2);
  });

  it('summarizeToolCall labels LibrarySearch with the query and hit count', () => {
    const s = summarizeToolCall(call(RESULT));
    expect(s.label).toBe('LibrarySearch · handheld tracking shot');
    expect(s.status).toBe('2 hits');
  });
});
