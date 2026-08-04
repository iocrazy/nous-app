/**
 * SkillEditor — the standalone skill page (spec §06).
 *
 * Covers two acceptance findings:
 *   1. "+ File" (create a sub-document inside the skill) must be reachable.
 *      It was hidden outright on built-in skills, which reads as a missing
 *      feature rather than a locked one, and it sat inside the horizontally
 *      scrolling tab strip, so a skill with a few files pushed it off-screen.
 *   2. The Versions panel had two stacked headings — one from the wrapper
 *      section, one from VersionHistoryPanel's own header.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibrarySkill } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>, o?: Record<string, unknown>) => {
      const def = typeof d === 'string' ? d : '';
      const vars = (typeof d === 'string' ? o : d) ?? {};
      return def.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(vars[k] ?? ''));
    },
  }),
}));

const getSkill = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    getSkill: (slug: string) => getSkill(slug),
    listSkills: () => Promise.resolve([]),
    updateSkill: () => Promise.resolve({}),
    upsertSkillFile: () => Promise.resolve({}),
    deleteSkill: () => Promise.resolve({}),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ userProfile: { role: 'user' } }),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('./MarkdownBody', () => ({ MarkdownBody: () => <div>body</div> }));
vi.mock('./MarkdownEditor', () => ({ MarkdownEditor: () => <div>editor</div> }));
vi.mock('./NewSkillModal', () => ({ NewSkillModal: () => <div>fork modal</div> }));

// Renders its own header, exactly like the real panel does — that is the
// whole point of test 2 below.
vi.mock('./VersionHistoryPanel', () => ({
  VersionHistoryPanel: () => (
    <div data-testid="version-panel">
      <h2>Versions</h2>
    </div>
  ),
}));

import { SkillEditor } from './SkillEditor';

const skill = (over: Partial<AILibrarySkill> = {}): AILibrarySkill =>
  ({
    id: 1,
    slug: 'script-outline',
    name: 'Script Outline',
    is_public: false,
    team_id: null,
    project_id: null,
    body_md: '# hi',
    frontmatter_json: {},
    files: [],
    agents: [],
    updated_at: '',
    ...over,
  }) as AILibrarySkill;

async function renderEditor(s: AILibrarySkill, onNewFile = vi.fn()) {
  getSkill.mockResolvedValue(s);
  render(
    <MemoryRouter>
      <SkillEditor slug={s.slug!} onBack={vi.fn()} onNewFile={onNewFile} />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText('Script Outline')).toBeTruthy());
  return onNewFile;
}

describe('SkillEditor "+ File"', () => {
  beforeEach(() => getSkill.mockReset());

  it('creates a sub-document on a self-authored skill', async () => {
    const onNewFile = await renderEditor(skill());
    const btn = screen.getByTestId('skill-new-file');
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(btn);
    expect(onNewFile).toHaveBeenCalled();
  });

  it('shows the button disabled with a reason on a built-in skill', async () => {
    // Hiding it made a locked feature look like a missing one — the user
    // reported "+ File" as absent from the skill editor entirely.
    await renderEditor(skill({ is_public: true }));
    const btn = screen.getByTestId('skill-new-file') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toContain('Fork');
  });

  it('keeps the button outside the scrolling tab strip', async () => {
    // Inside the overflow-x-auto strip it scrolled out of reach as soon as a
    // skill had more than a couple of files.
    await renderEditor(
      skill({
        files: [
          { path: 'references/a.md', file_type: 'markdown', content: '' },
          { path: 'references/b.md', file_type: 'markdown', content: '' },
        ] as AILibrarySkill['files'],
      }),
    );
    const strip = screen.getByTestId('skill-file-tabs');
    expect(strip.contains(screen.getByTestId('skill-new-file'))).toBe(false);
  });
});

describe('SkillEditor versions panel', () => {
  beforeEach(() => getSkill.mockReset());

  it('renders exactly one Versions heading', async () => {
    await renderEditor(skill());
    expect(screen.getAllByRole('heading', { name: 'Versions' })).toHaveLength(1);
    expect(screen.getByTestId('version-panel')).toBeTruthy();
  });
});
