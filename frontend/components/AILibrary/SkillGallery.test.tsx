/**
 * SkillGallery (B3) — the skill library as cards.
 *   1. A card names the agents using the skill.
 *   2. A skill nobody uses says so, in warn colour, with what to do.
 *   3. Clicking a card opens it.
 *   4. Provenance badge distinguishes built-in from self-authored.
 *   5. Search narrows the grid.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

import { SkillGallery } from './SkillGallery';

const skill = (over: Partial<AILibrarySkill>): AILibrarySkill =>
  ({
    id: 1,
    name: 'Script Outline',
    slug: 'script-outline',
    is_public: true,
    frontmatter_json: {},
    files: [],
    updated_at: '',
    ...over,
  }) as AILibrarySkill;

describe('SkillGallery', () => {
  it('names the agents using a skill', () => {
    render(
      <SkillGallery
        skills={[
          skill({
            agents: [
              { slug: 'script_ai', name: 'Script AI' },
              { slug: 'storyboard', name: 'Storyboard' },
            ],
          }),
        ]}
        onOpen={() => {}}
      />,
    );
    const card = screen.getByTestId('skill-card');
    expect(card.textContent).toContain('2 agents using');
    expect(card.textContent).toContain('Script AI');
  });

  it('warns about a skill no agent uses', () => {
    // An orphan skill is dead weight in every prompt budget conversation;
    // the whole point of the reverse index is making it visible at a glance.
    render(<SkillGallery skills={[skill({ agents: [] })]} onOpen={() => {}} />);
    expect(screen.getByTestId('orphan-hint').textContent).toContain(
      'No agent uses this',
    );
  });

  it('treats a missing agents field the same as an empty one', () => {
    // Older backends (and any endpoint that forgets to populate it) send
    // undefined; silently rendering "0 agents using" without the warning
    // would hide exactly the case we care about.
    render(<SkillGallery skills={[skill({})]} onOpen={() => {}} />);
    expect(screen.getByTestId('orphan-hint')).toBeTruthy();
  });

  it('opens the skill when its card is clicked', () => {
    const onOpen = vi.fn();
    render(<SkillGallery skills={[skill({ agents: [] })]} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('skill-card'));
    expect(onOpen).toHaveBeenCalledWith('script-outline');
  });

  it('distinguishes built-in from self-authored skills', () => {
    render(
      <SkillGallery
        skills={[
          skill({ id: 1, slug: 'builtin', is_public: true }),
          skill({ id: 2, slug: 'mine', name: 'Mine', is_public: false }),
        ]}
        onOpen={() => {}}
      />,
    );
    const cards = screen.getAllByTestId('skill-card');
    expect(cards[0].textContent).toContain('Built-in');
    expect(cards[1].textContent).toContain('Custom');
  });

  it('filters by search text', () => {
    render(
      <SkillGallery
        skills={[
          skill({ id: 1, slug: 'script-outline', name: 'Script Outline' }),
          skill({ id: 2, slug: 'translate-copy', name: 'Translate Copy' }),
        ]}
        onOpen={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText('Search skills...'), {
      target: { value: 'transl' },
    });
    expect(screen.getAllByTestId('skill-card')).toHaveLength(1);
  });
});
